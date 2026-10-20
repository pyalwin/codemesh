"""Harness integration checks; provider decisions are scripted, never live."""

import copy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import vnext_agent as runner
from working_state import WorkingState


def test_policy_exposes_recovery_in_both_arms_without_changing_full_schema():
    for mode in runner.MODES:
        full = json.loads(runner.schema(mode))
        compact = json.loads(runner.schema(mode, "working-set"))
        assert "restore" not in full["properties"]["action"]["enum"]
        assert "handle" not in full["properties"]
        assert "restore" in compact["properties"]["action"]["enum"]
        assert "handle" in compact["properties"]
        assert "restore(handle)" in runner.system_prompt(mode, context_policy="working-set")


def test_compact_provider_receives_state_and_handoff_instruction_but_audit_is_complete(tmp_path):
    snapshots = []
    state = WorkingState(tmp_path / "archive", "session")
    state.task("Find the failure. Preserve zero-argument factories.")
    state.observe("map", {"node": "repo"}, json.dumps({
        "node": {"id": "repo"}, "entries": [{"id": "dir:pydantic", "name": "pydantic",
                                                  "signature": "unique_obsolete_map_body"}]}))
    replies = iter([
        {"decision": {"action": "search", "query": "get_default", "answer": ""},
         "text": '{"action":"search","query":"get_default","path":null,"answer":""}'},
        {"decision": None, "text": "Check the wrapper's validated_data argument."},
    ])

    def provider(transcript, **kwargs):
        snapshots.append(copy.deepcopy(transcript))
        return {**next(replies), "cost_usd": None}

    history = []
    with patch.dict("sys.modules", {"codex_runner": SimpleNamespace(model_call=provider)}):
        first = runner.model_call("Full raw map message", "session", "codemesh", resume=False,
                                  structured=True, cwd=tmp_path, model="gpt-6-luna", budget=None,
                                  provider="codex", history=history, working_state=state)
        state.observe("search", {"query": "get_default"}, '{"results":[],"total":0}')
        runner.model_call("Write a factual handoff now.", "session", "codemesh", resume=True,
                          structured=False, cwd=tmp_path, model="gpt-6-luna", budget=None,
                          provider="codex", history=history, working_state=state)
    assert "unique_obsolete_map_body" in json.dumps(snapshots[0])
    assert "unique_obsolete_map_body" not in json.dumps(snapshots[1])
    assert snapshots[1][-1]["content"] == "Write a factual handoff now."
    assert "Preserve zero-argument factories" in json.dumps(snapshots[1])
    assert '"path":null' in history[1]["content"]  # Audit preserves verbatim output.
    assert first["prompt_context"]["policy"] == "working-set"
    assert len(history) == 4


def test_working_policy_refuses_provider_that_cannot_replace_history(tmp_path):
    with patch.object(runner, "worktree") as worktree:
        with pytest.raises(ValueError, match="explicit-transcript"):
            runner.trajectory(tmp_path, {}, "filesystem", 1, tmp_path, tmp_path / "unused",
                              Path("/unused/python"), "haiku", None, 4,
                              provider="claude", context_policy="working-set")
        worktree.assert_not_called()


def test_post_provider_state_failure_preserves_paid_usage_and_artifacts(tmp_path):
    state = WorkingState(tmp_path / "archive", "session")
    state.task("Investigate")
    row = {"decision": {"action": "answer", "answer": "Found it."},
           "text": '{"action":"answer","answer":"Found it."}',
           "input_tokens": 100, "output_tokens": 20,
           "input_artifacts": {"directory": str(tmp_path / "inputs")}}
    with patch.dict("sys.modules", {"codex_runner": SimpleNamespace(model_call=lambda *a, **kw: row)}), \
            patch.object(state, "assistant", side_effect=ValueError("state rejected output")):
        result = runner.model_call("Investigate", "session", "filesystem", resume=False,
                                   structured=True, cwd=tmp_path, model="gpt-6-luna", budget=None,
                                   provider="codex", history=[], working_state=state)
    assert result["input_tokens"] == 100 and result["output_tokens"] == 20
    assert result["input_artifacts"]["directory"] == str(tmp_path / "inputs")
    assert "after provider response" in result["error"]
    assert result["call_valid"] is False


def test_evicted_source_rehydration_is_distinct_from_resident_duplicates():
    def read(resident):
        return {"action": "read", "input": {"path": "pydantic/example.py", "start": 1, "limit": 1},
                "observation": "1: exact source", "observation_bytes": 15,
                "source_resident_before": resident}
    counts = runner.repeated_reads([{"phase": 0, "context_epoch": 0,
                                      "actions": [read(False), read(False), read(True)]}])
    assert counts["evicted_context_repeat_read_calls"] == 1
    assert counts["same_context_repeat_read_calls"] == 1
    assert counts["repeat_read_calls"] == 2 and counts["repeated_observation_bytes"] == 30


