#!/usr/bin/env python3
"""Bounded selector/reasoner pilot. Uses public CodeMesh CLI on seeded repairs.

This is a new, single-session protocol, not a continuation of the earlier
three-phase ablation. --run is required for model calls. All roles are counted.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import random
import re
import subprocess
import time
import uuid

import vnext_agent as old
from branch_state import SelectedWorkingState
from codex_runner import catalog_fingerprint, model_call
from decisions_selector import CodexDecisionSelector
from working_state import WorkingState

POLICIES = ("full", "working-set", "selected")


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def action_key(action):
    return json.dumps(action, sort_keys=True)


def source_object(observation):
    try:
        result = json.loads(observation)
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def observed_path(value):
    if not isinstance(value, str) or not value or len(value.encode()) > 512 or "\\" in value or "\0" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) == ".":
        return None
    return str(path)


def source_recovery_action(result):
    """Use stale indexed coordinates only to propose a bounded live file read."""
    path = observed_path(result.get("path") or result.get("filePath"))
    if not path:
        return None
    lines = result.get("lines", [result.get("lineStart"), result.get("lineEnd")])
    if (isinstance(lines, list) and len(lines) == 2 and all(type(line) is int for line in lines)
            and 1 <= lines[0] <= lines[1]):
        start = max(1, lines[0] - 8)
        limit = min(160, lines[1] - start + 9)
    else:
        start, limit = 1, 60
    return {"action": "read", "path": path, "start": start, "limit": limit}


def valid_source_view(result):
    source = result.get("source")
    source_hash = result.get("hash") or result.get("sourceHash")
    return (result.get("status") == "fresh" and isinstance(source, str) and bool(source)
            and sha256(source.encode()).hexdigest() == source_hash)


def complete_source_view(result):
    return (valid_source_view(result) and result.get("mode") in ("auto", "full", "preview")
            and not result.get("omittedRanges"))


class ActionCandidates:
    """Concrete observed targets only; no mutation or source-fact generation."""
    def __init__(self):
        self.pending = {}
        self.completed = set()

    def add(self, action, label):
        key = action_key(action)
        if key not in self.completed:
            self.pending.pop(key, None)
            self.pending[key] = {"action": action, "label": label[:600]}

    def observe(self, action, observation, error=None):
        if error and action.get("action") != "source":
            return
        key = action_key(action)
        result = source_object(observation)
        if action.get("action") == "source" and (error or not valid_source_view(result)):
            # A stale/null response did not complete a read. Prefer a concrete
            # filesystem recovery over repeatedly asking the unchanged index.
            self.completed.discard(key)
            self.pending.pop(key, None)
            recovery = source_recovery_action(result)
            if recovery:
                self.completed.discard(action_key(recovery))
                self.add(recovery, "Recover current file text near the indexed span; stale line numbers are navigation only. "
                                   "Verify the declaration in the live read before using its code.")
            return
        self.completed.add(key)
        self.pending.pop(key, None)
        entries = result.get("entries", result.get("results", []))
        if isinstance(entries, list):
            # New frontier first, original ordering preserved by reversing here.
            for entry in reversed(entries):
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    continue
                node = entry["id"]
                label = f"{entry.get('name', node)} {entry.get('signature', '')}"
                if node.startswith("symbol:"):
                    self.add({"action": "map", "node": node, "relation": "contains"}, "Members of " + label)
                    self.add({"action": "source", "node": node, "mode": "auto"}, "Read " + label)
                elif node == "repo" or node.startswith(("dir:", "file:")):
                    self.add({"action": "map", "node": node}, "Expand " + label)
        if action.get("action") == "map" and result.get("nextCursor"):
            self.add({**action, "cursor": result["nextCursor"]}, "Next page of current map")
        if action.get("action") == "source" and valid_source_view(result):
            node = result.get("id")
            if isinstance(node, str):
                if not complete_source_view(result):
                    self.add({"action": "source", "node": node, "mode": "full"},
                             "Read the complete symbol; the current view omits source or is a summary")
                for relation in ("calls", "called_by"):
                    self.add({"action": "map", "node": node, "relation": relation}, f"Explore {relation} of {node}")

    def choices(self):
        entries = list(reversed(list(self.pending.values())))[:19]
        return [{"value": f"c{i}", **item} for i, item in enumerate(entries)] + [
            {"value": "root", "label": "Return to the main repository index to explore a different branch",
             "action": {"action": "map", "node": "repo"}}]


def prompt(task):
    return (f"Repair this regression: {task['symptom']}\n"
            f"Preserve related behavior: {task['impact_question']}\n"
            "Read live source before editing. Apply a minimal source fix, run the visible test, "
            "then explain the change. Hidden acceptance tests are unavailable.")


def reasoner_schema(policy):
    selected = policy == "selected"
    data = json.loads(old.schema("codemesh", "working-set" if policy != "full" else "full"))
    if selected:
        data["properties"]["action"]["enum"] = ["request_context", "edit", "test", "answer"]
    else:
        data["properties"]["action"]["enum"] = ["map", "search", "source", "grep", "read", "edit", "test", "answer"]
        if policy == "working-set":
            data["properties"]["action"]["enum"].append("restore")
    return data


def reasoner_system(policy):
    selected = policy == "selected"
    common = ("Repair the reported regression using one JSON action per response. "
              "The harness executes actions; do not use native tools. Leave irrelevant fields null. "
              "Use source evidence before editing; signatures and model notes are insufficient. "
              "edit(path,old,new) replaces a unique exact source string in pydantic/*.py. "
              "test runs the visible test, at most three times; hidden tests are unavailable. "
              "answer finishes after an edit and visible test. Preserve the task constraints. ")
    if selected:
        common += (" A separate selector explores the graph and selects navigation evidence. "
                   "You reason and edit using the original source bytes supplied. "
                   "If context is insufficient, use request_context with a concrete query, exact node, "
                   "or path/start/limit; explain the missing evidence in answer. The selector will continue. "
                   "Retained evidence with status=fresh is original source already read live in this session, "
                   "valid until an edit invalidates it. An observation archive handle does not make this source historical. "
                   "Use that code directly; do not request another read merely because it is retained. "
                   "Only restored or stale navigation is historical. For source requests mode must be auto or full. "
                   "Selected navigation and summaries can be wrong; verify claims against source. "
                   "Archive choices do not prove branches irrelevant.")
    else:
        common += ("Available actions: map(node='repo'|exact ID,relation=contains|calls|called_by|imports|imported_by|extends,cursor), "
                   "search(query), source(node,mode=auto|full), grep(query), read(path,start,limit<=160), edit, test, answer. "
                   "Maps/searches return navigation only; read exact source before deciding. "
                   "Use source mode full or a bounded read when auto omits needed code. ")
        if policy == "working-set":
            common += ("The harness supplies bounded working state. restore(handle) recovers historical navigation; "
                       "restore(handle='archive:0') lists archived pointers. Evicted source needs a live read. ")
    return common


def requested_action(decision):
    if decision.get("query"):
        return {"action": "search", "query": decision["query"]}
    if decision.get("node"):
        node = decision["node"]
        if node == "repo" or node.startswith(("dir:", "file:")):
            return {"action": "map", "node": node}
        mode = decision.get("mode") or "auto"
        if mode not in ("auto", "full", "preview"):
            raise ValueError("Requested source mode must be auto, full, or preview")
        return {"action": "source", "node": node, "mode": mode}
    if decision.get("path"):
        return {"action": "read", "path": decision["path"], "start": decision.get("start") or 1,
                "limit": decision.get("limit") or 60}
    raise ValueError("request_context requires query, node, or path")


def totals(calls):
    fields = ("input_tokens", "output_tokens", "cache_read_input_tokens", "elapsed_ms")
    return {"calls": len(calls), **{field: old.total_known(calls, field) for field in fields},
            "cost_usd": old.total_cost(calls)}


def selector_context(state):
    """The routing model gets evidence descriptors; Terra gets original bytes.

    This projection never changes residency or the archived source. It makes no
    claim that signatures/descriptors establish behavior or sufficient evidence.
    """
    messages = deepcopy(state.render(role="selector"))
    header, content = messages[0]["content"].split("\n", 1)
    payload = json.loads(content)
    resident_hashes = state.resident_hashes()
    resident_sources = {}
    for entry in payload["retained_evidence"]:
        result = source_object(entry.get("observation", ""))
        if entry.get("action") == "source" and valid_source_view(result):
            resident_sources[(result.get("id"), result.get("mode"), result.get("hash") or result.get("sourceHash"))] = result

    def descriptor(entry):
        if entry.get("action") not in ("source", "read"):
            return entry
        observation = entry.get("observation", "")
        parsed = source_object(observation)
        result = {key: parsed[key] for key in ("id", "path", "filePath", "lines", "lineStart", "lineEnd",
                                              "hash", "sourceHash", "mode", "status", "omittedRanges", "hint")
                  if key in parsed}
        output = {"handle": entry.get("handle"), "action": entry["action"],
                  "source_descriptor": result, "source_bytes": 0,
                  "source_view_available_to_reasoner": False,
                  "exact_source_available_to_reasoner": False, "view_kind": "unavailable"}
        if entry.get("error"):
            output["error"] = entry["error"]
            result.setdefault("hint", entry["error"])
            return output
        if entry["action"] == "source":
            key = (parsed.get("id"), parsed.get("mode"), parsed.get("hash") or parsed.get("sourceHash"))
            effective = resident_sources.get(key) if parsed.get("status") == "unchanged" else parsed
            valid = valid_source_view(effective or {})
            if valid_source_view(parsed):
                output["source_bytes"] = len(parsed["source"].encode())
            if valid:
                output["view_kind"] = ("complete_symbol" if complete_source_view(effective) else
                                       "summary" if effective.get("mode") == "fold" else "bounded_preview")
                available = key in resident_hashes
                output["source_view_available_to_reasoner"] = available
                output["exact_source_available_to_reasoner"] = available and complete_source_view(effective)
                if parsed.get("status") == "unchanged":
                    output["resident_source_bytes"] = len(effective["source"].encode())
                elif not available:
                    output["delivery"] = "latest_only_not_resident"
            if not valid:
                recovery = source_recovery_action(parsed)
                if recovery:
                    output["recovery_action"] = recovery
                    output["recovery_basis"] = "Observed path; indexed lines are navigation hints, not verified current symbol boundaries"
        else:
            # Native reads are numbered file spans, not complete symbol bodies.
            match = re.fullmatch(r"([^\n]+) lines (\d+)-(\d+) of (\d+):\n([\s\S]*)", observation)
            if match and observed_path(match.group(1)):
                path, start, end, total, body = match.groups()
                start, end, total = int(start), int(end), int(total)
                rows = body.split("\n") if body else []
                prefixes = [f"{start + index}: " for index in range(len(rows))]
                valid = (1 <= start <= end <= total and len(rows) == end - start + 1
                         and all(row.startswith(prefix) for row, prefix in zip(rows, prefixes)))
                result.update(path=path, lines=[start, end], total_lines=total, status="fresh" if valid else "empty")
                if valid:
                    code = "\n".join(row[len(prefix):] for row, prefix in zip(rows, prefixes))
                    output["source_bytes"] = len(code.encode())
                    output["view_kind"] = "live_file_span"
                    try:
                        archived = state._load(entry["handle"])
                        resident = state.observation_resident("read", archived["input"], observation)
                    except (OSError, ValueError, KeyError):
                        resident = False
                    output["source_view_available_to_reasoner"] = resident
                    output["exact_source_available_to_reasoner"] = resident
                    output["complete_symbol_available_to_reasoner"] = False
                    if not resident:
                        output["delivery"] = "latest_only_not_resident"
        return output

    payload["retained_evidence"] = [descriptor(entry) for entry in payload["retained_evidence"]]
    messages[0]["content"] = header + "\n" + dump(payload)
    for message in messages[1:]:
        if message["content"].startswith("Latest tool observation "):
            prefix, observation = message["content"].split(":\n", 1)
            provenance = json.loads(prefix.removeprefix("Latest tool observation "))
            if provenance["action"] in ("source", "read"):
                original = observation.rsplit("\nChoose the next harness action", 1)[0]
                message["content"] = "Latest evidence descriptor:\n" + dump(descriptor({**provenance, "observation": original}))
    messages.append({"role": "user", "content": "Select navigation using the graph and evidence descriptors. "
                     "The reasoner receives resident original source bytes. exact_source_available_to_reasoner=false "
                     "means no complete symbol or verified live file span is available from that observation. "
                     "A bounded_preview is incomplete even when status=fresh. For stale/null source, choose the "
                     "observed recovery_action or another live read before claiming the missing source is available. "
                     "Indexed recovery lines are navigation hints; verify the declaration in current file text. "
                     "Once relevant source is available, escalate for reasoning; further exploration should address "
                     "a specific missing dependency or the reasoner's request. Descriptors do not establish code behavior."})
    return messages


def run_case(base, task, policy, index, out, python, max_calls=20):
    run_dir = out / policy / task["id"]
    run_dir.mkdir(parents=True, exist_ok=False)
    repo = run_dir / "checkout"
    session = str(uuid.uuid4())
    state = (SelectedWorkingState(run_dir / "archive", session) if policy == "selected" else
             WorkingState(run_dir / "archive", session) if policy == "working-set" else None)
    initial = prompt(task)
    if state:
        state.task(initial)
    history = [{"role": "user", "content": initial}]
    calls, actions = [], []
    candidates = ActionCandidates()
    selector = CodexDecisionSelector()
    role = "selector" if policy == "selected" else "reasoner"
    reads = edits = tests = 0
    first_patch = None
    full_hashes = set()
    selection_applied = selection_rejected = archived_handles = selector_fallbacks = 0
    status = "call_limit"
    started = time.monotonic()

    def deliver(action, observation, error=None, route=None):
        handle = state.observe(action["action"], action, observation, error=error, route=route) if state else None
        candidates.observe(action, observation, error)
        item = {"action": action, "observation": observation, "error": error, "route": route,
                "handle": handle}
        actions.append(item)
        history.append({"role": "user", "content": "Tool result:\n" + dump(item)})
        with (run_dir / "actions.jsonl").open("a") as log:
            log.write(dump(item) + "\n")

    old.worktree(base, repo, task, index)
    try:
        # Same genuine root map in all arms; selector receives no oracle path.
        initial_action = {"action": "map", "node": "repo"}
        _, observation, route = old.tool(initial_action, "codemesh", repo, task, python, session, 0, set(), 0)
        deliver(initial_action, observation, route=route)
        for number in range(1, max_calls + 1):
            artifact_dir = run_dir / "inputs" / f"call-{number:03}"
            selected = policy == "selected"
            context = (selector_context(state) if selected and role == "selector" else
                       state.render(role=role) if selected else state.render() if state else deepcopy(history))
            retention = []
            if role == "selector":
                nav = state.retention_candidates()
                offered = [{**item, "summary": dump(item), "protected": not item.get("archive_eligible", False)}
                           for item in nav["candidates"][:8]]
                result = selector.select(initial, context, candidates.choices(), offered, artifact_dir)
                call = result["call"]
                call["selection_error"] = result.get("error")
                selector_fallbacks += bool(result.get("error"))
                decision = result["action"]
                retention = result["retention"]
            else:
                call = model_call(context, system=reasoner_system(policy), schema=reasoner_schema(policy),
                                  model="gpt-5.6-terra", effort="low", artifact_dir=artifact_dir)
                decision = call.get("decision") or {}
            call.update(role=role, call_index=number, selected_action=decision, retention=retention,
                        context_bytes=len(dump(context).encode()))
            calls.append(call)
            with (run_dir / "calls.jsonl").open("a") as log:
                log.write(dump(call) + "\n")
            if call.get("error"):
                status = "model_error"
                break
            history.append({"role": "assistant", "content": call["text"]})
            if state:
                state.assistant(decision)
            if selected and retention:
                eligible = {item["handle"] for item in nav["candidates"] if item.get("eligible")}
                keep = [item["handle"] for item in retention if item["decision"] == "keep" and item["handle"] in eligible]
                archive = [item["handle"] for item in retention if item["decision"] == "archive"]
                reasons = "; ".join(item["reason"] for item in retention if item["decision"] != "defer")
                try:
                    if keep or archive:
                        report = state.apply_retention({"retain_handles": keep, "archive_handles": archive,
                                                        "reason": reasons[:2000], "metadata": {"selector_call": number}})
                        selection_applied += 1
                        archived_handles += len(report["archived_handles"])
                        with (run_dir / "selections.jsonl").open("a") as log:
                            log.write(dump({"call_index": number, "applied": True, "report": report}) + "\n")
                except (ValueError, TypeError) as exc:
                    selection_rejected += 1
                    with (run_dir / "selections.jsonl").open("a") as log:
                        log.write(dump({"call_index": number, "applied": False, "error": str(exc)}) + "\n")
                    deliver({"action": "control"}, f"Selection rejected; existing context preserved: {exc}", error=str(exc))
            action = decision.get("action")
            if action == "escalate":
                role = "reasoner"
                deliver({"action": "control"}, "Assess the current source evidence; request more context if needed.")
                continue
            if action == "request_context":
                try:
                    requested = requested_action(decision)
                    # The selector chooses among this concrete request and alternatives.
                    candidates.completed.discard(action_key(requested))
                    candidates.add(requested, "Reasoner requests: " + (decision.get("answer") or dump(requested)))
                    role = "selector"
                    deliver({"action": "control"}, "Missing evidence: " + (decision.get("answer") or dump(requested)))
                except ValueError as exc:
                    deliver({"action": "control"}, str(exc), error=str(exc))
                continue
            if action == "answer":
                if not edits or not reads or not tests:
                    deliver({"action": "control"}, "Read source, apply an edit and run a visible test before finishing.",
                            error="incomplete_workflow")
                    continue
                status = "complete"
                break
            try:
                if action == "edit" and not reads:
                    raise ValueError("Read live source before editing")
                known = state.resident_hashes() if state else full_hashes
                if action == "restore" and state:
                    observation = state.restore(decision.get("handle") or "")
                    route = {"backend": "observation_archive", "historical": True}
                else:
                    _, observation, route = old.tool(decision, "codemesh", repo, task, python, session, 0, known, tests)
                error = None
                if action == "read" or (action == "source" and route.get("status") == "fresh"):
                    reads += 1
                if action == "edit":
                    edits += 1
                    full_hashes.clear()
                    if first_patch is None:
                        first_patch = old.pytest(repo, python, old.ROOT / task["hidden_test"])["passed"]
                if action == "test":
                    tests += 1
            except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                observation, route, error = str(exc), {}, str(exc)
            deliver(decision, observation, error=error, route=route)
        hidden = old.pytest(repo, python, old.ROOT / task["hidden_test"])
        visible = old.pytest(repo, python, repo / "eval_visible_test.py")
        patch = old.command(["git", "diff", "--", "pydantic"], repo).stdout
        (run_dir / "patch.diff").write_text(patch)
        result = {"task": task["id"], "policy": policy, "status": status, "first_patch_pass": first_patch,
                  "final_patch_pass": hidden["passed"], "visible_pass": visible["passed"],
                  "hidden": hidden, "visible": visible, "totals": totals(calls),
                  "by_role": {name: totals([call for call in calls if call["role"] == name])
                              for name in ("selector", "reasoner")},
                  "actions": dict(Counter(item["action"]["action"] for item in actions)),
                  "retention_decisions": dict(Counter(item["decision"] for call in calls for item in call["retention"])),
                  "selection_applied": selection_applied, "selection_rejected": selection_rejected,
                  "navigation_handles_archived": archived_handles, "selector_fallbacks": selector_fallbacks,
                  "wall_ms": round((time.monotonic() - started) * 1000), "artifact_dir": str(run_dir)}
        (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    finally:
        old.remove_worktree(base, repo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("/tmp/codemesh-pydantic-benchmark"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path("/tmp/codemesh-edit-eval-venv/bin/python"))
    parser.add_argument("--policies", default=",".join(POLICIES))
    parser.add_argument("--task", action="append")
    parser.add_argument("--max-calls", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    policies = args.policies.split(",")
    if not policies or any(p not in POLICIES for p in policies) or len(set(policies)) != len(policies):
        parser.error("Invalid or duplicate policies")
    if not 1 <= args.max_calls <= 30:
        parser.error("max-calls must be 1..30")
    config = json.loads(old.TASKS.read_text())
    tasks = [task for task in config["tasks"] if not args.task or task["id"] in args.task]
    if not tasks:
        parser.error("No matching tasks")
    if old.command(["git", "rev-parse", "HEAD"], args.repo).stdout.strip() != old.PIN:
        parser.error("Unexpected benchmark revision")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.run and (out / "manifest.json").exists():
        parser.error("Choose a fresh output directory; this pilot does not silently resume")
    indexes = old.prepare(args.repo, tasks, out, args.python)
    plan = [(task, policy) for task in tasks for policy in policies]
    random.Random(args.seed).shuffle(plan)
    manifest = {"protocol": "selected-context-single-session-v3", "commit": old.PIN,
                "max_calls": args.max_calls, "seed": args.seed, "model_sampling_seeded": False,
                "plan": [[task["id"], policy] for task, policy in plan],
                "models": {name: catalog_fingerprint(name) for name in ("gpt-6-luna", "gpt-5.6-terra")},
                "build_fingerprint": old.build_fingerprint(), "decisions_api_used": False,
                "source_hashes": {path.name: sha256(path.read_bytes()).hexdigest() for path in
                    [Path(__file__), Path(__file__).with_name("branch_state.py"),
                     Path(__file__).with_name("decisions_selector.py"), Path(__file__).with_name("working_state.py"),
                     Path(old.__file__)]}}
    if not args.run:
        (out / "prepared.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print("Prepared fixtures. --run executes model calls.", flush=True)
        return
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    results = []
    for task, policy in plan:
        print(f"Running {task['id']} / {policy}", flush=True)
        result = run_case(args.repo, task, policy, indexes[task["id"]], out, args.python, args.max_calls)
        results.append(result)
        (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(f"{result['status']}, pass={result['final_patch_pass']}, input={result['totals']['input_tokens']}", flush=True)
        if result["status"] == "model_error":
            print("Stopped on provider error; preserved all calls.", flush=True)
            break


if __name__ == "__main__":
    main()
