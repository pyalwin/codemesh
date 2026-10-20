#!/usr/bin/env python3
"""Offline replay of saved actions; no tools, models, or inferred retention decisions.

Historical actions do not contain selector decisions. SelectedWorkingState is
therefore replayed without selection, and its reasoner view is diagnostic only.
The separately scripted navigation journey supplies explicit retention decisions.
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

from source_bundles import source_blocks
from working_state import WorkingState

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "Continue this explicit conversation transcript:\n"
VARIANTS = ("full", "working", "hydrate", "bundle", "combined")
GRADED = {"complete", "turn_limit", "context_limit"}


def serialize(messages: list[dict[str, str]]) -> str:
    return PREFIX + json.dumps(messages, ensure_ascii=False)


def size(text: str) -> int:
    return len(text.encode())


def common_prefix(before: str, after: str) -> int:
    left, right = before.encode(), after.encode()
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index
    return min(len(left), len(right))


def payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    return json.loads(messages[0]["content"].split("\n", 1)[1])


def json_objects(text: str):
    """Decode embedded observation envelopes without interpreting their text."""
    decoder = json.JSONDecoder()
    position = 0
    while position < len(text):
        start = text.find("{", position)
        if start < 0:
            return
        try:
            value, length = decoder.raw_decode(text[start:])
        except ValueError:
            position = start + 1
            continue
        yield value
        position = start + length


def source_views(messages: list[dict[str, str]]) -> set[tuple[str, str, str]]:
    """Find hash-validated exact source envelopes in the actual rendered text."""
    found = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            source = value.get("source")
            digest = value.get("hash") or value.get("sourceHash")
            if (value.get("status") == "fresh" and value.get("mode") in ("auto", "preview", "full")
                    and isinstance(source, str) and isinstance(value.get("id"), str)
                    and sha256(source.encode()).hexdigest() == digest):
                found.add((value["id"], value["mode"], digest))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            for item in json_objects(value):
                walk(item)

    for message in messages:
        walk(message["content"])
    return found


def delivered_views(action: dict[str, Any]) -> set[tuple[str, str, str]]:
    if action.get("error"):
        return set()
    result = set()
    for block in source_blocks(action["action"], action.get("observation", "")):
        source, digest = block.get("source"), block.get("hash")
        node, mode = block.get("id"), block.get("mode")
        if (block.get("status") == "fresh" and mode in ("auto", "preview", "full")
                and isinstance(node, str) and isinstance(source, str)
                and sha256(source.encode()).hexdigest() == digest):
            result.add((node, mode, digest))
            alias = action.get("input", {}).get("node")
            if action["action"] == "source" and isinstance(alias, str):
                result.add((alias, mode, digest))
    return result


def decision_action(row: dict[str, Any], call: dict[str, Any]) -> dict[str, Any] | None:
    """Automatic hydrate occupies a phase action entry, but never a model turn."""
    if call["kind"] != "decision" or call.get("error"):
        return None
    phase = next(item for item in row["phases"] if item["phase"] == call["phase"])
    actions = [item for item in phase["actions"] if item["action"] != "hydrate"]
    index = call["turn"] - 1
    if not 0 <= index < len(actions):
        raise ValueError("Successful saved decision has no recorded phase action")
    return actions[index]


def control_message(action: str, phase: int) -> str:
    if action == "early_action":
        return "Edit and test are available in the patch phase."
    if action == "premature_answer":
        return ("Read at least one source span before answering." if phase == 0
                else "Apply a source edit before finishing.")
    raise ValueError("Not a saved harness control action")


class NavigationExposure:
    """Only observed contains edges establish ancestry; paths are not guessed."""
    def __init__(self) -> None:
        self.counts = Counter()
        self.reset()

    def reset(self) -> None:
        self.parents: dict[str, str] = {}
        self.previous: str | None = None

    def ancestor(self, parent: str, child: str) -> bool:
        seen = set()
        while child in self.parents and child not in seen:
            seen.add(child)
            child = self.parents[child]
            if child == parent:
                return True
        return False

    def observe(self, action: dict[str, Any]) -> None:
        if action["action"] != "map":
            return
        self.counts["map_actions"] += 1
        if action.get("error"):
            self.counts["map_errors"] += 1
            return
        observation = json.loads(action["observation"])
        node = observation.get("node", {}).get("id")
        if not isinstance(node, str):
            return
        self.counts["repo_maps" if node == "repo" else "nonroot_maps"] += 1
        if action.get("input", {}).get("cursor"):
            self.counts["pagination_followups"] += 1
        if self.previous and self.previous != node:
            key = ("observed_descents" if self.ancestor(self.previous, node) else
                   "observed_backtracks" if self.ancestor(node, self.previous) else
                   "unrelated_or_unproven_transitions")
            self.counts[key] += 1
        if action.get("input", {}).get("relation", "contains") == "contains":
            for entry in observation.get("entries", []):
                if isinstance(entry.get("id"), str):
                    self.parents.setdefault(entry["id"], node)
        self.previous = node


def verify_state(state: Any, messages: list[dict[str, str]], initial_task: str,
                 delivered: set[tuple[str, str, str]]) -> dict[str, Any]:
    rendered = payload(messages)
    if rendered.get("initial_task") != initial_task:
        raise AssertionError("Initial task was not preserved exactly")
    residents = state.resident_hashes()
    if not residents <= delivered:
        raise AssertionError("Source not delivered in this context acquired residency")
    visible = {(mode, digest) for _, mode, digest in source_views(messages)}
    if any((mode, digest) not in visible for _, mode, digest in residents):
        raise AssertionError("Conditional source authorization lacks exact rendered bytes")
    return {"initial_task_present": True, "resident_sources_visible": True,
            "resident_hashes": sorted(residents), "visible_source_views": sorted(source_views(messages))}


def replay_attempt(row: dict[str, Any], directory: Path, selected_class: Any = None) -> dict[str, Any]:
    """Preserve every call/error; never reexecute a saved repository action."""
    directory.mkdir(parents=True)
    model_calls = [call for call in row["calls"] if call.get("kind") != "memory_error"]
    states = {"working_set": WorkingState(directory / "working-set-archive", model_calls[0]["context_session_id"])}
    if selected_class is not None:
        states["selected_no_decisions"] = selected_class(directory / "selected-archive", model_calls[0]["context_session_id"])
    history: list[dict[str, str]] = []
    delivered: set[tuple[str, str, str]] = set()
    exposure, counts = NavigationExposure(), Counter()
    session = phase_number = None
    initial_task = ""
    previous_prompts: dict[str, str] = {}
    totals, peaks = Counter(), Counter()
    prefix_values: dict[str, list[int]] = {}
    conditional_gaps = []
    snapshots = []
    observed_matches = 0

    def observe(action: dict[str, Any]) -> None:
        counts[action["action"]] += 1
        exposure.observe(action)
        delivered.update(delivered_views(action))
        for label, state in states.items():
            if action["action"] == "restore" and not action.get("error"):
                restored = state.restore(action["input"]["handle"])
                if restored != action["observation"]:
                    raise AssertionError(f"{label}: archive restoration differs from saved observation")
            else:
                state.observe(action["action"], action.get("input", {}), action["observation"],
                              error=action.get("error"), route=action.get("route"))

    for index, call in enumerate(model_calls, 1):
        if call["context_session_id"] != session:
            session = call["context_session_id"]
            history, phase_number, initial_task = [], None, ""
            delivered.clear()
            previous_prompts = {}
            exposure.reset()
            for state in states.values():
                state.reset(session)
                if state.resident_hashes():
                    raise AssertionError("Context reset retained source authorization")
            counts["context_starts"] += 1
        if call["kind"] == "decision" and phase_number != call["phase"]:
            phase = next(item for item in row["phases"] if item["phase"] == call["phase"])
            phase_number = call["phase"]
            initial_task = initial_task or phase["prompt"]
            for state in states.values():
                state.task(phase["prompt"])
            for action in phase["actions"]:
                if action["action"] != "hydrate":
                    break
                observe(action)
        history.append({"role": "user", "content": call["message"]})
        messages = {"full_history": history}
        validations = {}
        for label, state in states.items():
            rendered = state.render()
            if rendered != state.render():
                raise AssertionError("Rendering mutated state")
            validations[label] = verify_state(state, rendered, initial_task, delivered)
            if call["kind"] in ("handoff", "handoff_retry"):
                rendered = [*rendered, {"role": "user", "content": call["message"]}]
            messages[label] = rendered
            if label == "selected_no_decisions":
                reasoner = state.render(role="reasoner")
                validations["reasoner_diagnostic"] = verify_state(state, reasoner, initial_task, delivered)
                if call["kind"] in ("handoff", "handoff_retry"):
                    reasoner = [*reasoner, {"role": "user", "content": call["message"]}]
                messages["reasoner_diagnostic"] = reasoner
        prompts = {label: serialize(items) for label, items in messages.items()}
        if initial_task not in history[0]["content"]:
            raise AssertionError("Full transcript lost the session task")
        inputs = call.get("input_artifacts", {}).get("files", {}).get("stdin.txt")
        archived_match = None
        if inputs:
            raw = Path(inputs["path"]).read_bytes()
            if sha256(raw).hexdigest() != inputs["sha256"]:
                raise AssertionError("Saved input fingerprint mismatch")
            original_policy = call.get("prompt_context", {}).get("policy", "full")
            label = "full_history" if original_policy == "full" else "working_set"
            archived_match = raw == prompts[label].encode()
            if not archived_match:
                (directory / f"mismatch-{index}.txt").write_text(prompts[label])
                raise AssertionError(f"Saved {original_policy} prompt reconstruction differs at call {index}")
            observed_matches += 1
        byte_counts = {label: size(text) for label, text in prompts.items()}
        prefixes = {label: common_prefix(previous_prompts[label], text)
                    for label, text in prompts.items() if label in previous_prompts}
        for label, value in byte_counts.items():
            totals[label] += value
            peaks[label] = max(peaks[label], value)
        for label, value in prefixes.items():
            prefix_values.setdefault(label, []).append(value)
        snapshots.append({"call_index": index, "phase": call["phase"], "turn": call.get("turn"),
            "kind": call["kind"], "original_error": call.get("error"),
            "context_session_id": session, "original_input_reconstructed": archived_match,
            "prompt_bytes": byte_counts, "common_prefix_bytes": prefixes, "prompts": prompts,
            "validation": validations, "navigation_exposure": dict(exposure.counts),
            "state_stats": {label: state.context_stats() for label, state in states.items()}})
        previous_prompts = prompts
        if call.get("error"):
            counts["model_error_calls_preserved"] += 1
            continue
        history.append({"role": "assistant", "content": call["text"]})
        decision = call.get("decision")
        for state in states.values():
            state.assistant(decision if decision is not None else call["text"])
        action = decision_action(row, call)
        if action is None or action["action"] == "answer":
            continue
        if action["action"] in ("early_action", "premature_answer"):
            feedback = control_message(action["action"], call["phase"])
            counts[action["action"]] += 1
            for state in states.values():
                state.observe("control", decision or {}, feedback, error=feedback, route={"backend": "harness"})
            continue
        if action["action"] == "source" and action.get("input", {}).get("if_hash"):
            requested = action["input"]
            key = (requested["node"], requested.get("mode") or "auto", requested["if_hash"])
            for label, state in states.items():
                if key not in state.resident_hashes():
                    conditional_gaps.append({"call_index": index, "policy": label,
                                             "requested": list(key), "original_tool_error": action.get("error")})
        observe(action)
    with (directory / "snapshots.jsonl").open("w") as output:
        for item in snapshots:
            output.write(json.dumps(item, ensure_ascii=False) + "\n")
    final = {label: state.render() for label, state in states.items()}
    (directory / "final-state.json").write_text(json.dumps(final, indent=2, ensure_ascii=False) + "\n")
    return {"task": row["task"], "arm": row["mode"], "repeat": row["repeat"], "attempt_id": row["attempt_id"],
            "original_status": row["status"], "original_final_patch_pass": row.get("final_patch_pass"),
            "model_calls_replayed": len(model_calls), "original_input_matches": observed_matches,
            "bytes": dict(totals), "peak_bytes": dict(peaks), "actions": dict(counts),
            "navigation_exposure": dict(exposure.counts), "conditional_read_counterfactual_gaps": conditional_gaps,
            "adjacent_prefix": {label: {"pairs": len(values), "median_bytes": median(values),
                                         "min_bytes": min(values), "max_bytes": max(values)}
                                for label, values in prefix_values.items()},
            "snapshots": str(directory / "snapshots.jsonl"),
            "source_task_snapshots_checked": len(snapshots)}


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    navigation = Counter()
    for row in rows:
        totals.update(row["bytes"])
        navigation.update(row["navigation_exposure"])
    return {"attempts": len(rows), "original_statuses": dict(Counter(row["original_status"] for row in rows)),
            "model_calls": sum(row["model_calls_replayed"] for row in rows), "bytes": dict(totals),
            "original_input_matches": sum(row["original_input_matches"] for row in rows),
            "navigation_exposure": dict(navigation),
            "conditional_read_counterfactual_gaps": sum(len(row["conditional_read_counterfactual_gaps"]) for row in rows)}


def journey_observations() -> dict[str, Any]:
    """A declared synthetic fixture, not observations from a real repository."""
    def entry(node: str, name: str, kind: str, **extra: Any) -> dict[str, Any]:
        return {"id": node, "name": name, "type": kind, **extra}

    def navigation(node: dict[str, Any], children: list[dict[str, Any]]) -> str:
        return json.dumps({"node": node, "entries": children}, separators=(",", ":"))

    def source(path: str, name: str, text: str) -> str:
        return json.dumps({"id": f"symbol:{path}:{name}", "path": path,
            "lines": [1, len(text.splitlines())], "status": "fresh", "mode": "full",
            "hash": sha256(text.encode()).hexdigest(), "source": text}, separators=(",", ":"))

    return {
        "task": "Trace login authentication and billing authorization. Determine how a session identity reaches invoice creation. Inspect both paths before proposing changes; retain unresolved branches and preserve exact source evidence.",
        "root": navigation(entry("repo", "Shop service", "repo"), [
            entry("dir:auth", "auth", "dir", hasChildren=True),
            entry("dir:billing", "billing", "dir", hasChildren=True),
            entry("dir:api", "api", "dir", hasChildren=True),
            entry("dir:storage", "storage", "dir", hasChildren=True),
            entry("dir:tests", "tests", "dir", hasChildren=True),
            entry("file:settings.py", "settings.py", "file", hasChildren=True)]),
        "auth": navigation(entry("dir:auth", "auth", "dir"), [
            entry("file:auth/login.py", "login.py", "file", hasChildren=True),
            entry("file:auth/session.py", "session.py", "file", hasChildren=True),
            entry("file:auth/passwords.py", "passwords.py", "file", hasChildren=True),
            entry("file:auth/permissions.py", "permissions.py", "file", hasChildren=True)]),
        "login": navigation(entry("file:auth/login.py", "login.py", "file"), [
            entry("symbol:auth/login.py:login", "login", "symbol", signature="def login(credentials, sessions)", hasChildren=False),
            entry("symbol:auth/login.py:logout", "logout", "symbol", signature="def logout(session_id, sessions)", hasChildren=False),
            entry("symbol:auth/login.py:refresh", "refresh", "symbol", signature="def refresh(session_id, sessions)", hasChildren=False)]),
        "billing": navigation(entry("dir:billing", "billing", "dir"), [
            entry("file:billing/invoices.py", "invoices.py", "file", hasChildren=True),
            entry("file:billing/payments.py", "payments.py", "file", hasChildren=True),
            entry("file:billing/taxes.py", "taxes.py", "file", hasChildren=True)]),
        "invoices": navigation(entry("file:billing/invoices.py", "invoices.py", "file"), [
            entry("symbol:billing/invoices.py:create_invoice", "create_invoice", "symbol", signature="def create_invoice(session, cart, repository)", hasChildren=False),
            entry("symbol:billing/invoices.py:refund", "refund", "symbol", signature="def refund(invoice_id, repository)", hasChildren=False)]),
        "login_source": source("auth/login.py", "login", "def login(credentials, sessions):\n    identity = validate_credentials(credentials)\n    if identity is None:\n        raise InvalidCredentials()\n    return sessions.create(user_id=identity.id)"),
        "invoice_source": source("billing/invoices.py", "create_invoice", "def create_invoice(session, cart, repository):\n    user_id = session.require_user()\n    if not cart.items:\n        raise EmptyCart()\n    return repository.create(owner_id=user_id, items=cart.items)"),
    }


def scripted_journey(directory: Path, selected_class: Any) -> dict[str, Any]:
    """Explicit auditable decisions, including live navigation after restoration.

    Checkpoints are diagnostic views, not a simulated number of model calls.
    Selector decisions are supplied by this fixture and have no measured model cost.
    """
    directory.mkdir(parents=True)
    fixture = journey_observations()
    working = WorkingState(directory / "working-archive", "journey-session")
    selected = selected_class(directory / "selected-archive", "journey-session")
    for state in (working, selected):
        state.task(fixture["task"])
    history = [{"role": "user", "content": fixture["task"]}]
    delivered: set[tuple[str, str, str]] = set()
    snapshots, decisions, checks = [], [], []
    handles: dict[str, dict[str, str]] = {}
    exposure = NavigationExposure()
    previous: dict[str, str] = {}

    def checkpoint(label: str) -> None:
        messages = deepcopy({"full_history": history, "working_set": working.render(),
                             "selector": selected.render(), "reasoner": selected.render(role="reasoner")})
        validations = {"working_set": verify_state(working, messages["working_set"], fixture["task"], delivered),
                       "selector": verify_state(selected, messages["selector"], fixture["task"], delivered),
                       "reasoner": verify_state(selected, messages["reasoner"], fixture["task"], delivered)}
        prompts = {name: serialize(value) for name, value in messages.items()}
        snapshots.append({"checkpoint": label, "messages": messages, "prompts": prompts,
            "prompt_bytes": {name: size(value) for name, value in prompts.items()},
            "common_prefix_bytes": {name: common_prefix(previous[name], value)
                                    for name, value in prompts.items() if name in previous},
            "validation": validations, "retention_candidates": selected.retention_candidates(),
            "navigation_exposure": dict(exposure.counts)})
        previous.update(prompts)

    def observe(label: str, action: str, inputs: dict[str, Any], observation: str) -> None:
        decision = {"action": action, "answer": "", **inputs}
        history.append({"role": "assistant", "content": json.dumps(decision)})
        history.append({"role": "user", "content": f"Tool result for {action}:\n{observation}"})
        record = {"action": action, "input": decision, "observation": observation,
                  "route": {"backend": "scripted_fixture"}, "error": None}
        exposure.observe(record)
        delivered.update(delivered_views(record))
        handles[label] = {}
        for name, state in (("working", working), ("selected", selected)):
            state.assistant(decision)
            handles[label][name] = state.observe(action, decision, observation, route=record["route"])
        checkpoint(label)

    def retain(label: str, keep: list[str], archive: list[str], focus: str, reason: str) -> None:
        decision = {"retain_handles": [handles[name]["selected"] for name in keep],
                    "archive_handles": [handles[name]["selected"] for name in archive],
                    "focus_id": focus, "reason": reason}
        before = selected.resident_hashes()
        result = selected.apply_retention(decision)
        nonroot_keep = [handles[name]["selected"] for name in keep if name not in ("root", "root_live")]
        if not set(nonroot_keep) <= set(result["retained_handles"]):
            raise AssertionError("Explicitly retained navigation missing from selection result")
        if set(decision["archive_handles"]) & set(result["retained_handles"]):
            raise AssertionError("Explicitly archived navigation is still selected")
        rendered = payload(selected.render(role="reasoner"))
        selected_raw = {entry["handle"]: entry["observation"]
                        for entry in rendered["navigation"]["selected_observations"]}
        latest_raw = selected.render(role="reasoner")[-1]["content"]
        for name in keep:
            handle = handles[name]["selected"]
            expected = fixture["root" if name == "root_live" else "login" if name == "login_live" else name]
            if selected_raw.get(handle) != expected and expected not in latest_raw:
                raise AssertionError("Retained navigation is not exactly visible in reasoner prompt")
        if selected.resident_hashes() != before:
            raise AssertionError("Navigation retention modified exact source authorization")
        decisions.append({"checkpoint": label, "origin": "scripted_explicit_selection", "decision": decision,
                          "result": result})
        checks.append(label + ": exact selected navigation visible; source authority unchanged")
        checkpoint(label)

    def restore(label: str, origin: str, expected: str) -> None:
        before = selected.resident_hashes()
        for name, state in (("working", working), ("selected", selected)):
            state.assistant({"action": "restore", "handle": handles[origin][name], "answer": ""})
            if state.restore(handles[origin][name]) != expected:
                raise AssertionError("Archived navigation did not restore byte-identically")
        if selected.resident_hashes() != before:
            raise AssertionError("Navigation restoration changed source authorization")
        history.append({"role": "assistant", "content": json.dumps({"action": "restore", "handle": handles[origin]["selected"]})})
        history.append({"role": "user", "content": f"Historical navigation restored:\n{expected}"})
        checks.append(label + ": exact restoration without new source authority")
        checkpoint(label)

    checkpoint("initial")
    observe("root", "map", {"node": "repo"}, fixture["root"])
    observe("auth", "map", {"node": "dir:auth"}, fixture["auth"])
    retain("choose_auth", ["auth"], [], "dir:auth", "Inspect auth now; preserve the protected root index for the unresolved billing branch.")
    observe("login", "map", {"node": "file:auth/login.py"}, fixture["login"])
    retain("choose_login", ["login"], ["auth"], "file:auth/login.py", "The live login member map is sufficient for the next exact read; keep auth sibling recovery available.")
    observe("login_read", "source", {"node": "symbol:auth/login.py:login", "mode": "full"}, fixture["login_source"])
    restore("backtrack_root", "root", fixture["root"])
    observe("root_live", "map", {"node": "repo"}, fixture["root"])
    retain("switch_to_billing", ["root_live"], ["login"], "dir:billing", "Auth source is retained. Resolve the second required branch using the refreshed root map.")
    observe("billing", "map", {"node": "dir:billing"}, fixture["billing"])
    retain("choose_billing", ["billing"], [], "dir:billing", "Select the live billing frontier while retaining exact auth code and the protected root index.")
    observe("invoices", "map", {"node": "file:billing/invoices.py"}, fixture["invoices"])
    retain("choose_invoice", ["invoices"], ["billing"], "file:billing/invoices.py", "Read invoice ownership assignment; sibling billing pages remain recoverable.")
    observe("invoice_read", "source", {"node": "symbol:billing/invoices.py:create_invoice", "mode": "full"}, fixture["invoice_source"])
    restore("backtrack_login", "login", fixture["login"])
    observe("login_live", "map", {"node": "file:auth/login.py"}, fixture["login"])
    retain("reselect_login", ["login_live"], ["invoices"], "symbol:auth/login.py:login", "Cross-check session creation against retained invoice code; no source is discarded.")
    if len(selected.resident_hashes()) != 2:
        raise AssertionError("The two exact source blocks did not survive branch selection")
    checks.append("both exact source blocks retained across branch switches")
    for state in (working, selected):
        state.reset("journey-fresh-session", fixture["task"])
    delivered.clear()
    history[:] = [{"role": "user", "content": fixture["task"]}]
    previous.clear()
    if selected.resident_hashes() or working.resident_hashes():
        raise AssertionError("Fresh context inherited source authorization")
    checkpoint("fresh_context")
    restore("fresh_restore_login", "login", fixture["login"])
    if selected.resident_hashes() or source_views(selected.render(role="reasoner")):
        raise AssertionError("Historical navigation resurrected source after reset")
    checks.append("fresh-context restoration grants no source authority")
    (directory / "snapshots.json").write_text(json.dumps(snapshots, indent=2, ensure_ascii=False) + "\n")
    (directory / "explicit-decisions.json").write_text(json.dumps(decisions, indent=2) + "\n")
    example = {item["checkpoint"]: item for item in snapshots
               if item["checkpoint"] in ("login_read", "root_live", "switch_to_billing", "invoice_read", "reselect_login")}
    (directory / "before-after-prompts.json").write_text(json.dumps(example, indent=2, ensure_ascii=False) + "\n")
    return {"kind": "synthetic_two_branch_journey", "selector_decisions": len(decisions),
            "diagnostic_checkpoints": len(snapshots), "checks": checks, "navigation_exposure": dict(exposure.counts),
            "per_checkpoint": [{key: value for key, value in item.items()
                                if key in ("checkpoint", "prompt_bytes", "common_prefix_bytes")}
                               for item in snapshots],
            "snapshots": str(directory / "snapshots.json"), "decisions": str(directory / "explicit-decisions.json"),
            "before_after_prompts": str(directory / "before-after-prompts.json")}


def markdown_report(report: dict[str, Any]) -> str:
    journey = report["scripted_journey"]
    lines = ["# Explicit navigation selection: offline fixture", "",
        "This is a scripted state-management check with **zero model calls**. It does not measure answer quality, token savings, cost, or latency.", "",
        "## Method", "",
        "The same synthetic login/auth and billing/invoice observations are delivered to full history, the existing WorkingState, and SelectedWorkingState. Six declared navigation choices are applied only to the selected state. They are fixture instructions, not sampled selector outputs. Traversal alone makes no selection decision.", "",
        "Each checkpoint saves the complete messages and the exact serialized stdin prompt. Bytes are UTF-8, including `Continue this explicit conversation transcript:` and JSON escaping. Static instructions, schemas, provider framing, and selector inference cost are excluded. Selector and reasoner columns are separate diagnostic views; neither their sum nor a difference is a measured call schedule.", "",
        "## Prompt checkpoints", "",
        "| Checkpoint | Full history bytes | Working set bytes | Selector bytes | Reasoner bytes |",
        "|---|---:|---:|---:|---:|"]
    for item in journey["per_checkpoint"]:
        b = item["prompt_bytes"]
        lines.append(f"| {item['checkpoint']} | {b['full_history']:,} | {b['working_set']:,} | {b['selector']:,} | {b['reasoner']:,} |")
    lines += ["", "## Retention and recovery checks", ""]
    lines += [f"- {check}." for check in journey["checks"]]
    lines += ["", "Every state snapshot asserts that the initial task is intact and each source hash authorized for conditional reuse has exact source bytes in that view and was delivered during that context. Both source blocks survive navigation switches. Fresh-session restoration returns navigation only and grants no source authority.", "",
        "The root index preserves observed sibling pointers. Backtracking restores exact archived map output, then obtains a fresh synthetic map before a new retention choice. Restored output is not accepted as fresh evidence. The fixture does not prove that a real selector chooses sufficient context or that a live filesystem stays unchanged.", "",
        "## Prefix measurements", "",
        "Adjacent common prefixes are compared byte by byte within this scripted context. The pair crossing reset is excluded. Full history is append-only here, while working views change serialized fields. These are text-layout measurements; they do not establish server caching behavior or token counts.", "",
        "| View | Adjacent pairs | Median prefix bytes | Minimum | Maximum |",
        "|---|---:|---:|---:|---:|"]
    for name in ("full_history", "working_set", "selector", "reasoner"):
        values = [item["common_prefix_bytes"][name] for item in journey["per_checkpoint"]
                  if name in item["common_prefix_bytes"]]
        lines.append(f"| {name} | {len(values)} | {median(values):g} | {min(values)} | {max(values)} |")
    lines += ["", "## Scope and artifacts", "",
        f"- {journey['diagnostic_checkpoints']} checkpoints and {journey['selector_decisions']} explicit selection decisions; observations and decisions are synthetic.",
        "- Exact before/after examples: `scripted-journey/before-after-prompts.json`.",
        "- All exact prompts: `scripted-journey/snapshots.json`; auditable decisions: `scripted-journey/explicit-decisions.json`.",
        "- Replay measurements and implementation fingerprints: `report.json`.",
        "- Reproduce with `PYTHONPATH=eval python3 eval/replay_branch_state.py --out <new-directory>`.",
        "- The optional `--matrix` path replays historical observations without inventing selector decisions. It is not part of this fixture run.",
        "- The new live pilot uses a distinct single-phase repair protocol; historical matrix outcomes must not be pooled with it.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path,
                        help="Optional saved matrix replay; historical traces contain no retention selections")
    parser.add_argument("--out", type=Path, default=ROOT / "eval/results/branch-compaction-2026-10-19/offline")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Choose a new output directory; replay evidence is never overwritten")
    from branch_state import SelectedWorkingState
    args.out.mkdir(parents=True)
    results, journals = [], {}
    for variant in VARIANTS if args.matrix else ():
        path = args.matrix / variant / "runs.jsonl"
        data = path.read_bytes()
        journals[variant] = {"path": str(path.resolve()), "sha256": sha256(data).hexdigest()}
        for row in (json.loads(line) for line in data.splitlines() if line.strip()):
            directory = args.out / variant / row["attempt_id"]
            results.append({"variant": variant, **replay_attempt(row, directory, SelectedWorkingState)})
    journey = scripted_journey(args.out / "scripted-journey", SelectedWorkingState)
    report = {"kind": "offline_selected_context_replay", "journals": journals, "scripted_journey": journey,
        "implementation_sha256": {name: sha256((ROOT / "eval" / name).read_bytes()).hexdigest()
                                  for name in ("working_state.py", "branch_state.py", "replay_branch_state.py")},
        "measurement": "UTF-8 bytes of exact serialized explicit transcripts, including fixed stdin prefix",
        "excludes": ["static model instructions", "response schemas", "provider framing", "tokens", "cost", "quality"],
        "method": ["Every saved attempt is retained, including runner errors and turn limits.",
                   "Replay the same tasks, decisions and tool observations; execute no repository tools or models.",
                   "Hydration is a pre-decision observation; handoff and early-action feedback follow the saved lifecycle.",
                   "No historical selector decisions exist. Selected state receives NO inferred retention decisions.",
                   "Reasoner view at each snapshot is diagnostic, not an observed schedule or an extra free model call.",
                   "Archived original inputs must exactly match reconstruction under their original context policy.",
                   "Source authorization requires exact evidence in the rendered view and prior delivery in this context.",
                   "A recorded conditional read that becomes unauthorized is reported, never silently rehydrated."],
        "all_attempts": aggregate(results), "graded_attempts": aggregate([r for r in results if r["original_status"] in GRADED]),
        "by_variant": {variant: aggregate([r for r in results if r["variant"] == variant]) for variant in VARIANTS},
        "results": results}
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / "REPORT.md").write_text(markdown_report(report))
    print(json.dumps({"report": str(args.out / "report.json"), "summary": report["all_attempts"],
                      "fixture": {key: journey[key] for key in ("diagnostic_checkpoints", "selector_decisions", "navigation_exposure", "checks")}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