@pytest.mark.parametrize("action", ["read", "answer", "edit"])
def test_archive_failure_stops_trajectory_without_losing_model_usage(tmp_path, action):
    provider_calls = []
    def provider(*args, **kwargs):
        provider_calls.append(args)
        decision = {"action": action, "path": "pydantic/example.py", "start": 1, "limit": 1, "answer": ""}
        return {"decision": decision, "text": json.dumps(decision), "input_tokens": 123,
                "output_tokens": 12, "cost_usd": None}

    def worktree(base, path, task, index):
        (path / "pydantic").mkdir(parents=True)
        (path / "pydantic/example.py").write_text("value = 1\n")

    task = {"id": "archive_failure", "path": "pydantic/example.py", "symptom": "Wrong value.",
            "impact_question": "Check callers.", "hidden_test": "unused.py"}
    with patch.dict("sys.modules", {"codex_runner": SimpleNamespace(model_call=provider)}), \
            patch.object(runner, "worktree", side_effect=worktree), \
            patch.object(runner, "remove_worktree"), \
            patch.object(runner, "pytest", return_value={"passed": False}), \
            patch.object(WorkingState, "observe", side_effect=OSError("archive unavailable")):
        result = runner.trajectory(tmp_path, task, "filesystem", 1, tmp_path, tmp_path / "unused.db",
                                   Path("/unused/python"), "gpt-6-luna", None, 4,
                                   provider="codex", context_policy="working-set")
    assert len(provider_calls) == 1
    assert result["status"] == "context_state_error"
    assert result["totals"]["input_tokens"] == 123
    assert result["phases"][0]["context_error"] == "archive unavailable"
    if action == "read":
        assert "value = 1" in result["phases"][0]["actions"][0]["observation"]


def test_compact_trajectory_resets_hashes_and_runs_tools_from_scripted_decisions(tmp_path):
    snapshots, saved_notes, source_requests, artifact_paths = [], [], [], []
    symbol = "symbol:pydantic/fields.py:resolve_default"
    code = "def resolve_default():\n    return broken_value"
    body_hash = sha256(code.encode()).hexdigest()
    source = {"status": "fresh", "id": symbol, "path": "pydantic/fields.py", "lines": [1, 2],
              "hash": body_hash, "mode": "auto", "source": code}
    decisions = iter([
        {"action": "map", "node": "repo", "answer": ""},
        {"action": "source", "node": symbol, "answer": ""},
        {"action": "answer", "answer": "The wrapper discards data."},
        {"action": "answer", "answer": "Preserve no-argument factories."},
        "Re-read resolve_default and repair the returned value.",
        {"action": "source", "node": symbol, "if_hash": body_hash, "answer": ""},
        {"action": "source", "node": symbol, "answer": ""},
        {"action": "edit", "path": "pydantic/fields.py", "old": "broken_value", "new": "fixed_value", "answer": ""},
        {"action": "test", "answer": ""},
        {"action": "answer", "answer": "Repaired and tested."},
    ])

    def provider(transcript, **kwargs):
        snapshots.append(copy.deepcopy(transcript))
        artifact_paths.append(kwargs["artifact_dir"])
        result = next(decisions)
        return {"decision": result if isinstance(result, dict) else None,
                "text": json.dumps(result) if isinstance(result, dict) else result, "cost_usd": None}

    def worktree(base, path, task, index):
        (path / "pydantic").mkdir(parents=True)
        (path / "pydantic/fields.py").write_text(code + "\n")

    def cli(repo, *args):
        if args[:2] == ("explore", "map"):
            return {"node": {"id": "repo"}, "entries": [{"id": "dir:pydantic",
                    "signature": "unique_discarded_directory_listing"}]}, 1
        if args[:2] == ("explore", "source"):
            source_requests.append(args)
            return source, 1
        if args[0] == "remember":
            saved_notes.append({"summary": args[args.index("--summary") + 1], "status": "hypothesis"})
            return {"success": True}, 1
        if args[:2] == ("explore", "recall"):
            return {"notes": saved_notes}, 1
        raise AssertionError(args)

    task = {"id": "working", "path": "pydantic/fields.py", "symptom": "Wrong default.",
            "impact_question": "Preserve both factory variants.", "hidden_test": "unused.py"}
    with patch.dict("sys.modules", {"codex_runner": SimpleNamespace(model_call=provider)}), \
            patch.object(runner, "worktree", side_effect=worktree), \
            patch.object(runner, "remove_worktree"), \
            patch.object(runner, "pytest", return_value={"passed": True, "output": "1 passed"}), \
            patch.object(runner, "cli", side_effect=cli):
        result = runner.trajectory(tmp_path, task, "codemesh", 1, tmp_path, tmp_path / "unused.db",
                                   Path("/unused/python"), "gpt-6-luna", None, 8,
                                   provider="codex", context_policy="working-set")
    assert result["status"] == "complete"
    assert result["final_patch_pass"] and result["context_policy"] == "working-set"
    assert len(source_requests) == 2  # Saved hash is rejected before the CLI after reset.
    assert "unique_discarded_directory_listing" in json.dumps(snapshots[1])
    assert "unique_discarded_directory_listing" not in json.dumps(snapshots[2])
    assert code not in snapshots[5][0]["content"]
    assert "if_hash bytes are absent" in json.dumps(snapshots[6])
    assert len(artifact_paths) == len(set(artifact_paths)) == 10
    assert result["handoff_reference_count"] == 1
    archive = list((tmp_path / "observations").rglob("*.json"))
    assert archive
    assert "unique_discarded_directory_listing" in "".join(p.read_text() for p in archive)
