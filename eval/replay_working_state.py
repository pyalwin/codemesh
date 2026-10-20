#!/usr/bin/env python3
"""Replay saved actions through WorkingState without invoking tools or models.

Measures the serialized transcript passed to Codex on stdin. Current static
instructions and output schemas are measured separately. Hidden provider
framing and token counts are unavailable. Observations and decisions stay fixed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from working_state import WorkingState


ROOT = Path(__file__).resolve().parent.parent
PROMPT_PREFIX = "Continue this explicit conversation transcript:\n"


def serialized_prompt(history: list[dict[str, str]]) -> str:
    # Identical to codex_runner.model_call's stdin serialization.
    return PROMPT_PREFIX + json.dumps(history, ensure_ascii=False)


def byte_count(text: str) -> int:
    return len(text.encode("utf-8"))


def rendered_state(history: list[dict[str, str]]) -> dict[str, Any]:
    return json.loads(history[0]["content"].split("\n", 1)[1])


def rendered_source_views(history: list[dict[str, str]]) -> set[tuple[str, str, str]]:
    """Find complete source payloads actually present in the rendered messages."""
    state = rendered_state(history)
    observations = [entry["observation"] for entry in state["retained_evidence"]
                    if entry["action"] == "source"]
    for item in history:
        if item["content"].startswith("Latest tool observation "):
            observation = item["content"].split(":\n", 1)[1]
            observations.append(observation.rsplit("\nChoose the next harness action", 1)[0])
    result = set()
    for observation in observations:
        view = delivered_view(observation, {})
        if view:
            result.update(view[0])
    return result


def action_for(row: dict[str, Any], call: dict[str, Any]) -> dict[str, Any] | None:
    if call["kind"] != "decision":
        return None
    phase = next(phase for phase in row["phases"] if phase["phase"] == call["phase"])
    turn = call["turn"]
    if turn > len(phase["actions"]):
        raise ValueError("Saved decision has no corresponding recorded action")
    return phase["actions"][turn - 1]


def delivered_view(observation: str, decision: dict[str, Any]) -> tuple[set[tuple[str, str, str]], str] | None:
    try:
        view = json.loads(observation)
        source = view.get("source")
        source_hash = view.get("hash") or view.get("sourceHash")
        if (view.get("status") != "fresh" or view.get("mode") == "fold"
                or not isinstance(source, str) or sha256(source.encode()).hexdigest() != source_hash):
            return None
        ids = {view["id"], decision.get("node", view["id"])}
        return {(node, view["mode"], source_hash) for node in ids}, source
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def replay(row: dict[str, Any], directory: Path) -> dict[str, Any]:
    mode = row["mode"]
    calls = row["calls"]
    if (row.get("status") != "complete" or not calls
            or any(call.get("error") or call.get("call_valid") is False
                   or call.get("router_error_count", 0) for call in calls)):
        raise ValueError("Replay expects a completed clean trajectory")
    state = WorkingState(directory / "archive", calls[0]["context_session_id"])
    history: list[dict[str, str]] = []
    snapshots = []
    session = None
    phase_number = None
    delivered: set[tuple[str, str, str]] = set()
    observations = Counter()
    reset_checks = 0
    hash_checks = 0
    task_checks = 0
    edit_checks = 0
    ledger_checks = 0
    initial_task = ""
    successful_edit = None

    for index, call in enumerate(calls):
        context = call["context_session_id"]
        if context != session:
            state.reset(context)
            if state.resident_hashes():
                raise AssertionError("Fresh context retained source residency")
            reset_checks += int(session is not None)
            session = context
            history = []
            delivered.clear()
            phase_number = None
            initial_task = ""
        history.append({"role": "user", "content": call["message"]})
        if call["kind"] == "decision" and phase_number != call["phase"]:
            state.task(call["message"])
            phase_number = call["phase"]
            if not initial_task:
                initial_task = call["message"]

        working_history = state.render()
        if working_history != state.render():
            raise AssertionError("Rendering changed active state")
        active = rendered_state(working_history)
        stats = state.context_stats()
        if active["initial_task"] != initial_task:
            raise AssertionError("The session task disappeared from working state")
        if len(active["navigation_ledger"]) != stats["ledger_count"] or stats["ledger_count"] > 8:
            raise AssertionError("The rendered navigation ledger does not match bounded state")
        for entry in active["navigation_ledger"]:
            if not (directory / "archive" / (entry["handle"] + ".json")).is_file():
                raise AssertionError("A navigation pointer has no observation archive")
        ledger_checks += 1
        if call["kind"] in ("handoff", "handoff_retry"):
            working_history = [*working_history, {"role": "user", "content": call["message"]}]
        baseline = serialized_prompt(history)
        working = serialized_prompt(working_history)
        task_checks += 1
        residents = state.resident_hashes()
        if not residents <= delivered:
            raise AssertionError("Undelivered source acquired residency")
        visible_views = {(mode, digest) for _, mode, digest in rendered_source_views(working_history)}
        if any((mode, digest) not in visible_views for _, mode, digest in residents):
            raise AssertionError("A resident hash has no exact source payload in the prompt")
        hash_checks += 1
        snapshots.append({
            "call_index": index + 1, "phase": call["phase"], "kind": call["kind"],
            "turn": call.get("turn"), "action": (call.get("decision") or {}).get("action"),
            "context_session_id": context,
            "baseline_prompt_bytes": byte_count(baseline), "working_prompt_bytes": byte_count(working),
            "baseline_prompt": baseline, "working_prompt": working,
            "resident_hashes": sorted(residents), "state": state.context_stats(),
        })

        # The old protocol retained the exact provider text, including nullable
        # transport fields. WorkingState keeps its canonical decision instead.
        history.append({"role": "assistant", "content": call["text"]})
        decision = call.get("decision")
        state.assistant(decision if decision is not None else call["text"])
        if not state.resident_hashes() <= delivered:
            raise AssertionError("Consuming an observation invented source residency")
        action = action_for(row, call)
        if action is None or action["action"] == "answer":
            continue
        if action["action"] in ("early_action", "premature_answer"):
            if index + 1 >= len(calls):
                raise ValueError("Control rejection has no saved feedback message")
            feedback = calls[index + 1]["message"]
            state.observe("control", decision or {}, feedback, error=feedback, route={"backend": "harness"})
            continue
        observation = action["observation"]
        observations[action["action"]] += 1
        state.observe(action["action"], action.get("input") or decision or {}, observation,
                      error=action.get("error"), route=action.get("route"))
        if action["action"] == "source" and not action.get("error"):
            view = delivered_view(observation, action.get("input") or {})
            if view:
                delivered.update(view[0])
        if action["action"] == "edit" and not action.get("error"):
            path = action["input"]["path"]
            # observe(edit) performs invalidation; calling invalidate again would
            # invent another source revision absent from the saved trajectory.
            if any(node.startswith(f"symbol:{path}:") for node, _, _ in state.resident_hashes()):
                raise AssertionError("Edited file retained valid source residency")
            edit_checks += 1
            successful_edit = action["input"]

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "snapshots.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in snapshots))
    final_state = state.render()
    final_payload = rendered_state(final_state)
    if final_payload.get("latest_test", {}).get("passed") is not True:
        raise AssertionError("The saved passing test outcome was lost after its observation was consumed")
    edits = final_payload.get("successful_edits", {}).get("recent", [])
    if not successful_edit or not any(all(entry.get(key) == successful_edit.get(key)
                                           for key in ("path", "old", "new")) for entry in edits):
        raise AssertionError("The successful patch was lost after later observations")
    (directory / "final-state.json").write_text(json.dumps(final_state, indent=2, ensure_ascii=False) + "\n")
    baseline_bytes = sum(item["baseline_prompt_bytes"] for item in snapshots)
    working_bytes = sum(item["working_prompt_bytes"] for item in snapshots)
    return {
        "mode": mode, "calls": len(calls), "observed_actions": dict(observations),
        "baseline_transcript_bytes": baseline_bytes, "working_transcript_bytes": working_bytes,
        "transcript_byte_reduction_percent": 100 * (1 - working_bytes / baseline_bytes),
        "baseline_peak_bytes": max(item["baseline_prompt_bytes"] for item in snapshots),
        "working_peak_bytes": max(item["working_prompt_bytes"] for item in snapshots),
        "validation": {"task_snapshots_checked": task_checks, "source_residency_snapshots_checked": hash_checks,
                       "archive_backed_ledger_snapshots_checked": ledger_checks, "final_test_retained": True,
                       "successful_patch_retained": True,
                       "fresh_context_resets_checked": reset_checks, "edited_file_invalidations_checked": edit_checks},
        "snapshot_path": str(directory / "snapshots.jsonl"), "final_state": state.context_stats(),
        "per_call": [{key: value for key, value in item.items()
                      if key not in ("baseline_prompt", "working_prompt", "state", "resident_hashes")}
                     for item in snapshots],
    }


def guard_probes(directory: Path) -> dict[str, Any]:
    """Exercise eviction and recovery paths absent from the saved trajectories."""
    checked = []

    def source(name: str) -> tuple[dict[str, str], str, tuple[str, str, str]]:
        node = f"symbol:pydantic/{name}.py:chosen"
        text = f"def chosen():\n    return '{name}'"
        digest = sha256(text.encode()).hexdigest()
        observation = json.dumps({"id": node, "path": f"pydantic/{name}.py", "lines": [1, 2],
                                  "status": "fresh", "mode": "full", "hash": digest, "source": text},
                                 separators=(",", ":"))
        return {"node": node, "mode": "full"}, observation, (node, "full", digest)

    inputs_a, observation_a, key_a = source("a")
    inputs_b, observation_b, key_b = source("b")
    sizing = WorkingState(directory / "sizing", "sizing")
    sizing.observe("source", inputs_a, observation_a)
    one_view_budget = sizing.context_stats()["evidence_bytes"]
    state = WorkingState(directory / "guards", "first-session", evidence_bytes=one_view_budget)
    state.task("Repair the seeded regression; preserve unrelated behavior.")
    state.task("Now inspect the relevant callers.")
    payload = rendered_state(state.render())
    assert payload["initial_task"] == "Repair the seeded regression; preserve unrelated behavior."
    assert payload["current_phase"] == "Now inspect the relevant callers."
    checked.append("initial_task_and_current_phase_retained")

    first_handle = state.observe("source", inputs_a, observation_a)
    state.assistant({"action": "source", **inputs_b, "answer": ""})
    assert key_a in state.resident_hashes()
    assert key_a in rendered_source_views(state.render())
    state.observe("source", inputs_b, observation_b)
    state.assistant({"action": "answer", "answer": "Inspected the second source."})
    assert key_b in state.resident_hashes() and key_a not in state.resident_hashes()
    assert key_a not in rendered_source_views(state.render())
    assert state.context_stats()["evidence_bytes"] <= one_view_budget
    checked.append("evicted_source_loses_conditional_read_authority")

    try:
        state.restore(first_handle)
    except ValueError:
        pass
    else:
        raise AssertionError("Historical source was restored without a live read")
    assert key_a not in state.resident_hashes()
    checked.append("source_archive_cannot_reauthorize_evicted_bytes")

    state.observe("edit", {"path": "pydantic/b.py"}, "Edited pydantic/b.py.")
    assert not state.resident_hashes()
    state.assistant({"action": "test", "answer": ""})
    payload = rendered_state(state.render())
    assert any(entry.get("path") == "pydantic/b.py" and entry.get("stale")
               for entry in payload["navigation_ledger"])
    checked.append("edit_invalidates_evidence_and_marks_navigation_stale")

    navigation = json.dumps({"entries": [{"id": "file:pydantic/b.py", "name": "b.py"}],
                             "nextCursor": "next-page"}, separators=(",", ":"))
    nav_handle = state.observe("map", {"node": "dir:pydantic"}, navigation)
    state.assistant({"action": "answer", "answer": "Selected a file."})
    entry = next(item for item in rendered_state(state.render())["navigation_ledger"]
                 if item["handle"] == nav_handle)
    assert entry["pending_cursor"] == "next-page" and entry["candidate_ids"] == ["file:pydantic/b.py"]
    assert state.restore(nav_handle) == navigation
    assert not state.resident_hashes()
    checked.append("navigation_cursor_and_exact_archive_restoration_preserved")

    state.reset("fresh-session", "Resume with saved navigation pointers.")
    assert not state.resident_hashes()
    assert not rendered_state(state.render())["navigation_ledger"]
    assert state.restore(nav_handle) == navigation
    restored = rendered_state(state.render())["navigation_ledger"][0]
    assert restored["replayed"] is True and restored["stale"] is True
    assert not state.resident_hashes()
    checked.append("fresh_context_restoration_is_historical_navigation_only")

    state.observe("test", {}, "1 passed", route={"passed": True})
    state.assistant({"action": "answer", "answer": "The visible test passed."})
    assert rendered_state(state.render())["latest_test"]["current_revision"] is True
    state.observe("edit", {"path": "pydantic/a.py"}, "Edited pydantic/a.py.")
    state.assistant({"action": "answer", "answer": "A later edit needs another test."})
    assert rendered_state(state.render())["latest_test"]["current_revision"] is False
    checked.append("test_outcome_survives_consumption_but_not_later_revision")

    oversized = WorkingState(directory / "oversized", "small-budget", evidence_bytes=1)
    oversized.observe("source", inputs_a, observation_a)
    assert key_a in rendered_source_views(oversized.render())
    assert not oversized.resident_hashes()
    oversized.assistant({"action": "answer", "answer": "Read once."})
    assert key_a not in rendered_source_views(oversized.render())
    assert not oversized.resident_hashes()
    checked.append("oversized_source_displays_once_without_persistent_hash_authority")

    discovery = WorkingState(directory / "discovery", "archive-session")
    discovery.observe("source", inputs_a, observation_a)
    for index in range(9):
        discovery.observe("search", {"query": f"candidate_{index}"}, '{"results":[]}')
    discovery.reset("new-archive-session", "Recover a navigation branch.")
    assert rendered_state(discovery.render())["archive"]["root"] == "archive:0"
    index_page = json.loads(discovery.restore("archive:0"))
    assert len(index_page["entries"]) == 8 and index_page["next"] == "archive:8"
    source_pointer = index_page["entries"][0]
    assert source_pointer["restore"] == "live_read_required" and source_pointer["id"] == key_a[0]
    assert not ({"source", "hash", "sourceHash"} & source_pointer.keys())
    assert len(json.loads(discovery.restore(index_page["next"]))["entries"]) == 2
    assert not discovery.resident_hashes()
    checked.append("archive_index_discovers_paginated_pointers_without_source_authority")

    patches = WorkingState(directory / "patches", "patch-session")
    for index in range(5):
        patches.observe("edit", {"path": "pydantic/a.py", "old": f"before_{index}",
                                  "new": f"after_{index}"}, "Edited pydantic/a.py.")
        patches.assistant({"action": "test", "answer": ""})
    patches.observe("test", {}, "1 passed", route={"passed": True})
    patches.assistant({"action": "answer", "answer": "The patch passed the visible test."})
    edits = rendered_state(patches.render())["successful_edits"]
    assert edits["total"] == 5 and edits["omitted_older"] == 1 and len(edits["recent"]) == 4
    assert [entry["new"] for entry in edits["recent"]] == [f"after_{index}" for index in range(1, 5)]
    assert rendered_state(patches.render())["latest_test"]["passed"] is True
    checked.append("bounded_patch_records_survive_later_test_and_answer")
    return {"passed": len(checked), "checks": checked}


def protocol_sizes(rows: list[dict[str, Any]], directory: Path) -> list[dict[str, Any]]:
    """Measure current local protocol files, not unarchived historical framing."""
    from codex_runner import PROTOCOL, strict_schema
    from vnext_agent import schema, system_prompt

    directory.mkdir(parents=True)
    result = []
    for row in rows:
        mode = row["mode"]
        calls = len(row["calls"])
        structured_calls = sum(call["kind"] == "decision" for call in row["calls"])
        policies = {}
        for policy in ("full", "working-set"):
            instructions = PROTOCOL + "\n\n" + system_prompt(mode, source_mode="auto", context_policy=policy)
            response_schema = json.dumps(strict_schema(json.loads(schema(mode, context_policy=policy))))
            stem = f"{mode}-{policy}"
            (directory / (stem + ".instructions.txt")).write_text(instructions)
            (directory / (stem + ".schema.json")).write_text(response_schema)
            policies[policy] = {
                "instruction_bytes_per_call": byte_count(instructions),
                "schema_bytes_per_structured_call": byte_count(response_schema),
                "cumulative_instruction_bytes": calls * byte_count(instructions),
                "cumulative_schema_bytes": structured_calls * byte_count(response_schema),
                "instructions_sha256": sha256(instructions.encode()).hexdigest(),
                "schema_sha256": sha256(response_schema.encode()).hexdigest(),
            }
        result.append({"mode": mode, "calls": calls, "structured_calls": structured_calls,
                       "policies": policies})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path,
                        default=ROOT / "eval/results/vnext-luna-reference-pilot/report.json")
    parser.add_argument("--out", type=Path, default=ROOT / "eval/results/vnext-working-state-replay")
    args = parser.parse_args()
    original = json.loads(args.report.read_text())
    rows = original["results"]
    if {row["mode"] for row in rows} != {"filesystem", "codemesh"}:
        parser.error("Expected both filesystem and Codemesh trajectories")
    if args.out.exists():
        parser.error("Choose a new --out; replay artifacts are never overwritten")
    args.out.mkdir(parents=True)
    results = [replay(row, args.out / f"{row['task']}-{row['mode']}-{row['repeat']}") for row in rows]
    probes = guard_probes(args.out / "guard-probes")
    protocols = protocol_sizes(rows, args.out / "protocol-inputs")
    result = {"kind": "offline_working_state_replay", "input_report": str(args.report.resolve()),
              "input_report_sha256": sha256(args.report.read_bytes()).hexdigest(),
              "working_state_sha256": sha256((ROOT / "eval/working_state.py").read_bytes()).hexdigest(),
              "vnext_agent_sha256": sha256((ROOT / "eval/vnext_agent.py").read_bytes()).hexdigest(),
              "codex_runner_sha256": sha256((ROOT / "eval/codex_runner.py").read_bytes()).hexdigest(),
              "measurement": "UTF-8 bytes of the serialized Codex stdin transcript, including its fixed prefix",
              "excludes": ["static instructions", "response schema", "provider framing", "tokens", "cost", "quality"],
              "protocol_measurement": "Current local instruction/schema file bytes, separately measured; "
                                      "historical files and provider framing are not reconstructed",
              "current_protocol_sizes": protocols,
              "guard_probes": probes, "results": results}
    (args.out / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"report": str(args.out / "report.json"), "guard_probes": probes,
                     "current_protocol_sizes": protocols, "results": [
        {key: value for key, value in row.items() if key not in ("per_call", "final_state")} for row in results]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
