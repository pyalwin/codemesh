"""No-model regression checks for the live matrix auditor."""
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from audit_controlled_next import Replay, blocks, known_sum, prompt_observations


def test_source_normalization_and_nonjson():
    assert blocks("read", "file lines 1-1 of 1:\n1: code") == []
    result = blocks("inspect", json.dumps({"sources": [{"filePath": "a.py", "sourceHash": "hash",
                                                       "lineStart": 2, "lineEnd": 4}]}))
    assert result[0]["path"] == "a.py"
    assert result[0]["hash"] == "hash"
    assert result[0]["lines"] == [2, 4]


def test_prompt_residency_excludes_handoff_ledger_and_restored_history():
    handoff = json.dumps({"action": "source", "source": "old", "hash": "not-authority"})
    state = {"initial_task": handoff, "navigation_ledger": [{"hash": "old"}],
             "retained_evidence": [{"action": "source", "observation": "retained"}]}
    messages = [
        {"role": "user", "content": "Deterministic harness working state.\n" + json.dumps(state)},
        {"role": "user", "content": 'Latest tool observation {"action":"restore","replayed":true}:\nhistorical\nChoose the next harness action'},
        {"role": "assistant", "content": "a hypothesis containing source"},
        {"role": "user", "content": 'Latest tool observation {"action":"source","replayed":false,"error":null}:\nfresh\nChoose the next harness action or answer from observed evidence.'},
    ]
    observed, states = prompt_observations("Continue this explicit conversation transcript:\n" + json.dumps(messages))
    assert [item["observation"] for item in observed] == ["retained", "fresh"]
    assert states == [state]


def test_unknown_usage_never_becomes_zero():
    assert known_sum([{"n": 3}, {"n": None}], "n") is None
    assert known_sum([{"n": 3}, {"n": 0}], "n") == 3


def fixture():
    task = {"path": "pydantic/a.py", "visible_test": "assert True\n", "mutations": [
        {"path": "pydantic/a.py", "old": "return good", "mutant": "return bad", "occurrence": 2},
        {"path": "pydantic/b.py", "old": "other good", "mutant": "other bad"}]}
    cache = {("pin", "pydantic/a.py"): "return good\nreturn good\ntail\n",
             ("pin", "pydantic/b.py"): "other good\n"}
    return Replay(Path("/unused"), "pin", task, cache)


def test_replay_multifile_mutations_exact_edit_and_patch():
    replay = fixture()
    assert replay.get("pydantic/a.py") == "return good\nreturn bad\ntail\n"
    assert replay.get("pydantic/b.py") == "other bad\n"
    assert replay.edit({"path": "pydantic/a.py", "old": "return bad", "new": "return good"})
    assert replay.edit({"path": "pydantic/b.py", "old": "other bad", "new": "other good"})
    assert not replay.edit({"path": "pydantic/a.py", "old": "return good", "new": "ambiguous"})
    assert replay.patch().count("--- a/") == 2
    assert "-return bad" in replay.patch()
    assert "-other bad" in replay.patch()


def test_source_replay_live_span_preview_and_changed_file_hash():
    replay = fixture()
    block = {"path": "pydantic/a.py", "lines": [1, 3], "mode": "auto",
             "source": "return good\n… [lines 2-2 omitted] …\ntail",
             "omittedRanges": [{"start": 2, "end": 2}]}
    assert replay.source_matches(block)
    assert replay.source_matches({**block, "source": "wrong"}) is False
    assert replay.source_matches({**block, "fileHash": "old"}) is False
    assert replay.source_matches({**block, "mode": "fold"}) is None
    assert replay.source_matches({**block, "omittedRanges": [{"start": 3, "end": 5}]}) is False


def test_numbered_read_replay_checks_headers_and_current_bytes():
    replay = fixture()
    text = "pydantic/a.py lines 2-3 of 3:\n2: return bad\n3: tail"
    assert replay.read_matches(text)
    assert not replay.read_matches(text.replace("of 3", "of 4"))
    assert not replay.read_matches(text.replace("return bad", "return good"))
    assert replay.read_matches("eval_visible_test.py lines 1-1 of 1:\n1: assert True")


def test_replay_rejects_path_escape_without_shell_execution():
    replay = fixture()
    with patch("audit_controlled_next.subprocess.run") as run:
        with pytest.raises(ValueError, match="Invalid source path"):
            replay.get("../../outside.py")
        run.assert_not_called()


def test_only_explicit_transport_diagnostic_error_item_is_classified():
    from audit_controlled_next import is_transport_diagnostic
    event = {"type": "item.completed", "item": {"type": "error", "message":
        "Falling back from WebSockets to HTTPS transport. stream disconnected before completion: Connection refused (os error 61)"}}
    assert is_transport_diagnostic(event)
    assert not is_transport_diagnostic({**event, "type": "item.started"})
    assert not is_transport_diagnostic({**event, "item": {**event["item"], "type": "command_execution"}})
    assert not is_transport_diagnostic({**event, "item": {"type": "error", "message": "An unrelated error"}})


def test_no_write_does_not_export_reconstructed_patches(tmp_path, monkeypatch, capsys):
    import audit_controlled_next as module
    monkeypatch.setattr(module, "audit", lambda *args: {"status": "PASS", "_patch_replays": [
        {"path": "derived-patches/attempt.diff", "body": "reconstructed"}]})
    monkeypatch.setattr(module.sys, "argv", ["audit", "--root", str(tmp_path), "--no-write"])
    assert module.main() == 0
    assert list(tmp_path.iterdir()) == []
    assert json.loads(capsys.readouterr().out)["derived_patch_artifacts_written"] is False


def test_written_reconstructed_patch_is_distinct_and_labeled(tmp_path, monkeypatch, capsys):
    import audit_controlled_next as module
    monkeypatch.setattr(module, "audit", lambda *args: {"status": "PASS", "_patch_replays": [
        {"path": "derived-patches/attempt.diff", "body": "reconstructed"}]})
    monkeypatch.setattr(module.sys, "argv", ["audit", "--root", str(tmp_path), "--stage", "full"])
    assert module.main() == 0
    assert (tmp_path / "derived-patches/attempt.diff").read_text() == "reconstructed"
    report = json.loads((tmp_path / "VALIDATION-full.json").read_text())
    assert report["derived_patch_artifacts_written"] is True
    assert "_patch_replays" not in report
