from hashlib import sha256
import json

import pytest

from focused_state import FocusedWorkingState


def data(state):
    return json.loads(state.render()[0]["content"].split("\n", 1)[1])


def text(state):
    return json.dumps(state.render(), ensure_ascii=False)


def mapping(state, parent, children, *, cursor=None, request_cursor=None, relation="contains", marker=""):
    result = {"node": {"id": parent, "name": parent, "type": parent.split(":")[0]},
              "entries": [{"id": child, "name": child.split(":")[-1], "type": child.split(":")[0], "signature": marker}
                          for child in children]}
    if cursor:
        result["nextCursor"] = cursor
    inputs = {"node": parent, "relation": relation}
    if request_cursor:
        inputs["cursor"] = request_cursor
    observation = json.dumps(result, separators=(",", ":"))
    return state.observe("map", inputs, observation), observation


def source(state, name="login", **changes):
    code = f"def {name}():\n    return '{name}_EXACT_SOURCE'"
    result = {"id": f"symbol:src/{name}.py:{name}", "path": f"src/{name}.py", "status": "fresh", "mode": "full",
              "source": code, "hash": sha256(code.encode()).hexdigest(), "lines": [1, 2], **changes}
    observation = json.dumps(result, separators=(",", ":"))
    return state.observe("source", {"node": name, "mode": "full"}, observation), result, observation


def test_branch_switch_keeps_both_root_branches_and_all_resident_source(tmp_path):
    state = FocusedWorkingState(tmp_path, "s")
    state.task("Repair login and billing; keep the public API.")
    root, original_root = mapping(state, "repo", ["dir:auth", "dir:billing"])
    auth, _ = mapping(state, "dir:auth", ["file:src/login.py"])
    source(state)
    state.assistant({"action": "map", "node": "dir:billing"})
    billing, _ = mapping(state, "dir:billing", ["file:src/invoice.py"])
    source(state, "invoice")
    state.assistant({"action": "map", "node": "dir:auth"})
    ids = [item["id"] for item in data(state)["navigation"]["main_index"]["entries"]]
    assert ids == ["dir:auth", "dir:billing"]
    assert "login_EXACT_SOURCE" in text(state) and "invoice_EXACT_SOURCE" in text(state)
    assert {auth, billing} <= {entry["handle"] for entry in data(state)["navigation"]["ledger"]}
    before = state.resident_hashes()
    assert state.restore(root) == original_root
    assert "historical_navigation_only" in text(state)
    assert state.resident_hashes() == before
    assert "selection_history" not in text(state) and "retention_candidates" not in text(state)


def test_duplicate_request_collapses_detail_without_evicting_other_branch(tmp_path):
    state = FocusedWorkingState(tmp_path, "s", ledger_entries=2)
    a, original = mapping(state, "dir:auth", ["file:src/login.py"], marker="REPEATED_RAW_DETAIL")
    b, _ = mapping(state, "dir:billing", ["file:src/invoice.py"])
    repeated, again = mapping(state, "dir:auth", ["file:src/login.py"], marker="REPEATED_RAW_DETAIL")
    assert again == original
    ledger = data(state)["navigation"]["ledger"]
    assert [entry["handle"] for entry in ledger] == [b, repeated]
    assert ledger[-1]["supersedes"] == a and ledger[-1]["same_result_as"] == a
    assert "REPEATED_RAW_DETAIL" not in text(state)
    assert "Repeated navigation observation" in text(state)
    assert json.loads((tmp_path / (repeated + ".json")).read_text())["observation"] == original
    assert state.restore(repeated) == original
    assert "REPEATED_RAW_DETAIL" in text(state)


def test_changed_result_supersedes_only_same_request_and_is_delivered_raw(tmp_path):
    state = FocusedWorkingState(tmp_path, "s")
    original, _ = mapping(state, "file:src/login.py", ["symbol:src/login.py:login"])
    changed, _ = mapping(state, "file:src/login.py", ["symbol:src/login.py:login", "symbol:src/login.py:logout"], marker="NEW_RESULT")
    assert "NEW_RESULT" in text(state)
    ledger = data(state)["navigation"]["ledger"]
    assert ledger[-1]["handle"] == changed and ledger[-1]["supersedes"] == original
    assert "same_result_as" not in ledger[-1]
    assert state.context_stats()["collapsed_requests"] == 1


