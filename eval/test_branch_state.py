from __future__ import annotations

from hashlib import sha256
import json

import pytest

from branch_state import BranchWorkingState, SelectedWorkingState


def data(state, role="selector"):
    return json.loads(state.render(role=role)[0]["content"].split("\n", 1)[1])


def text(state, role="selector"):
    return json.dumps(state.render(role=role), ensure_ascii=False)


def node(identity, name=None):
    return {"id": identity, "name": name or identity.split(":")[-1], "type": identity.split(":")[0]}


def mapping(state, parent, children, *, cursor=None, relation="contains", sentinel=""):
    result = {"node": node(parent), "entries": [{**node(child), "signature": sentinel} for child in children]}
    if cursor:
        result["nextCursor"] = cursor
    observation = json.dumps(result)
    handle = state.observe("map", {"node": parent, "relation": relation}, observation)
    return handle, observation


def source(state, identity="symbol:auth/login.py:login", body="def login(): return 'EXACT_AUTH_BODY'"):
    result = {"status": "fresh", "id": identity, "path": identity.split(":")[1], "lines": [1, 1],
              "mode": "full", "source": body, "hash": sha256(body.encode()).hexdigest()}
    return state.observe("source", {"node": identity, "mode": "full"}, json.dumps(result)), result


def select(state, keep=(), archive=(), focus=None, reason="Keep evidence for the active question"):
    decision = {"reason": reason, "retain_handles": list(keep), "archive_handles": list(archive)}
    if focus:
        decision["focus_id"] = focus
    return state.apply_retention(decision)


def test_root_branch_symbol_switch_and_backtrack_require_explicit_choices(tmp_path):
    state = SelectedWorkingState(tmp_path, "session")
    state.task("Repair auth and billing. Preserve compatibility.")
    root, _ = mapping(state, "repo", ["dir:auth", "dir:billing"])
    auth, _ = mapping(state, "dir:auth", ["file:auth/login.py"], sentinel="AUTH_MAP_DETAIL")
    login, _ = mapping(state, "file:auth/login.py", ["symbol:auth/login.py:login"], sentinel="LOGIN_MAP_DETAIL")
    state.assistant({"action": "source", "node": "symbol:auth/login.py:login"})
    # Traversal is a proposal only. It has not classified billing as irrelevant.
    assert state.context_stats()["selection_count"] == 0
    assert {entry["id"] for entry in data(state)["navigation"]["main_index"]["entries"]} == {"dir:auth", "dir:billing"}
    select(state, [root, auth, login], focus="symbol:auth/login.py:login")
    source(state)
    state.assistant({"action": "test"})
    assert "AUTH_MAP_DETAIL" in text(state, "reasoner")
    assert data(state, "reasoner")["navigation"]["breadcrumb"]["ids"] == ["repo", "dir:auth", "file:auth/login.py", "symbol:auth/login.py:login"]
    billing, _ = mapping(state, "dir:billing", ["file:billing/invoice.py"], sentinel="BILLING_MAP_DETAIL")
    select(state, [billing], [auth, login], focus="file:billing/invoice.py", reason="Auth source is resident; investigate invoice behavior next")
    state.assistant({"action": "source", "node": "invoice"})
    assert "AUTH_MAP_DETAIL" not in text(state, "reasoner")
    assert "BILLING_MAP_DETAIL" in text(state, "reasoner")
    assert "EXACT_AUTH_BODY" in text(state, "reasoner")
    assert "AUTH_MAP_DETAIL" in state.restore(auth)
    with pytest.raises(ValueError, match="Replayed"):
        select(state, [auth], focus="file:auth/login.py")
    live_auth, _ = mapping(state, "dir:auth", ["file:auth/login.py"])
    select(state, [live_auth], [billing], focus="file:auth/login.py")
    assert data(state, "reasoner")["navigation"]["breadcrumb"]["ids"] == ["repo", "dir:auth", "file:auth/login.py"]
    assert root not in [item["handle"] for item in data(state, "reasoner")["navigation"]["selected_observations"]]


