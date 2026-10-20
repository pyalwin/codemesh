#!/usr/bin/env python3
"""Offline deterministic context replay. No repository tools or model calls.

Two saved full-history pilot controls supply exact tasks, decisions and tool
observations. A separately labeled synthetic journey covers navigation repetition,
pagination, branch switching, errors and recovery absent from those short controls.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from typing import Any

from focused_state import FocusedWorkingState
from replay_branch_state import (
    PREFIX, NavigationExposure, common_prefix, delivered_views, journey_observations,
    payload, serialize, source_views, verify_state,
)
from working_state import BUNDLES, NAVIGATION, WorkingState

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTROLS = ROOT / "eval/results/selected-context-2026-10-19/pilot/full"
DEFAULT_OUT = ROOT / "eval/results/graphify-deterministic-2026-10-19/replay"
TASKS = ("factory_validated_data", "negative_bytesize")
PROTECTED = ("initial_task", "current_phase", "retained_evidence", "prior_model_hypotheses",
             "successful_edits", "latest_test", "latest_tool_error")
SOURCE = {"source", "read", *BUNDLES}


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def file_fingerprint(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": str(path.resolve()), "sha256": sha256(data).hexdigest(), "bytes": len(data)}


def normalized(item: dict[str, Any]) -> dict[str, Any]:
    action = item["action"]
    return {"action": action["action"], "input": action, "observation": item["observation"],
            "error": item.get("error"), "route": item.get("route")}


class ReplayPair:
    """Compare rendered state at fixed checkpoints, without changing actions."""
    def __init__(self, directory: Path, session: str, task: str):
        directory.mkdir(parents=True)
        self.directory = directory
        self.states = {"working": WorkingState(directory / "working-archive", session),
                       "focused": FocusedWorkingState(directory / "focused-archive", session)}
        self.task = task
        self.delivered: set[tuple[str, str, str]] = set()
        self.source_records: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self.events: list[str] = []
        self.counts = Counter()
        self.previous: dict[str, str] = {}
        self.navigation = NavigationExposure()
        self.latest: str | None = None
        self.recovery_checks: list[dict[str, Any]] = []
        self.reset(session, task)

    def reset(self, session: str, task: str) -> None:
        for state in self.states.values():
            state.reset(session, task)
        self.task, self.latest = task, None
        self.delivered.clear()
        self.source_records.clear()
        self.previous = {}
        self.events = ["context_reset"]
        self.navigation.reset()
        self.counts["context_starts"] += 1

    def assistant(self, value: dict[str, Any] | str) -> None:
        for state in self.states.values():
            state.assistant(value)
        self.latest = None

    def observe(self, record: dict[str, Any]) -> str:
        action = record["action"]
        handles = [state.observe(action, record["input"], record["observation"],
                                 error=record.get("error"), route=record.get("route"))
                   for state in self.states.values()]
        if len(set(handles)) != 1:
            raise AssertionError("The policies did not archive identical observation records")
        self.delivered.update(delivered_views(record))
        if action in SOURCE:
            self.source_records.append(deepcopy(record))
        self.counts[action] += 1
        self.events.append(action)
        self.navigation.observe(record)
        self.latest = action
        return handles[0]

    def restore(self, handle: str, expected: str) -> None:
        before = {name: state.resident_hashes() for name, state in self.states.items()}
        values = [state.restore(handle) for state in self.states.values()]
        if values != [expected, expected]:
            raise AssertionError("Navigation restore did not return the exact archived observation")
        if any(state.resident_hashes() != before[name] for name, state in self.states.items()):
            raise AssertionError("Restoration changed source authorization")
        self.recovery_checks.append({"handle": handle, "exact_bytes": len(expected.encode()),
                                     "byte_identical": True, "source_authority_unchanged": True})
        self.events.append("restore")
        self.counts["restore"] += 1
        self.latest = "restore"

    def checks(self, messages: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
        rendered = {name: payload(value) for name, value in messages.items()}
        for field in PROTECTED:
            if rendered["working"].get(field) != rendered["focused"].get(field):
                raise AssertionError(f"Protected state differs: {field}")
        if self.states["working"].resident_hashes() != self.states["focused"].resident_hashes():
            raise AssertionError("Focused presentation changed source hash authority")
        validations = {name: verify_state(state, messages[name], self.task, self.delivered)
                       for name, state in self.states.items()}
        source_residency = []
        for record in self.source_records:
            resident = {name: state.observation_resident(record["action"], record["input"], record["observation"])
                        for name, state in self.states.items()}
            if len(set(resident.values())) != 1:
                raise AssertionError("Exact source/native read residency differs")
            if resident["working"]:
                for name in messages:
                    in_evidence = any(item["observation"] == record["observation"]
                                      for item in rendered[name]["retained_evidence"])
                    in_latest = record["observation"] in messages[name][-1]["content"]
                    if not in_evidence and not in_latest:
                        raise AssertionError("Resident source bytes are not exactly present in rendered evidence")
            source_residency.append({"action": record["action"],
                                     "observation_sha256": sha256(record["observation"].encode()).hexdigest(),
                                     "resident": resident["working"]})
        if self.latest is not None and self.latest not in NAVIGATION:
            if messages["working"][-1] != messages["focused"][-1]:
                raise AssertionError("Latest source/edit/test/error/recovery output changed")
        return {"protected_fields_equal": list(PROTECTED), "source_residency_equal": True,
                "source_residency": source_residency, "states": validations}

    def snapshot(self, label: str, *, full: list[dict[str, str]] | None = None,
                 metadata: dict[str, Any] | None = None) -> None:
        messages = {name: state.render() for name, state in self.states.items()}
        for name, state in self.states.items():
            if messages[name] != state.render():
                raise AssertionError("render() mutated state")
        validation = self.checks(messages)
        if full is not None:
            messages["recorded_full"] = deepcopy(full)
        prompts = {name: serialize(value) for name, value in messages.items()}
        nav_only = bool(self.previous and self.events and all(event in NAVIGATION for event in self.events))
        self.snapshots.append({"checkpoint": label, "events_since_previous": list(self.events),
            "navigation_only_transition": nav_only, "metadata": metadata or {},
            "messages": messages, "prompts": prompts,
            "prompt_bytes": {name: len(value.encode()) for name, value in prompts.items()},
            "common_prefix_bytes": {name: common_prefix(self.previous[name], value)
                                    for name, value in prompts.items() if name in self.previous},
            "validation": validation,
            "stats": {name: state.context_stats() for name, state in self.states.items()}})
        self.previous = prompts
        self.events = []

    def finish(self) -> dict[str, Any]:
        (self.directory / "snapshots.json").write_text(json.dumps(self.snapshots, indent=2, ensure_ascii=False) + "\n")
        totals = {name: sum(item["prompt_bytes"][name] for item in self.snapshots)
                  for name in self.snapshots[0]["prompt_bytes"]}
        peaks = {name: max(item["prompt_bytes"][name] for item in self.snapshots) for name in totals}
        prefix = {}
        for name in ("working", "focused"):
            values = [item["common_prefix_bytes"][name] for item in self.snapshots
                      if item["navigation_only_transition"] and name in item["common_prefix_bytes"]]
            prefix[name] = {"pairs": len(values), "values": values,
                            "median_bytes": median(values) if values else None}
        return {"checkpoints": len(self.snapshots), "serialized_bytes_sum": totals, "peak_prompt_bytes": peaks,
                "focused_minus_working_bytes": totals["focused"] - totals["working"],
                "events": dict(self.counts), "navigation_exposure": dict(self.navigation.counts),
                "navigation_only_prefix": prefix, "recovery_checks": self.recovery_checks,
                "snapshots": str(self.directory / "snapshots.json"),
                "final_stats": {name: state.context_stats() for name, state in self.states.items()}}


def archived_history(call: dict[str, Any]) -> list[dict[str, str]]:
    descriptor = call["input_artifacts"]["files"]["stdin.txt"]
    raw = Path(descriptor["path"]).read_bytes()
    if sha256(raw).hexdigest() != descriptor["sha256"] or len(raw) != descriptor["bytes"]:
        raise AssertionError("Archived input fingerprint changed")
    text = raw.decode()
    if not text.startswith(PREFIX):
        raise ValueError("Expected an explicit full transcript")
    messages = json.loads(text[len(PREFIX):])
    if serialize(messages).encode() != raw:
        raise AssertionError("Reconstructed original stdin differs from saved bytes")
    return messages


def replay_control(directory: Path, output: Path) -> dict[str, Any]:
    """Replay exact append-only full-policy input transcripts and final tail."""
    calls = [json.loads(line) for line in (directory / "calls.jsonl").read_text().splitlines() if line.strip()]
    action_log = [json.loads(line) for line in (directory / "actions.jsonl").read_text().splitlines() if line.strip()]
    result = json.loads((directory / "result.json").read_text())
    if result["policy"] != "full":
        raise ValueError("Replay requires recorded full-history controls")
    first = archived_history(calls[0])
    task = first[0]["content"]
    pair = ReplayPair(output, "replay-" + result["task"], task)
    previous: list[dict[str, str]] = []
    observations = []
    matched = 0

    def apply_message(message: dict[str, str]) -> None:
        if message["role"] == "assistant":
            try:
                value = json.loads(message["content"])
            except ValueError:
                value = message["content"]
            pair.assistant(value)
        elif message["role"] == "user" and message["content"].startswith("Tool result:\n"):
            item = json.loads(message["content"][len("Tool result:\n"):])
            observations.append(item)
            if item["action"]["action"] == "restore":
                raise ValueError("Saved full controls unexpectedly contain archive restoration")
            pair.observe(normalized(item))
        else:
            raise ValueError("Unexpected transcript event; do not infer missing lifecycle")

    for index, call in enumerate(calls):
        history = archived_history(call)
        if history[:len(previous)] != previous:
            raise AssertionError("Saved control history is not append-only")
        added = history[len(previous):]
        if index == 0:
            if not added or added[0] != {"role": "user", "content": task}:
                raise AssertionError("Initial task does not match")
            added = added[1:]
        for message in added:
            apply_message(message)
        matched += 1
        pair.snapshot(f"call-{index + 1:03}", full=history,
                      metadata={"original_error": call.get("error"), "original_call_index": call["call_index"],
                                "original_stdin_matched": True})
        previous = history
    if action_log[:len(observations)] != observations:
        raise AssertionError("Archived prompts and action journal disagree")
    # Results after the final provider call were never input to another call.
    # Preserve this tail for state checks, but exclude it from prompt-byte sums.
    if not calls[-1].get("error"):
        pair.assistant(calls[-1].get("selected_action") or calls[-1]["text"])
    for item in action_log[len(observations):]:
        pair.observe(normalized(item))
    final_messages = {name: state.render() for name, state in pair.states.items()}
    pair.checks(final_messages)
    (output / "final-state.json").write_text(json.dumps(final_messages, indent=2, ensure_ascii=False) + "\n")
    return {"task": result["task"], "kind": "saved_full_control", "original_status": result["status"],
            "original_stdin_matches": matched, "model_calls_made": 0,
            "source_journals": {name: file_fingerprint(directory / name)
                                for name in ("calls.jsonl", "actions.jsonl", "result.json")},
            **pair.finish()}


def scripted_journey(output: Path) -> dict[str, Any]:
    fixture = journey_observations()
    pair = ReplayPair(output, "focused-synthetic-journey", fixture["task"])
    handles = {}
    history = [{"role": "user", "content": fixture["task"]}]

    def observe(label: str, action: str, inputs: dict[str, Any], observation: str,
                error: str | None = None, route: dict[str, Any] | None = None) -> None:
        decision = {"action": action, **inputs}
        pair.assistant(decision)
        record = {"action": action, "input": decision, "observation": observation, "error": error,
                  "route": route or {"backend": "synthetic_fixture"}}
        handles[label] = pair.observe(record)
        history.extend([{"role": "assistant", "content": dump(decision)},
                        {"role": "user", "content": "Tool result:\n" + dump(record)}])
        pair.snapshot(label, full=history)

    def restore(label: str, origin: str, expected: str) -> None:
        decision = {"action": "restore", "handle": handles[origin]}
        pair.assistant(decision)
        pair.restore(handles[origin], expected)
        history.extend([{"role": "assistant", "content": dump(decision)},
                        {"role": "user", "content": "Restored historical navigation:\n" + expected}])
        pair.snapshot(label, full=history)

    auth = json.loads(fixture["auth"])
    auth_page1 = dump({**auth, "entries": auth["entries"][:2], "nextCursor": "auth-page-2"})
    auth_page2 = dump({**auth, "entries": auth["entries"][2:]})
    pair.snapshot("initial", full=history)
    observe("root", "map", {"node": "repo"}, fixture["root"])
    observe("auth_page1", "map", {"node": "dir:auth"}, auth_page1)
    observe("login", "map", {"node": "file:auth/login.py"}, fixture["login"])
    observe("login_source", "source", {"node": "symbol:auth/login.py:login", "mode": "full"}, fixture["login_source"])
    observe("billing", "map", {"node": "dir:billing"}, fixture["billing"])
    observe("invoices", "map", {"node": "file:billing/invoices.py"}, fixture["invoices"])
    observe("invoice_source", "source", {"node": "symbol:billing/invoices.py:create_invoice", "mode": "full"}, fixture["invoice_source"])
    observe("root_repeat", "map", {"node": "repo"}, fixture["root"])
    observe("auth_repeat", "map", {"node": "dir:auth"}, auth_page1)
    focused = pair.states["focused"]
    if focused.context_stats()["collapsed_requests"] != 2:
        raise AssertionError("Identical repeated map requests were not collapsed")
    if len(focused.resident_hashes()) != 2:
        raise AssertionError("Branch switching discarded source evidence")
    restore("recover_collapsed_auth", "auth_repeat", auth_page1)
    observe("auth_page2", "map", {"node": "dir:auth", "cursor": "auth-page-2"}, auth_page2)
    if focused.context_stats()["pending_page_count"]:
        raise AssertionError("Successful continuation did not clear pending cursor")
    observe("search_error", "search", {"query": "unknown_validator"}, "Search failed: simulated index unavailable", "index_unavailable")
    observe("failed_test", "test", {}, "1 failed: session owner mismatch", route={"passed": False})
    observe("edit", "edit", {"path": "auth/login.py", "old": "user_id=identity.id", "new": "user_id=str(identity.id)"}, "Edited auth/login.py")
    if len(focused.resident_hashes()) != 1:
        raise AssertionError("Edit failed to invalidate exactly the changed file's source")
    observe("passed_test", "test", {}, "1 passed: session owner check", route={"passed": True})
    observe("invoices_after_edit", "map", {"node": "file:billing/invoices.py"}, fixture["invoices"])
    for state in pair.states.values():
        try:
            state.restore(handles["login_source"])
        except ValueError:
            pass
        else:
            raise AssertionError("Source restored from archive without a live read")
    before_reset = {name: state.context_stats() for name, state in pair.states.items()}
    pair.reset("focused-fresh-context", fixture["task"])
    history[:] = [{"role": "user", "content": fixture["task"]}]
    pair.snapshot("fresh_context", full=history)
    restore("historical_billing", "billing", fixture["billing"])
    if any(state.resident_hashes() for state in pair.states.values()):
        raise AssertionError("Historical navigation resurrected source authority")
    return {"kind": "synthetic_two_branch_journey", "checks": [
                "Two exact source blocks survive navigation and repeated maps.",
                "Two identical repeated requests collapse into latest compact pointers.",
                "The collapsed map is restored byte-identically without new source authority.",
                "Pagination continuation clears the pending cursor.",
                "Task, error, edit and test evidence match the unchanged base policy at every checkpoint.",
                "Edit invalidates only its affected source; archived source restoration is rejected.",
                "Reset clears source authority; old navigation remains exactly recoverable."],
            "pre_reset_stats": before_reset, **pair.finish()}


def markdown_report(report: dict[str, Any]) -> str:
    lines = ["# Deterministic focused-state offline replay", "",
        "No model or repository tool was called. All values below are **serialized UTF-8 input bytes**, not tokens, cost, quality or server cache measurements.", "",
        "## Saved real controls", "",
        "Two fixed full-history controls from the earlier single-session pilot are replayed: the longer factory-default repair and the shorter negative-ByteSize repair. Their original tasks, assistant decisions, observations, errors and routes are reused exactly. Each original stdin is fingerprinted and reconstructed byte-identically. The focused state receives no selector decisions and no inferred branches.", "",
        "| Task | Saved calls | Original full input bytes | Working input bytes | Focused input bytes | Focused minus working |",
        "|---|---:|---:|---:|---:|---:|"]
    for row in report["real_controls"]:
        b = row["serialized_bytes_sum"]
        lines.append(f"| {row['task']} | {row['checkpoints']} | {b['recorded_full']:,} | {b['working']:,} | {b['focused']:,} | {row['focused_minus_working_bytes']:+,} |")
    totals = {name: sum(row["serialized_bytes_sum"][name] for row in report["real_controls"])
              for name in ("recorded_full", "working", "focused")}
    difference = totals["focused"] - totals["working"]
    lines += ["", f"Across these saved calls, focused presentation changes the existing working-state input size by {difference:+,} bytes ({difference / totals['working'] * 100:+.2f}%). The saved actions are fixed; this cannot predict what a live agent would read or answer after seeing a different prompt.", "",
        "The real controls include native source reads, one edit and one visible test each. They contain no repeated navigation, tool errors, context resets, or archive restores, and no typed `source` reads. Those missing cases are covered only by the synthetic fixture below. Original live success is historical metadata and is not a focused-policy quality result.", "",
        "## Prefix layout on navigation-only transitions", "",
        "A pair qualifies only within one context when all observations since the prior snapshot are map/search/grep. Source/read, test, edit, restore and reset transitions are excluded. Assistant decisions may change. We compare the exact serialized prefix byte by byte; bytes are not cache tokens, and no provider cache behavior is inferred.", "",
        "| Case | Navigation-only pairs | Working median prefix bytes | Focused median prefix bytes |",
        "|---|---:|---:|---:|"]
    for row in [*report["real_controls"], report["synthetic_journey"]]:
        w, f = row["navigation_only_prefix"]["working"], row["navigation_only_prefix"]["focused"]
        lines.append(f"| {row.get('task', 'synthetic journey')} | {w['pairs']} | {w['median_bytes']} | {f['median_bytes']} |")
    fixture = report["synthetic_journey"]
    lines += ["", "## Synthetic two-branch fixture", "",
        "The fixture explores root → auth → login/source, then billing → invoices/source; repeats two maps, restores a collapsed map, follows a pending page, records a search error, edits login, tests, resets context, and restores historical billing navigation. This is a state-management test, not a repository task or a sampled model trajectory.", ""]
    lines += ["- " + check for check in fixture["checks"]]
    lines += ["", "Synthetic byte totals aggregate diagnostic checkpoints, not a model-call schedule. Per-checkpoint bytes and exact prompts are in `synthetic/snapshots.json`; real call prompts are in each task's `snapshots.json`. Recovery outputs preserve original bytes, with historical provenance. Both policies use the same source LRU and evidence budget; source/native-read residency and protected fields are checked for equality at every snapshot.", "",
        "## Measurement limits and reproduction", "",
        "- Input measurements include the fixed explicit-transcript prefix and JSON escaping. They exclude system instructions, response schemas, provider framing, outputs and external runtime context.",
        "- The focused main index, pending-page records and duplicate pointers can add prompt overhead. Inspect measured totals rather than assuming a reduction.",
        "- Static instruction/schema changes in the live harness are not evaluated here. No quality or live cost comparison was run.",
        "- `report.json` records input journals, source fingerprints, checks, complete byte/prefix values, and exact prompt locations.",
        "- Reproduce: `PYTHONPATH=eval python3 eval/replay_focused_state.py --out <new-directory>`.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controls", type=Path, default=DEFAULT_CONTROLS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Choose a new output directory; saved evidence is not overwritten")
    args.out.mkdir(parents=True)
    real = [replay_control(args.controls / task, args.out / task) for task in TASKS]
    synthetic = scripted_journey(args.out / "synthetic")
    report = {"kind": "offline_deterministic_focused_state_replay", "model_calls_made": 0,
              "measurement": "UTF-8 bytes of exact serialized explicit transcripts including stdin prefix",
              "excluded": ["static instructions", "schemas", "provider framing", "tokens", "cost", "quality"],
              "real_controls": real, "synthetic_journey": synthetic,
              "source_fingerprints": {name: file_fingerprint(ROOT / "eval" / name)
                                      for name in ("working_state.py", "focused_state.py", "replay_focused_state.py",
                                                   "replay_branch_state.py", "source_bundles.py")}}
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / "REPORT.md").write_text(markdown_report(report))
    print(json.dumps({"report": str(args.out / "REPORT.md"), "real": [{key: row[key] for key in
        ("task", "checkpoints", "original_stdin_matches", "serialized_bytes_sum", "focused_minus_working_bytes")}
        for row in real], "synthetic_checkpoints": synthetic["checkpoints"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