def test_failed_and_replayed_requests_do_not_supersede_live_frontier(tmp_path):
    state = FocusedWorkingState(tmp_path, "s")
    live, original = mapping(state, "dir:auth", ["file:src/login.py"])
    failed = state.observe("map", {"node": "dir:auth"}, "Cannot read map", error="failed")
    assert {live, failed} <= {entry["handle"] for entry in data(state)["navigation"]["ledger"]}
    assert state.context_stats()["collapsed_requests"] == 0
    state.restore(live)
    assert state.context_stats()["collapsed_requests"] == 0
    assert original in state.render()[-1]["content"]


def test_task_and_exact_evidence_prefix_is_stable_across_navigation_only_turns(tmp_path):
    state = FocusedWorkingState(tmp_path, "s")
    state.task("Investigate authentication. Preserve rate limiting.")
    source(state, "login")
    source(state, "invoice")
    state.assistant({"action": "map", "node": "repo"})
    before = state.render()[0]["content"].split(',"navigation":', 1)[0]
    mapping(state, "repo", ["dir:auth", "dir:billing"])
    state.assistant({"action": "search", "query": "login"})
    state.observe("search", {"query": "login"}, '{"results":[{"id":"symbol:src/login.py:login"}]}')
    after = state.render()[0]["content"].split(',"navigation":', 1)[0]
    assert before == after
    assert [entry["path"] for entry in data(state)["retained_evidence"]] == ["src/login.py", "src/invoice.py"]
    assert state.render() == state.render()
    assert list(data(state))[:2] == ["initial_task", "retained_evidence"]


def test_source_is_rendered_once_and_navigation_does_not_change_hash_authority(tmp_path):
    state = FocusedWorkingState(tmp_path, "s")
    _, result, original = source(state)
    assert text(state).count("login_EXACT_SOURCE") == 1
    assert data(state)["retained_evidence"] == []
    hashes = state.resident_hashes()
    assert (result["id"], "full", result["hash"]) in hashes
    state.assistant({"action": "search", "query": "login"})
    state.observe("search", {"query": "login"}, '{"results":[]}')
    assert text(state).count("login_EXACT_SOURCE") == 1
    assert data(state)["retained_evidence"][0]["observation"] == original
    assert state.resident_hashes() == hashes


def test_pending_pagination_survives_ledger_eviction_and_backtracking(tmp_path):
    state = FocusedWorkingState(tmp_path, "s", ledger_entries=1)
    page, original = mapping(state, "dir:auth", ["file:src/login.py"], cursor="30")
    mapping(state, "dir:billing", ["file:src/invoice.py"])
    assert page not in [entry["handle"] for entry in data(state)["navigation"]["ledger"]]
    assert data(state)["navigation"]["pending_pages"][0]["pending_cursor"] == "30"
    state.restore(page)
    assert data(state)["navigation"]["pending_pages"][0]["handle"] == page
    state.observe("map", {"node": "dir:auth", "cursor": "30"}, "failed", error="failed")
    assert data(state)["navigation"]["pending_pages"]
    mapping(state, "dir:auth", [], request_cursor="30")
    assert not data(state)["navigation"].get("pending_pages")
    assert state.restore(page) == original


def test_repeated_root_and_pending_page_are_deduplicated_not_accumulated(tmp_path):
    state = FocusedWorkingState(tmp_path, "s")
    for _ in range(12):
        latest, _ = mapping(state, "repo", ["dir:auth", "dir:billing"], cursor="30")
    nav = data(state)["navigation"]
    assert len(nav["main_index"]["pages"]) == len(nav["pending_pages"]) == 1
    assert nav["main_index"]["pages"][0]["handle"] == latest
    assert nav["pending_pages"][0]["handle"] == latest
    assert len(nav["main_index"]["entries"]) == 2
    assert state.context_stats()["collapsed_requests"] == 11