def test_defer_keeps_current_navigation_and_compact_pointers_in_both_roles(tmp_path):
    state = SelectedWorkingState(tmp_path, "session")
    handle, _ = mapping(state, "file:a.py", ["symbol:a.py:alpha"], cursor="30", sentinel="CURRENT_MAP_DETAIL")
    for role in ("selector", "reasoner"):
        assert "CURRENT_MAP_DETAIL" in text(state, role)
        assert data(state, role)["navigation"]["ledger"][0]["pending_cursor"] == "30"
    assert "retention_candidates" not in data(state, "reasoner")["navigation"]
    state.assistant({"action": "source", "node": "symbol:a.py:alpha"})
    assert data(state, "reasoner")["navigation"]["ledger"][0]["handle"] == handle
    assert state.context_stats()["selection_count"] == 0


def test_root_keep_is_protected_noop_and_root_archive_is_atomic_error(tmp_path):
    state = SelectedWorkingState(tmp_path, "session")
    root, _ = mapping(state, "repo", [f"dir:branch{i}" for i in range(70)], sentinel="X" * 100)
    candidate = state.retention_candidates()["candidates"][0]
    assert candidate["protected"] and not candidate["eligible"]
    report = select(state, [root])
    assert report["pointer_only_handles"] == [root]
    assert state.context_stats()["selected_count"] == 0
    before = state.render()
    with pytest.raises(ValueError, match="main index is protected"):
        select(state, archive=[root])
    assert state.render() == before


def test_oversized_keep_preserves_pointer_and_reports_no_exact_promotion(tmp_path):
    state = SelectedWorkingState(tmp_path, "session", ledger_bytes=2048)
    handle, _ = mapping(state, "file:huge.py", ["symbol:huge.py:alpha"], sentinel="BIG" * 1000)
    state.assistant({"action": "source", "node": "alpha"})
    report = select(state, [handle])
    assert report["pointer_only_handles"] == [handle]
    assert not report["retained_handles"]
    assert data(state, "reasoner")["navigation"]["ledger"][0]["handle"] == handle


def test_selected_exact_page_is_shown_once_and_render_is_idempotent(tmp_path):
    state = SelectedWorkingState(tmp_path, "session")
    handle, original = mapping(state, "file:a.py", ["symbol:a.py:a"], sentinel="ONCE_NAV_SENTINEL")
    select(state, [handle])
    for role in ("selector", "reasoner"):
        assert text(state, role).count("ONCE_NAV_SENTINEL") == 1
        assert state.render(role=role) == state.render(role=role)
    state.assistant({"action": "source", "node": "a"})
    for role in ("selector", "reasoner"):
        saved = data(state, role)["navigation"]["selected_observations"]
        assert saved[0]["observation"] == original
        assert text(state, role).count("ONCE_NAV_SENTINEL") == 1


def test_archived_page_preserves_pagination_and_empty_result_metadata(tmp_path):
    state = SelectedWorkingState(tmp_path, "session")
    handle, _ = mapping(state, "file:a.py", [], cursor="50", sentinel="unused")
    select(state, [handle])
    select(state, archive=[handle], reason="Read the next page; keep this page recoverable")
    for role in ("selector", "reasoner"):
        ledger = data(state, role)["navigation"]["ledger"]
        assert ledger[0]["pending_cursor"] == "50"
        assert ledger[0]["explicitly_archived"]
    indexed = json.loads(state.restore("archive:0"))["entries"]
    archived = next(entry for entry in indexed if entry["handle"] == handle)
    assert archived["empty_result"] and archived["pending_branch"]


