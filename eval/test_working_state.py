from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from working_state import WorkingState


def source(name: str = "alpha", *, body: str | None = None, mode: str = "full", **changes) -> tuple[dict, str]:
    body = body if body is not None else f"def {name}():\n    return '{name}_SOURCE_SENTINEL'"
    result = {"status": "fresh", "id": f"symbol:src/{name}.py:{name}", "path": f"src/{name}.py",
              "lines": [1, 2], "mode": mode, "hash": sha256(body.encode()).hexdigest(), "source": body, **changes}
    return result, json.dumps(result, separators=(",", ":"))


def rendered(state: WorkingState) -> str:
    return json.dumps(state.render(), ensure_ascii=False)


def data(state: WorkingState) -> dict:
    return json.loads(state.render()[0]["content"].split("\n", 1)[1])


def deliver(state: WorkingState, name: str = "alpha", **kwargs) -> tuple[dict, str, str]:
    result, text = source(name, **kwargs)
    inputs = {"node": name, "mode": result["mode"]}
    handle = state.observe("source", inputs, text, route={"status": result["status"]})
    return result, text, handle


def test_task_phases_keep_initial_constraints_without_growing_history(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    state.task("Repair alpha. Preserve public API. Investigate first.")
    state.task("Check branch A.")
    state.task("Check branch B.")
    payload = data(state)
    assert payload["initial_task"] == "Repair alpha. Preserve public API. Investigate first."
    assert payload["current_phase"] == "Check branch B."
    assert "Check branch A" not in rendered(state)


def test_latest_navigation_is_idempotent_then_drops_raw_output_with_handle_retained(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    state.assistant({"action": "map", "node": "repo", "query": None, "answer": ""})
    observation = json.dumps({"entries": [{"id": "symbol:src/alpha.py:alpha", "summary": "RAW_NAV_SENTINEL"}], "nextCursor": "cursor-2"})
    handle = state.observe("map", {"node": "repo"}, observation)
    assert state.render() == state.render()
    assert rendered(state).count("RAW_NAV_SENTINEL") == 1
    assert '"query":null' not in state.render()[1]["content"]
    state.assistant({"action": "source", "node": "symbol:src/alpha.py:alpha", "answer": ""})
    assert "RAW_NAV_SENTINEL" not in rendered(state)
    entry = data(state)["navigation_ledger"][0]
    assert entry["handle"] == handle
    assert entry["candidate_ids"] == ["symbol:src/alpha.py:alpha"]
    assert entry["pending_cursor"] == "cursor-2"
    assert state.restore(handle) == observation
    assert "RAW_NAV_SENTINEL" in rendered(state)
    assert "historical_navigation_only" in rendered(state)
    assert not state.resident_hashes()


def test_archive_keeps_complete_original_input_output_error_and_route_immutably(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    inputs = {"action": "search", "query": "alpha", "limit": None}
    route = {"backend": "codemesh", "elapsed_ms": 5}
    observation = "unbounded archive observation " * 1000
    handle = state.observe("search", inputs, observation, "failed", route)
    stored = (tmp_path / (handle + ".json")).read_bytes()
    inputs["query"] = "changed"
    route["backend"] = "changed"
    state.reset("next-session", "New task")
    state.observe("grep", {"query": "new"}, "No matches")
    assert (tmp_path / (handle + ".json")).read_bytes() == stored
    record = json.loads(stored)
    assert record["observation"] == observation
    assert record["input"]["query"] == "alpha"
    assert "limit" in record["input"] and record["input"]["limit"] is None
    assert record["route"]["backend"] == "codemesh" and record["error"] == "failed"
    assert record["session_id"] == "s"
    assert state.context_stats()["archive_count"] == 2


def test_exact_source_is_rendered_once_then_retained_and_aliases_are_eligible(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    result, observation, _ = deliver(state)
    assert rendered(state).count("alpha_SOURCE_SENTINEL") == 1
    assert data(state)["retained_evidence"] == []  # The latest raw observation already carries it.
    assert (result["id"], "full", result["hash"]) in state.resident_hashes()
    assert ("alpha", "full", result["hash"]) in state.resident_hashes()
    state.assistant({"action": "search", "query": "other", "answer": ""})
    assert state.observation_resident("source", {"node": result["id"]}, observation)
    state.observe("search", {"query": "other"}, '{"results":[]}')
    evidence = data(state)["retained_evidence"]
    assert len(evidence) == 1 and evidence[0]["observation"] == observation
    assert rendered(state).count("alpha_SOURCE_SENTINEL") == 1


def test_source_lru_eviction_removes_hash_authority_and_requires_live_read(tmp_path: Path):
    state = WorkingState(tmp_path, "s", evidence_bytes=1800)
    a, a_text, a_handle = deliver(state, "a", body="a" * 400)
    state.assistant({"action": "source", "node": "b"})
    b, b_text, b_handle = deliver(state, "b", body="b" * 400)
    assert state.context_stats()["evidence_count"] == 2
    # A conditional hit touches existing exact evidence without reintroducing bytes.
    state.assistant({"action": "source", "node": "a", "if_hash": a["hash"]})
    state.observe("source", {"node": "a", "mode": "full"}, json.dumps({**a, "status": "unchanged", "source": None}))
    state.assistant({"action": "source", "node": "c"})
    deliver(state, "c", body="c" * 400)
    assert state.observation_resident("source", {}, a_text)
    assert not state.observation_resident("source", {}, b_text)
    assert (b["id"], "full", b["hash"]) not in state.resident_hashes()
    assert state.context_stats()["evidence_bytes"] <= 1800
    with pytest.raises(ValueError, match="live read"):
        state.restore(b_handle)
    assert (tmp_path / (a_handle + ".json")).is_file()


def test_oversized_source_is_delivered_once_but_cannot_authorize_conditional_reads(tmp_path: Path):
    state = WorkingState(tmp_path, "s", evidence_bytes=200)
    result, observation, handle = deliver(state, body="🙂" * 1000)
    assert state.render()[-1]["content"].count(observation) == 1
    assert not state.resident_hashes()
    state.assistant({"action": "search", "query": "next"})
    assert "🙂" not in rendered(state)
    assert not state.observation_resident("source", {}, observation)
    with pytest.raises(ValueError, match="live read"):
        state.restore(handle)


def test_native_reads_retained_exactly_without_source_hash_authority(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    inputs = {"path": "./src/alpha.py", "start": 1, "limit": 2}
    observation = "src/alpha.py lines 1-2 of 2:\n1: def alpha():\n2:     return 1"
    handle = state.observe("read", inputs, observation)
    state.assistant({"action": "grep", "query": "beta"})
    assert state.observation_resident("read", inputs, observation)
    assert not state.observation_resident("read", {**inputs, "start": 2}, observation)
    assert not state.observation_resident("read", inputs, observation + "modified")
    assert data(state)["retained_evidence"][0]["observation"] == observation
    assert not state.resident_hashes()
    with pytest.raises(ValueError, match="live read"):
        state.restore(handle)


def test_edit_invalidates_all_file_views_aliases_pending_source_and_navigation(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    state.observe("search", {"query": "alpha"}, '{"results":[{"id":"symbol:src/alpha.py:alpha"}]}')
    state.assistant({"action": "source", "node": "alpha"})
    a, _, _ = deliver(state)
    deliver(state, mode="preview")
    state.invalidate("./src/alpha.py")
    assert not state.resident_hashes()
    assert "alpha_SOURCE_SENTINEL" not in rendered(state)
    assert data(state)["navigation_ledger"][0]["stale"] is True
    assert state.context_stats()["evidence_count"] == 0


def test_successful_edit_details_survive_test_and_final_answer(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    deliver(state)
    edit = {"action": "edit", "path": "src/alpha.py", "old": "return False", "new": "return True", "answer": ""}
    state.assistant(edit)
    state.observe("edit", edit, "Edited src/alpha.py", route={"backend": "filesystem"})
    state.assistant({"action": "test", "answer": ""})
    state.observe("test", {"action": "test"}, "1 passed", route={"passed": True})
    state.assistant({"action": "answer", "answer": "Repaired."})
    assert not state.resident_hashes()
    patches = data(state)["successful_edits"]
    assert patches["total"] == 1 and patches["omitted_older"] == 0
    assert patches["recent"][0]["old"] == "return False"
    assert patches["recent"][0]["new"] == "return True"
    assert patches["recent"][0]["truncated"] is False
    assert data(state)["latest_test"]["passed"] is True


def test_edit_log_is_bounded_and_failed_edits_are_not_recorded(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    for index in range(6):
        state.observe("edit", {"path": f"src/{index}.py", "old": "x" * 5000, "new": "y" * 5000}, "Edited")
    state.observe("edit", {"path": "src/failure.py", "old": "absent", "new": "x"}, "old not found", error="old not found")
    edits = data(state)["successful_edits"]
    assert edits["total"] == 6 and edits["omitted_older"] == 2
    assert len(edits["recent"]) == 4
    assert all(entry["truncated"] and entry["old_excerpt"]["truncated"] for entry in edits["recent"])
    assert all("old" not in entry and "new" not in entry for entry in edits["recent"])


def test_fresh_reset_clears_all_active_authority_but_navigation_archive_can_restore(tmp_path: Path):
    state = WorkingState(tmp_path, "old")
    state.task("Old task")
    source_result, _, source_handle = deliver(state)
    state.assistant({"action": "answer", "answer": "Old conclusion"})
    handle = state.observe("grep", {"query": "alpha"}, "./src/alpha.py:1:def alpha():")
    state.reset("new", "New task with handoff pointer only")
    assert not state.resident_hashes()
    assert "Old task" not in rendered(state) and "Old conclusion" not in rendered(state)
    assert source_result["hash"] not in rendered(state)
    assert data(state)["navigation_ledger"] == [] and data(state)["retained_evidence"] == []
    with pytest.raises(ValueError, match="live read"):
        state.restore(source_handle)
    state.restore(handle)
    assert data(state)["navigation_ledger"][0]["stale"] is True
    assert data(state)["navigation_ledger"][0]["session_id"] == "old"
    assert not state.resident_hashes()


@pytest.mark.parametrize("changes", [{"hash": "wrong"}, {"status": "stale"}, {"status": "deleted"},
                                      {"status": "unchanged", "source": None}, {"mode": "fold"},
                                      {"mode": []}, {"id": "alpha"}, {"path": "../outside.py"}])
def test_invalid_or_non_source_views_cannot_create_hash_authority(tmp_path: Path, changes: dict):
    state = WorkingState(tmp_path, "s")
    result, observation = source(**changes)
    state.observe("source", {"node": "alpha"}, observation)
    assert not state.resident_hashes()
    assert not state.observation_resident("source", {}, observation)


def test_stale_source_response_invalidates_other_resident_views_of_same_file(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    a, _, _ = deliver(state)
    state.assistant({"action": "source", "node": "alpha"})
    state.observe("source", {"node": "alpha"}, json.dumps({**a, "status": "stale", "source": None, "hash": None}))
    assert not state.resident_hashes()
    assert "alpha_SOURCE_SENTINEL" not in rendered(state)


def test_prior_answers_remain_bounded_explicit_hypotheses_after_later_tools(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    for number in range(3):
        state.task(f"Phase {number}")
        state.assistant({"action": "answer", "answer": f"Hypothesis {number}: " + "🙂" * 1000})
    state.assistant({"action": "search", "query": "later", "limit": None})
    state.observe("search", {"query": "later"}, '{"results":[]}')
    hypotheses = data(state)["prior_model_hypotheses"]
    assert len(hypotheses) == 2
    assert all(entry["model_authored_hypothesis"] and entry["truncated"] for entry in hypotheses)
    assert all(len(entry["text"].encode()) <= 2000 for entry in hypotheses)
    assert "Hypothesis 0" not in rendered(state)


def test_latest_error_and_failed_test_survive_navigation_without_duplicate_raw_output(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    state.observe("source", {"node": "missing"}, "Tool error: missing symbol", error="missing symbol")
    assert rendered(state).count("Tool error: missing symbol") == 1
    state.assistant({"action": "test"})
    state.observe("test", {"action": "test"}, "FAILED test_alpha: expected 1 got 0", route={"passed": False})
    state.assistant({"action": "search", "query": "alpha"})
    state.observe("search", {"query": "alpha"}, '{"results":[]}')
    assert data(state)["latest_tool_error"]["text"] == "Tool error: missing symbol"
    assert data(state)["latest_test"]["passed"] is False
    assert "expected 1 got 0" in data(state)["latest_test"]["text"]


def test_navigation_limits_preserve_archive_and_restore_provenance(tmp_path: Path):
    state = WorkingState(tmp_path, "s", ledger_entries=2, ledger_bytes=1500)
    handles = []
    for index in range(20):
        handles.append(state.observe("search", {"query": f"query{index}"}, json.dumps({
            "results": [{"id": f"symbol:src/alpha.py:candidate{index}"}], "nextCursor": "c" * 2000})))
    assert state.context_stats()["ledger_count"] <= 2
    assert state.context_stats()["ledger_bytes"] <= 1500
    assert state.context_stats()["archive_count"] == 20
    assert data(state)["navigation_ledger"][-1]["pending_cursor_archived"] is True
    state.restore(handles[0])
    assert "query0" in rendered(state)
    assert not state.resident_hashes()


def test_archive_handles_reject_traversal_symlinks_and_tampering(tmp_path: Path):
    state = WorkingState(tmp_path / "archive", "s")
    handle = state.observe("map", {"node": "repo"}, '{"entries":[]}')
    for invalid in ("../secret", "/tmp/secret", handle + "/../x"):
        with pytest.raises(ValueError, match="Invalid observation handle"):
            state.restore(invalid)
    path = state.archive_dir / (handle + ".json")
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="integrity"):
        state.restore(handle)
    path.unlink()
    other = tmp_path / "other.json"
    other.write_bytes(original)
    path.symlink_to(other)
    with pytest.raises(ValueError, match="archive file"):
        state.restore(handle)


def test_evicted_ledger_branches_are_discoverable_through_bounded_archive_index(tmp_path: Path):
    state = WorkingState(tmp_path, "s", ledger_entries=1)
    for number in range(9):
        state.observe("map", {"node": f"dir:branch{number}"}, json.dumps({"entries": [], "nextCursor": f"cursor{number}"}))
    state.observe("search", {"query": "failed-query"}, "Tool error: invalid query", error="invalid query")
    _, _, source_handle = deliver(state)
    state.assistant({"action": "answer", "answer": "Investigated"})
    assert data(state)["archive"]["root"] == "archive:0"
    before = state.resident_hashes()
    first_page = json.loads(state.restore("archive:0"))
    assert len(first_page["entries"]) == 8 and first_page["next"] == "archive:8"
    assert first_page["entries"][0]["node"] == "dir:branch0"
    assert first_page["entries"][0]["pending_branch"] is True
    assert first_page["entries"][0]["empty_result"] is True
    second_page = json.loads(state.restore(first_page["next"]))
    assert len(second_page["entries"]) == 3 and "next" not in second_page
    assert second_page["entries"][1]["failed"] is True
    assert second_page["entries"][1]["query"] == "failed-query"
    assert second_page["entries"][2]["id"] == "symbol:src/alpha.py:alpha"
    assert second_page["entries"][2]["restore"] == "live_read_required"
    assert "source" not in second_page["entries"][2]
    assert state.resident_hashes() == before
    with pytest.raises(ValueError, match="live read"):
        state.restore(source_handle)
    assert len(json.dumps(first_page).encode()) < 8192


def test_new_working_state_can_restore_archive_without_reusing_active_context(tmp_path: Path):
    first = WorkingState(tmp_path, "s1")
    handle = first.observe("search", {"query": "alpha"}, '{"results":[]}')
    second = WorkingState(tmp_path, "s2")
    assert second.context_stats()["archive_count"] == 1
    assert second.restore(handle) == '{"results":[]}'
    second.observe("grep", {"query": "alpha"}, "No matches")
    assert second.context_stats()["archive_count"] == 2
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_stats_are_read_only_and_count_exact_serialized_context(tmp_path: Path):
    state = WorkingState(tmp_path, "s")
    deliver(state)
    before = state.render()
    stats = state.context_stats()
    assert stats["rendered_bytes"] == len(json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    assert stats["archive_bytes"] == sum(path.stat().st_size for path in tmp_path.glob("*.json"))
    assert state.render() == before