def test_cross_relation_map_does_not_modify_root_containment(tmp_path):
    state = FocusedWorkingState(tmp_path, "s")
    mapping(state, "repo", ["dir:auth"])
    before = data(state)["navigation"]["main_index"]
    mapping(state, "symbol:src/login.py:login", ["symbol:src/invoice.py:invoice"], relation="called_by", cursor="30")
    assert data(state)["navigation"]["main_index"] == before
    assert data(state)["navigation"]["pending_pages"][0]["relation"] == "called_by"


def test_task_hypotheses_errors_tests_edits_survive_bounded_navigation(tmp_path):
    state = FocusedWorkingState(tmp_path, "s", ledger_entries=1, ledger_bytes=1024)
    state.task("Initial constraints: preserve public API")
    source(state)
    state.assistant({"action": "answer", "answer": "Unverified hypothesis: login uses cached permissions"})
    state.task("Verify the billing caller")
    state.observe("test", {}, "TEST_FAILURE_DETAIL", route={"passed": False})
    state.observe("search", {"query": "bad"}, "TOOL_ERROR_DETAIL", error="failed")
    for index in range(10):
        mapping(state, f"dir:branch{index}", [f"file:branch{index}/module.py"], cursor="30")
    payload = data(state)
    assert "preserve public API" in payload["initial_task"]
    assert payload["current_phase"] == "Verify the billing caller"
    assert payload["prior_model_hypotheses"][0]["model_authored_hypothesis"]
    assert "TEST_FAILURE_DETAIL" in text(state) and "TOOL_ERROR_DETAIL" in text(state)
    assert "login_EXACT_SOURCE" in text(state)
    state.observe("edit", {"path": "src/login.py", "old": "cached", "new": "current"}, "done")
    state.assistant({"action": "test"})
    state.observe("test", {}, "PASS", route={"passed": True})
    assert data(state)["successful_edits"]["recent"][0]["new"] == "current"
    assert "login_EXACT_SOURCE" not in text(state) and not state.resident_hashes()


def test_edit_and_stale_source_invalidate_source_and_navigation(tmp_path):
    state = FocusedWorkingState(tmp_path, "s")
    nav, _ = mapping(state, "file:src/login.py", ["symbol:src/login.py:login"], cursor="30")
    source(state)
    state.invalidate("src/login.py")
    assert not state.resident_hashes()
    assert "login_EXACT_SOURCE" not in text(state)
    assert data(state)["navigation"]["pending_pages"][0]["stale"]
    assert next(entry for entry in data(state)["navigation"]["ledger"] if entry["handle"] == nav)["stale"]
    fresh, _ = mapping(state, "file:src/login.py", ["symbol:src/login.py:login"], cursor="30")
    assert "Repeated navigation observation" not in text(state)
    source(state)
    source(state, status="stale", source=None, hash=None)
    assert not state.resident_hashes()
    assert "login_EXACT_SOURCE" not in text(state)


def test_reset_clears_source_and_focused_navigation_but_preserves_archive(tmp_path):
    state = FocusedWorkingState(tmp_path, "s")
    root, original = mapping(state, "repo", ["dir:auth"], cursor="30")
    source_handle, _, _ = source(state)
    archive_count = state.context_stats()["archive_count"]
    state.reset("fresh", "Only this supplied handoff task")
    assert data(state)["initial_task"] == "Only this supplied handoff task"
    assert not state.resident_hashes()
    assert not data(state)["navigation"].get("main_index", {}).get("entries")
    assert not data(state)["navigation"].get("pending_pages")
    assert state.context_stats()["archive_count"] == archive_count
    assert state.restore(root) == original
    assert not data(state)["navigation"].get("main_index", {}).get("entries")
    with pytest.raises(ValueError, match="live read"):
        state.restore(source_handle)