@pytest.mark.parametrize("bad", ["unknown", "error", "replayed", "old_session", "source", "stale"])
def test_invalid_retention_is_atomic_and_does_not_prune_evidence(tmp_path, bad):
    state = SelectedWorkingState(tmp_path, "session")
    good, _ = mapping(state, "file:a.py", ["symbol:a.py:a"])
    select(state, [good])
    invalid, _ = mapping(state, "file:b.py", ["symbol:b.py:b"])
    if bad == "unknown":
        invalid = "obs-not-real"
    elif bad == "error":
        invalid = state.observe("map", {"node": "bad"}, "ERROR_SENTINEL", error="failed")
    elif bad == "replayed":
        state.restore(invalid)
    elif bad == "old_session":
        state.reset("new-session")
    elif bad == "source":
        invalid, _ = source(state)
    elif bad == "stale":
        state.invalidate("b.py")
    before = state.render()
    with pytest.raises(ValueError):
        select(state, [invalid], [good])
    assert state.render() == before


def test_cross_relation_map_never_invents_containment_or_automatic_focus(tmp_path):
    state = SelectedWorkingState(tmp_path, "session")
    handle, _ = mapping(state, "symbol:a.py:a", ["symbol:b.py:b"], relation="called_by")
    assert data(state)["navigation"]["breadcrumb"]["ids"] == []
    select(state, [handle], focus="symbol:b.py:b")
    branch = data(state)["navigation"]["breadcrumb"]
    assert branch["ids"] == ["symbol:b.py:b"] and not branch["complete"]
    with pytest.raises(ValueError, match="exact ID"):
        select(state, focus="guessed:unknown")


def test_source_hashes_errors_tests_edits_and_task_survive_handoff(tmp_path):
    state = SelectedWorkingState(tmp_path, "session")
    state.task("Initial task; never change the public API.")
    nav, _ = mapping(state, "file:auth/login.py", ["symbol:auth/login.py:login"])
    source_handle, result = source(state)
    state.assistant({"action": "answer", "answer": "Hypothesis: login calls a stale cache."})
    state.task("Now check invoice interaction.")
    state.select_for_handoff("Reasoner must verify the login hypothesis", [nav], [])
    state.observe("control", {}, "HANDOFF")
    for role in ("selector", "reasoner"):
        payload = data(state, role)
        assert "public API" in payload["initial_task"]
        assert payload["current_phase"] == "Now check invoice interaction."
        assert payload["retained_evidence"][0]["handle"] == source_handle
        assert "EXACT_AUTH_BODY" in text(state, role)
        assert "Hypothesis" in text(state, role)
    assert (result["id"], "full", result["hash"]) in state.resident_hashes()
    state.observe("map", {"node": "bad"}, "PROTECTED_TOOL_ERROR", error="failed")
    assert "PROTECTED_TOOL_ERROR" in text(state, "reasoner")
    state.assistant({"action": "test"})
    state.observe("test", {}, "FAILED_TEST_EVIDENCE", route={"passed": False})
    assert "PROTECTED_TOOL_ERROR" in text(state, "reasoner")
    assert "FAILED_TEST_EVIDENCE" in text(state, "reasoner")
    state.observe("edit", {"path": result["path"], "old": "stale", "new": "fresh"}, "edited")
    assert not state.resident_hashes()
    assert "EXACT_AUTH_BODY" not in text(state, "reasoner")
    assert nav in data(state, "reasoner")["navigation"]["stale_selected_handles"]
    state.assistant({"action": "test"})
    state.observe("test", {}, "PASS", route={"passed": True})
    assert data(state, "reasoner")["successful_edits"]["recent"][0]["new"] == "fresh"


def test_reset_same_session_id_clears_all_authority_and_selected_state(tmp_path):
    state = SelectedWorkingState(tmp_path, "session")
    nav, _ = mapping(state, "file:auth/login.py", ["symbol:auth/login.py:login"])
    select(state, [nav])
    source(state)
    state.reset("session", "Only supplied handoff task")
    assert not state.resident_hashes()
    assert state.context_stats()["selected_count"] == 0
    assert data(state)["initial_task"] == "Only supplied handoff task"
    with pytest.raises(ValueError, match="Historical session"):
        select(state, [nav])
    assert state.restore(nav)
    assert not state.resident_hashes()


