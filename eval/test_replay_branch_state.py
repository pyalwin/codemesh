"""Offline replay checks. No repository tools or model calls are executed."""

from hashlib import sha256
import json

import pytest

from branch_state import SelectedWorkingState
from replay_branch_state import (
    NavigationExposure, common_prefix, decision_action, delivered_views,
    journey_observations, payload, replay_attempt, scripted_journey, serialize,
    source_views, verify_state,
)
from working_state import WorkingState


def test_prefix_counts_utf8_bytes_not_characters():
    assert common_prefix("🙂a", "🙂b") == 4
    assert common_prefix("abc", "ab") == 2


def test_action_mapping_skips_automatic_hydration_and_preserves_errors():
    source = {"action": "source"}
    row = {"phases": [{"phase": 0, "actions": [{"action": "hydrate"}, source, {"action": "answer"}]}]}
    call = {"kind": "decision", "phase": 0, "turn": 1}
    assert decision_action(row, call) is source
    assert decision_action(row, {**call, "error": "runner failed"}) is None
    assert decision_action(row, {**call, "kind": "handoff"}) is None


def test_source_validation_requires_delivered_bytes_and_current_visibility(tmp_path):
    fixture = journey_observations()
    source = json.loads(fixture["login_source"])
    state = WorkingState(tmp_path / "archive", "s")
    state.task(fixture["task"])
    record = {"action": "source", "input": {"node": source["id"]}, "observation": fixture["login_source"]}
    state.observe("source", record["input"], record["observation"])
    delivered = delivered_views(record)
    assert len(delivered) == 1
    assert verify_state(state, state.render(), fixture["task"], delivered)["resident_sources_visible"]
    with pytest.raises(AssertionError, match="not delivered"):
        verify_state(state, state.render(), fixture["task"], set())
    without_source = state.render()[:-1]
    with pytest.raises(AssertionError, match="exact rendered bytes"):
        verify_state(state, without_source, fixture["task"], delivered)
    modified = {**source, "source": "corrupted"}
    assert not source_views([{"role": "user", "content": json.dumps(modified)}])
    state.reset("fresh", fixture["task"])
    assert not state.resident_hashes()


def test_navigation_exposure_requires_observed_contains_edges():
    fixture = journey_observations()
    exposure = NavigationExposure()
    for key, node in (("root", "repo"), ("auth", "dir:auth"), ("login", "file:auth/login.py"),
                      ("root", "repo"), ("billing", "dir:billing"), ("invoices", "file:billing/invoices.py"),
                      ("login", "file:auth/login.py")):
        exposure.observe({"action": "map", "input": {"node": node}, "observation": fixture[key]})
    assert exposure.counts["map_actions"] == 7
    assert exposure.counts["observed_descents"] == 4
    assert exposure.counts["observed_backtracks"] == 1
    assert exposure.counts["unrelated_or_unproven_transitions"] == 1


def test_saved_replay_preserves_hydration_handoff_error_and_fresh_context(tmp_path):
    fixture = journey_observations()
    task, patch = fixture["task"], "Apply the verified change. Preserve public behavior."
    source = json.loads(fixture["login_source"])
    hydration = json.dumps({"sources": [source]})
    read = {"action": "source", "node": source["id"], "mode": "full", "answer": ""}
    answer = {"action": "answer", "answer": "Identity comes from login."}
    row = {"task": "fixture", "mode": "codemesh", "repeat": 1, "attempt_id": "saved-failure",
           "status": "model_error", "final_patch_pass": False,
           "phases": [{"phase": 0, "prompt": task, "actions": [
               {"action": "hydrate", "input": {}, "observation": hydration},
               {"action": "source", "input": read, "observation": fixture["login_source"]},
               {"action": "answer", "input": answer, "observation": ""}]},
               {"phase": 2, "prompt": patch, "actions": []}],
           "calls": [
               {"kind": "decision", "phase": 0, "turn": 1, "context_session_id": "s1",
                "message": task + "\nHydrated source:\n" + hydration, "decision": read, "text": json.dumps(read)},
               {"kind": "decision", "phase": 0, "turn": 2, "context_session_id": "s1",
                "message": "Tool result for source:\n" + fixture["login_source"], "decision": answer, "text": json.dumps(answer)},
               {"kind": "handoff", "phase": 0, "turn": None, "context_session_id": "s1",
                "message": "Write a factual handoff.", "decision": None, "text": "Read login again before editing."},
               {"kind": "decision", "phase": 2, "turn": 1, "context_session_id": "s2",
                "message": patch, "error": "Scripted transport failure", "text": ""}]}
    history, session = [], None
    for index, call in enumerate(row["calls"]):
        if call["context_session_id"] != session:
            history, session = [], call["context_session_id"]
        history.append({"role": "user", "content": call["message"]})
        raw = serialize(history).encode()
        path = tmp_path / f"stdin-{index}.txt"
        path.write_bytes(raw)
        call["input_artifacts"] = {"files": {"stdin.txt": {"path": str(path), "sha256": sha256(raw).hexdigest()}}}
        if not call.get("error"):
            history.append({"role": "assistant", "content": call["text"]})
    result = replay_attempt(row, tmp_path / "replay", SelectedWorkingState)
    assert result["model_calls_replayed"] == 4
    assert result["original_input_matches"] == 4
    assert result["actions"]["hydrate"] == 1
    assert result["actions"]["model_error_calls_preserved"] == 1
    snapshots = [json.loads(line) for line in (tmp_path / "replay/snapshots.jsonl").read_text().splitlines()]
    assert snapshots[-1]["original_error"] == "Scripted transport failure"
    assert snapshots[-1]["common_prefix_bytes"] == {}
    assert snapshots[-1]["validation"]["working_set"]["resident_hashes"] == []
    assert "Write a factual handoff." in snapshots[2]["prompts"]["reasoner_diagnostic"]


def test_scripted_journey_saves_exact_stable_snapshots_and_explicit_selections(tmp_path):
    result = scripted_journey(tmp_path / "fixture", SelectedWorkingState)
    snapshots = json.loads((tmp_path / "fixture/snapshots.json").read_text())
    decisions = json.loads((tmp_path / "fixture/explicit-decisions.json").read_text())
    assert result["selector_decisions"] == 6 == len(decisions)
    assert result["diagnostic_checkpoints"] == len(snapshots)
    assert snapshots[0]["messages"]["full_history"] == [
        {"role": "user", "content": journey_observations()["task"]}]
    for snapshot in snapshots:
        for name, messages in snapshot["messages"].items():
            assert serialize(messages) == snapshot["prompts"][name]
            assert len(snapshot["prompts"][name].encode()) == snapshot["prompt_bytes"][name]
    by_name = {snapshot["checkpoint"]: snapshot for snapshot in snapshots}
    assert len(by_name["reselect_login"]["validation"]["reasoner"]["resident_hashes"]) == 2
    assert by_name["fresh_context"]["common_prefix_bytes"] == {}
    assert by_name["fresh_restore_login"]["validation"]["reasoner"]["resident_hashes"] == []
    assert by_name["fresh_restore_login"]["validation"]["reasoner"]["visible_source_views"] == []
    for snapshot in snapshots[1:]:
        if snapshot["checkpoint"] == "fresh_context":
            break
        entries = payload(snapshot["messages"]["reasoner"])["navigation"]["main_index"]["entries"]
        assert {"dir:auth", "dir:billing"} <= {entry["id"] for entry in entries}
    example = json.loads((tmp_path / "fixture/before-after-prompts.json").read_text())
    assert "root_live" in example and "switch_to_billing" in example
    assert len(example) == 5
