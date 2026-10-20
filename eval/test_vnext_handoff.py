import copy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import vnext_agent as runner
from vnext_agent import handoff_payload, load_handoff, restored_references, source_reference


SYMBOL = "symbol:pydantic/fields.py:FieldInfo.get_default@L901"
SOURCE = "    def get_default(self):\n        return unique_source_evidence"


def delivered_source(repo: Path, **changes) -> dict:
    path = repo / "pydantic/fields.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n" * 900 + SOURCE + "\n")
    return {"id": SYMBOL, "path": "pydantic/fields.py", "lines": [901, 902],
            "hash": sha256(SOURCE.encode()).hexdigest(), "mode": "full",
            "source": SOURCE, "status": "fresh", **changes}


def reference_for(repo: Path) -> dict:
    reference = source_reference(repo, json.dumps(delivered_source(repo)))
    assert reference is not None
    return reference


def test_handoff_is_restored_with_freshness() -> None:
    note = {"summary": "Check the alias generator.", "status": "hypothesis"}
    with patch("vnext_agent.cli", return_value=({"notes": [note]}, 1)) as call:
        restored = load_handoff(Path("/tmp/repo"), "pydantic/main.py", note["summary"])
    assert "Check the alias generator." in restored
    assert "hypothesis" in restored
    call.assert_called_once_with(Path("/tmp/repo"), "explore", "recall", "--path",
                                 "pydantic/main.py", "--compact")


def test_stale_handoff_does_not_supply_old_summary() -> None:
    note = {"summary": "Old claim", "status": "stale"}
    with patch("vnext_agent.cli", return_value=({"notes": [note]}, 1)):
        restored = load_handoff(Path("/tmp/repo"), "pydantic/main.py", note["summary"])
    assert "stale" in restored
    assert "Old claim" not in restored


def test_capture_preserves_canonical_reference_without_source(tmp_path: Path) -> None:
    observed = delivered_source(tmp_path)
    reference = source_reference(tmp_path, json.dumps(observed))
    assert reference == {key: observed[key] for key in ("id", "path", "lines", "hash", "mode")} | {
        "file_hash": sha256((tmp_path / observed["path"]).read_bytes()).hexdigest()}
    assert SOURCE not in json.dumps(reference)


@pytest.mark.parametrize("changes", [
    {"hash": "corrupted"},
    {"source": SOURCE + " changed after hashing"},
    {"mode": "fold"},
    {"status": "unchanged", "source": None},
    {"status": "stale", "source": None},
    {"status": "deleted", "source": None},
    {"status": "error", "source": None},
    {"id": "FieldInfo.get_default"},
    {"lines": [True, 902]},
    {"lines": [902, 901]},
    {"path": "../outside.py"},
])
def test_undelivered_or_invalid_source_is_not_captured(tmp_path: Path, changes: dict) -> None:
    observed = delivered_source(tmp_path, **changes)
    assert source_reference(tmp_path, json.dumps(observed)) is None


@pytest.mark.parametrize("observation", ["Tool error: Symbol not found", "null", "[]", "{}"])
def test_non_source_observations_are_not_captured(tmp_path: Path, observation: str) -> None:
    assert source_reference(tmp_path, observation) is None


def test_json_handoff_restores_exact_overload_id_without_code(tmp_path: Path) -> None:
    reference = reference_for(tmp_path)
    persisted = handoff_payload("Forward validated_data to the resolver.", [reference])
    note = {"summary": persisted, "status": "hypothesis"}
    with patch.object(runner, "cli", return_value=({"notes": [note]}, 1)):
        restored = load_handoff(tmp_path, "pydantic/fields.py", persisted)
    assert "Forward validated_data to the resolver." in restored
    assert SYMBOL in restored
    assert '"lines":[901,902]' in restored
    assert reference["hash"] in restored
    assert '"status":"file_unchanged"' in restored
    assert "Saved hashes do not authorize if_hash" in restored
    assert "unique_source_evidence" not in restored
    assert "file_hash" not in restored


def test_changed_other_file_restores_only_stale_pointer(tmp_path: Path) -> None:
    reference = reference_for(tmp_path)
    persisted = handoff_payload("Check the caller in main.py.", [reference])
    # The note is bound to main.py; its referenced fields.py changes separately.
    (tmp_path / "pydantic/fields.py").write_text("# changed file\n")
    note = {"summary": persisted, "status": "hypothesis"}
    with patch.object(runner, "cli", return_value=({"notes": [note]}, 1)):
        restored = load_handoff(tmp_path, "pydantic/main.py", persisted)
    assert "Check the caller in main.py." in restored
    assert SYMBOL in restored and '"status":"stale"' in restored
    assert reference["hash"] not in restored
    assert '"lines"' not in restored
    assert '"mode"' not in restored
    assert "unique_source_evidence" not in restored


def test_deleted_reference_is_unavailable_without_old_span_or_hash(tmp_path: Path) -> None:
    reference = reference_for(tmp_path)
    (tmp_path / reference["path"]).unlink()
    assert restored_references(tmp_path, [reference]) == [
        {"id": SYMBOL, "path": reference["path"], "status": "unavailable"}]