def test_navigation_is_bounded_and_decisions_are_immutable_archive_records(tmp_path):
    state = SelectedWorkingState(tmp_path, "session", ledger_bytes=1800, ledger_entries=3)
    for i in range(12):
        handle, _ = mapping(state, f"file:f{i}.py", [f"symbol:f{i}.py:f"], cursor=str(i + 1))
        state.assistant({"action": "source", "node": f"symbol:f{i}.py:f"})
    report = select(state, [handle], reason="This file defines the active behavior")
    record = json.loads((tmp_path / (report["decision_handle"] + ".json")).read_text())
    assert record["action"] == "retention"
    assert record["input"]["reason"] == report["reason"]
    assert record["route"]["policy"] == "selected"
    for role in ("selector", "reasoner"):
        nav = data(state, role)["navigation"]
        assert len(json.dumps(nav, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= 1800
        assert state.render(role=role) == state.render(role=role)
    assert BranchWorkingState is SelectedWorkingState


def test_evicted_source_is_not_restored_by_navigation_selection(tmp_path):
    state = SelectedWorkingState(tmp_path, "session", evidence_bytes=600)
    nav, _ = mapping(state, "file:auth/login.py", ["symbol:auth/login.py:login"])
    original, result = source(state, body="A" * 2000)
    state.assistant({"action": "map", "node": "repo"})
    select(state, [nav])
    assert not state.resident_hashes()
    assert result["source"] not in text(state, "reasoner")
    with pytest.raises(ValueError, match="live read"):
        state.restore(original)


def test_reason_and_duplicate_handles_are_validated_without_mutation(tmp_path):
    state = SelectedWorkingState(tmp_path, "session")
    nav, _ = mapping(state, "file:a.py", ["symbol:a.py:a"])
    before = state.render()
    for decision in ({"reason": "", "retain_handles": [nav]},
                     {"reason": "why", "retain_handles": [nav, nav]},
                     {"reason": "why", "retain_handles": [nav], "archive_handles": [nav]}):
        with pytest.raises(ValueError):
            state.apply_retention(decision)
        assert state.render() == before


def test_stale_latest_navigation_is_a_pointer_not_current_raw_evidence(tmp_path):
    state = SelectedWorkingState(tmp_path, "session")
    handle, _ = mapping(state, "file:a.py", ["symbol:a.py:a"], sentinel="STALE_NAV_DETAIL")
    state.invalidate("a.py")
    for role in ("selector", "reasoner"):
        assert "STALE_NAV_DETAIL" not in text(state, role)
        entry = data(state, role)["navigation"]["ledger"][0]
        assert entry["handle"] == handle and entry["stale"]


@pytest.mark.parametrize("budget", [0, 24, 256, 1024, 1300, 2048])
def test_small_navigation_budgets_never_hide_selected_exact_observations(tmp_path, budget):
    state = SelectedWorkingState(tmp_path, "session", ledger_bytes=budget)
    for i in range(8):
        handle, _ = mapping(state, f"file:f{i}.py", [f"symbol:f{i}.py:f"])
        select(state, [handle])
        state.invalidate(f"f{i}.py")
    handle, original = mapping(state, "file:live.py", ["symbol:live.py:live"])
    report = select(state, [handle])
    state.assistant({"action": "source", "node": "live"})
    for role in ("selector", "reasoner"):
        nav = data(state, role)["navigation"]
        assert len(json.dumps(nav, ensure_ascii=False, separators=(",", ":")).encode()) <= max(2, budget)
        if handle in report["retained_handles"]:
            assert any(item["observation"] == original for item in nav["selected_observations"])


def test_archive_eligibility_is_independent_of_size_and_pagination(tmp_path):
    state = SelectedWorkingState(tmp_path, "session", ledger_bytes=2048)
    root, _ = mapping(state, "repo", ["dir:large"], cursor="30")
    large, _ = mapping(state, "file:large.py", ["symbol:large.py:large"], cursor="80", sentinel="BIG" * 1000)
    offered = {item["handle"]: item for item in state.retention_candidates()["candidates"]}
    assert not offered[root]["archive_eligible"]
    assert not offered[large]["eligible"] and offered[large]["archive_eligible"]
    select(state, archive=[large], reason="The visible page is not needed; keep its next-page pointer")
    for role in ("selector", "reasoner"):
        pending = data(state, role)["navigation"]["pending_pages"]
        assert any(item["handle"] == large and item["pending_cursor"] == "80" for item in pending)


def test_pending_cursor_survives_ledger_eviction_until_successful_matching_live_page(tmp_path):
    state = SelectedWorkingState(tmp_path, "session", ledger_entries=1)
    page, _ = mapping(state, "file:a.py", ["symbol:a.py:a"], cursor="30")
    select(state, archive=[page], reason="Store the page detail; follow its cursor later")
    mapping(state, "file:b.py", ["symbol:b.py:b"])
    state.assistant({"action": "source", "node": "b"})
    assert all(item["handle"] != page for item in data(state)["navigation"]["ledger"])
    assert data(state, "reasoner")["navigation"]["pending_pages"][0]["handle"] == page
    state.observe("map", {"node": "file:a.py", "cursor": "30"}, "request failed", error="failed")
    assert data(state)["navigation"]["pending_pages"][0]["handle"] == page
    state.restore(page)
    assert data(state)["navigation"]["pending_pages"][0]["handle"] == page
    state.observe("map", {"node": "file:a.py", "cursor": "30"}, json.dumps({"node": node("file:a.py"), "entries": []}))
    assert not data(state)["navigation"].get("pending_pages")


def test_combined_keep_overflow_keeps_existing_exact_page_and_new_pointer(tmp_path):
    state = SelectedWorkingState(tmp_path, "session", ledger_bytes=2048)
    first, first_text = mapping(state, "file:a.py", ["symbol:a.py:a"], sentinel="A" * 350)
    second, second_text = mapping(state, "file:b.py", ["symbol:b.py:b"], sentinel="B" * 350)
    select(state, [first])
    report = select(state, [first, second], reason="Keep both relevant pages as far as the explicit budget permits")
    assert report["retained_handles"] == [first]
    assert report["pointer_only_handles"] == [second]
    assert report["pointer_only_reasons"][second] == "selected_navigation_budget_full"
    assert not report["archived_handles"]
    state.assistant({"action": "source", "node": "b"})
    assert data(state, "reasoner")["navigation"]["selected_observations"][0]["observation"] == first_text
    assert any(item["handle"] == second and not item.get("explicitly_archived")
               for item in data(state, "reasoner")["navigation"]["ledger"])
    freed = select(state, [second], [first], reason="Source A is read; replace its navigation detail with B")
    assert freed["retained_handles"] == [second]
    assert "pointer_only_handles" not in freed
    assert data(state, "reasoner")["navigation"]["selected_observations"][0]["observation"] == second_text


def test_combined_overflow_still_validates_all_requested_handles_atomically(tmp_path):
    state = SelectedWorkingState(tmp_path, "session", ledger_bytes=2048)
    first, _ = mapping(state, "file:a.py", ["symbol:a.py:a"], sentinel="A" * 350)
    second, _ = mapping(state, "file:b.py", ["symbol:b.py:b"], sentinel="B" * 350)
    select(state, [first])
    before = state.render()
    with pytest.raises(ValueError):
        select(state, [second, "obs-invalid"], [first])
    assert state.render() == before
