"""Replay lifecycle checks; no model or repository tool execution."""

from hashlib import sha256
import json

import pytest

from replay_branch_state import journey_observations, payload, serialize
from replay_focused_state import ReplayPair, archived_history, replay_control, scripted_journey


def test_prefix_pairs_exclude_source_edit_test_and_reset(tmp_path):
    fixture = journey_observations()
    pair = ReplayPair(tmp_path / "pair", "s1", fixture["task"])
    pair.snapshot("initial")
    actions = [("map", {"node": "repo"}, fixture["root"]),
               ("source", {"node": "symbol:auth/login.py:login"}, fixture["login_source"]),
               ("search", {"query": "billing"}, '{"results":[]}'),
               ("test", {}, "1 passed")]
    for action, inputs, observation in actions:
        pair.assistant({"action": action, **inputs})
        pair.observe({"action": action, "input": inputs, "observation": observation})
        pair.snapshot(action)
    pair.reset("s2", fixture["task"])
    pair.snapshot("reset")
    assert [s["checkpoint"] for s in pair.snapshots if s["navigation_only_transition"]] == ["map", "search"]
    assert pair.snapshots[-1]["common_prefix_bytes"] == {}
    assert pair.snapshots[-1]["validation"]["states"]["focused"]["resident_hashes"] == []


def test_fingerprint_verification_rejects_changed_original_input(tmp_path):
    path = tmp_path / "stdin.txt"
    original = serialize([{"role": "user", "content": "Task"}]).encode()
    path.write_bytes(original)
    call = {"input_artifacts": {"files": {"stdin.txt": {"path": str(path),
            "bytes": len(original), "sha256": sha256(original).hexdigest()}}}}
    assert archived_history(call) == [{"role": "user", "content": "Task"}]
    path.write_bytes(original + b" ")
    with pytest.raises(AssertionError, match="fingerprint"):
        archived_history(call)


def test_saved_control_keeps_failed_call_and_original_input(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    fixture = journey_observations()
    item = {"action": {"action": "map", "node": "repo"}, "observation": fixture["root"],
            "error": None, "route": {"backend": "synthetic_test"}, "handle": None}
    history = [{"role": "user", "content": fixture["task"]},
               {"role": "user", "content": "Tool result:\n" + json.dumps(item)}]
    raw = serialize(history).encode()
    path = control / "stdin.txt"
    path.write_bytes(raw)
    call = {"call_index": 1, "error": "recorded provider failure", "input_artifacts": {
        "files": {"stdin.txt": {"path": str(path), "bytes": len(raw), "sha256": sha256(raw).hexdigest()}}}}
    (control / "calls.jsonl").write_text(json.dumps(call) + "\n")
    (control / "actions.jsonl").write_text(json.dumps(item) + "\n")
    (control / "result.json").write_text(json.dumps({"task": "scripted", "policy": "full", "status": "model_error"}))
    result = replay_control(control, tmp_path / "result")
    assert result["checkpoints"] == result["original_stdin_matches"] == 1
    snapshots = json.loads((tmp_path / "result/snapshots.json").read_text())
    assert snapshots[0]["metadata"]["original_error"] == "recorded provider failure"
    assert snapshots[0]["prompt_bytes"]["recorded_full"] == len(raw)


def test_two_branch_fixture_exact_source_navigation_recovery_and_mutation_checks(tmp_path):
    result = scripted_journey(tmp_path / "fixture")
    snapshots = json.loads((tmp_path / "fixture/snapshots.json").read_text())
    by_name = {item["checkpoint"]: item for item in snapshots}
    assert result["pre_reset_stats"]["focused"]["collapsed_requests"] == 3
    assert result["events"]["restore"] == 2
    assert result["pre_reset_stats"]["focused"]["pending_page_count"] == 0
    assert len(by_name["auth_repeat"]["validation"]["states"]["focused"]["resident_hashes"]) == 2
    assert len(by_name["edit"]["validation"]["states"]["focused"]["resident_hashes"]) == 1
    assert "Repeated navigation observation" in by_name["auth_repeat"]["messages"]["focused"][-1]["content"]
    assert "historical_navigation_only" in by_name["recover_collapsed_auth"]["messages"]["focused"][-1]["content"]
    assert by_name["historical_billing"]["validation"]["states"]["focused"]["resident_hashes"] == []
    state = payload(by_name["invoices_after_edit"]["messages"]["focused"])
    assert state["successful_edits"]["total"] == 1
    assert state["latest_test"]["passed"] is True
    assert state["latest_tool_error"]["action"] == "search"
    assert {"dir:auth", "dir:billing"} <= {entry["id"] for entry in state["navigation"]["main_index"]["entries"]}
    for snapshot in snapshots:
        for name, messages in snapshot["messages"].items():
            assert serialize(messages) == snapshot["prompts"][name]
            assert len(snapshot["prompts"][name].encode()) == snapshot["prompt_bytes"][name]