def test_reference_limit_keeps_eight_recent_pointers(tmp_path: Path) -> None:
    reference = reference_for(tmp_path)
    references = [{**reference, "id": f"symbol:pydantic/fields.py:method_{index}"} for index in range(10)]
    payload = json.loads(handoff_payload("A concise handoff.", references))
    assert payload["references"] == references[-8:]
    assert len(restored_references(tmp_path, payload["references"])) == 8
    # Also bound restoration of an older/manually supplied oversized payload.
    assert len(restored_references(tmp_path, references)) == 8


def test_stale_json_note_withholds_narrative_and_all_references(tmp_path: Path) -> None:
    reference = reference_for(tmp_path)
    persisted = handoff_payload("Old claim", [reference])
    note = {"summary": persisted, "status": "stale"}
    with patch.object(runner, "cli", return_value=({"notes": [note]}, 1)):
        restored = load_handoff(tmp_path, reference["path"], persisted)
    assert "stale" in restored
    assert "Old claim" not in restored
    assert SYMBOL not in restored
    assert reference["hash"] not in restored


def test_missing_persisted_note_does_not_inject_local_handoff(tmp_path: Path) -> None:
    with patch.object(runner, "cli", return_value=({"notes": []}, 1)):
        with pytest.raises(RuntimeError, match="Persisted handoff was not found"):
            load_handoff(tmp_path, "pydantic/fields.py", "Unpersisted claim")


def test_trajectory_restores_persisted_references_without_source_or_hash_residency(tmp_path: Path) -> None:
    snapshots = []
    saved_notes = []
    source_requests = []
    source_hash = sha256(SOURCE.encode()).hexdigest()
    decisions = iter([
        {"action": "source", "node": "FieldInfo.get_default", "mode": "full", "answer": ""},
        {"action": "answer", "answer": "Investigated."},
        {"action": "answer", "answer": "Impact checked."},
        "Forward validated_data to the resolver after rechecking source.",
        {"action": "source", "node": SYMBOL, "mode": "full", "if_hash": source_hash, "answer": ""},
        {"action": "source", "node": SYMBOL, "mode": "full", "answer": ""},
        {"action": "edit", "path": "pydantic/fields.py", "old": "return unique_source_evidence",
         "new": "return repaired_source_evidence", "answer": ""},
        {"action": "answer", "answer": "Patched."},
    ])

    def provider(history, **kwargs):
        snapshots.append(copy.deepcopy(history))
        result = next(decisions)
        structured = isinstance(result, dict)
        assert (kwargs["schema"] is not None) == structured
        return {"decision": result if structured else None,
                "text": json.dumps(result) if structured else result, "cost_usd": None}

    def worktree(base, path, task, index):
        delivered_source(path)

    def cli(repo, *args):
        if args[:2] == ("explore", "source"):
            source_requests.append(args)
            return delivered_source(repo), 1
        if args[0] == "remember":
            saved_notes.append({"summary": args[args.index("--summary") + 1], "status": "hypothesis"})
            return {"success": True}, 1
        if args[:2] == ("explore", "recall"):
            return {"notes": saved_notes}, 1
        raise AssertionError(f"Unexpected CLI call: {args}")

    task = {"id": "handoff", "path": "pydantic/fields.py", "symptom": "Missing validated data.",
            "impact_question": "Check the resolver.", "hidden_test": "unused.py"}
    fake = SimpleNamespace(model_call=provider)
    with patch.dict("sys.modules", {"codex_runner": fake}), \
            patch.object(runner, "worktree", side_effect=worktree), \
            patch.object(runner, "remove_worktree"), \
            patch.object(runner, "pytest", return_value={"passed": True}), \
            patch.object(runner, "cli", side_effect=cli):
        result = runner.trajectory(tmp_path, task, "codemesh", 1, tmp_path, tmp_path / "unused.db",
                                   Path("/unused/python"), "gpt-6-luna", None, 4, provider="codex")

    assert result["status"] == "complete"
    assert result["handoff_reference_count"] == 1
    assert result["harness_memory_writes"] == 1
    payload = json.loads(saved_notes[0]["summary"])
    assert payload["references"][0]["id"] == SYMBOL
    assert "unique_source_evidence" not in saved_notes[0]["summary"]
    assert "unique_source_evidence" in json.dumps(snapshots[2])
    assert len(snapshots[4]) == 1
    fresh_prompt = snapshots[4][0]["content"]
    assert SYMBOL in fresh_prompt and source_hash in fresh_prompt
    assert "unique_source_evidence" not in fresh_prompt
    assert "if_hash bytes are absent from this session" in snapshots[5][-1]["content"]
    assert len(source_requests) == 2  # The old-hash attempt failed before any CLI call.
    assert all("--if-hash" not in args for args in source_requests)
    assert source_requests[0][source_requests[0].index("--epoch") + 1] == "0"
    assert source_requests[1][source_requests[1].index("--epoch") + 1] == "1"
    assert result["calls"][0]["context_session_id"] != result["calls"][4]["context_session_id"]