@pytest.mark.parametrize("budget", [0, 40, 512, 1024, 2048])
def test_navigation_budget_and_archive_recovery_do_not_evict_exact_evidence(tmp_path, budget):
    state = FocusedWorkingState(tmp_path, "s", ledger_bytes=budget, ledger_entries=2)
    source(state)
    state.assistant({"action": "map", "node": "repo"})
    for index in range(20):
        mapping(state, "repo", [f"dir:branch{index}"], cursor=str(index + 1), request_cursor=str(index))
    payload = data(state)
    nav_bytes = len(json.dumps(payload["navigation"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    assert nav_bytes <= max(2, budget)
    assert payload["archive"]["root"] == "archive:0"
    assert "login_EXACT_SOURCE" in text(state)
    assert state.resident_hashes()
    assert state.render() == state.render()
    assert json.loads(state.restore("archive:0"))["entries"]


def test_oversized_source_is_once_only_and_never_gains_hash_authority(tmp_path):
    state = FocusedWorkingState(tmp_path, "s", evidence_bytes=100)
    handle, _, _ = source(state)
    assert "login_EXACT_SOURCE" in text(state)
    assert not state.resident_hashes()
    state.assistant({"action": "map", "node": "repo"})
    mapping(state, "repo", ["dir:auth"])
    assert "login_EXACT_SOURCE" not in text(state)
    with pytest.raises(ValueError, match="live read"):
        state.restore(handle)


def test_current_provenance_is_enclosed_but_historical_and_stale_provenance_remain(tmp_path):
    state = FocusedWorkingState(tmp_path, "current")
    handle, _ = mapping(state, "file:src/login.py", ["symbol:src/login.py:login"], cursor="30")
    nav = data(state)["navigation"]
    assert nav["provenance"] == {"session_id": "current", "revision": 0}
    assert "session_id" not in nav["ledger"][0] and "revision" not in nav["ledger"][0]
    assert "session_id" not in nav["pending_pages"][0]
    state.invalidate("src/login.py")
    nav = data(state)["navigation"]
    assert nav["ledger"][0]["session_id"] == "current" and nav["ledger"][0]["revision"] == 0
    assert nav["ledger"][0]["stale"]
    assert nav["pending_pages"][0]["revision"] == 0 and nav["pending_pages"][0]["stale"]
    state.reset("next")
    state.restore(handle)
    historic = data(state)["navigation"]["ledger"][0]
    assert historic["session_id"] == "current" and historic["revision"] == 0
    assert historic["replayed"] and historic["stale"]


def test_root_and_source_metadata_are_not_repeated_in_navigation_ledger(tmp_path):
    state = FocusedWorkingState(tmp_path, "s")
    root, _ = mapping(state, "repo", ["dir:auth", "file:README.md"], cursor="30")
    source_handle, _, original = source(state)
    state.assistant({"action": "search", "query": "login"})
    nav = data(state)["navigation"]
    assert nav["main_index"]["entries"] == [{"id": "dir:auth"}, {"id": "file:README.md"}]
    assert nav["main_index"]["pages"] == [{"handle": root}]
    assert not nav.get("ledger")
    assert nav["pending_pages"][0]["pending_cursor"] == "30"
    assert "recovery" not in nav
    assert "budget_omissions" not in nav and "pending_pages_omitted" not in nav
    assert data(state)["archive"]["root"] == "archive:0"
    assert data(state)["retained_evidence"][0]["handle"] == source_handle
    assert data(state)["retained_evidence"][0]["observation"] == original


@pytest.mark.parametrize("action,option,first,second", [
    ("search", "scope", "files", "symbols"),
    ("search", "max_bytes", 1024, 8192),
    ("search", "maxBytes", 1024, 8192),
    ("map", "max_bytes", 1024, 8192),
    ("map", "maxBytes", 1024, 8192),
])
def test_different_scope_or_budget_requests_do_not_supersede(tmp_path, action, option, first, second):
    state = FocusedWorkingState(tmp_path, "s")
    inputs = {"query": "login", "path": "src"} if action == "search" else {"node": "dir:src"}
    observation = '{"results":[]}' if action == "search" else '{"node":{"id":"dir:src"},"entries":[]}'
    original = state.observe(action, {**inputs, option: first}, observation)
    different = state.observe(action, {**inputs, option: second}, observation)
    assert state.context_stats()["collapsed_requests"] == 0
    assert {original, different} <= {entry["handle"] for entry in data(state)["navigation"]["ledger"]}
    repeated = state.observe(action, {**inputs, option: second, "answer": "harness narration", "unused": None}, observation)
    assert state.context_stats()["collapsed_requests"] == 1
    assert {original, repeated} <= {entry["handle"] for entry in data(state)["navigation"]["ledger"]}
