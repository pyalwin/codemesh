from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import selected_context_eval as pilot


def test_candidates_use_observed_ids_preserve_pagination_and_offer_backtracking():
    pool = pilot.ActionCandidates()
    pool.observe({"action": "map", "node": "repo"}, json.dumps({
        "entries": [{"id": "dir:auth", "name": "authentication"},
                    {"id": "dir:billing", "name": "billing"}], "nextCursor": "2"}))
    choices = pool.choices()
    assert len(choices) <= 20
    assert any(c["action"].get("cursor") == "2" for c in choices)
    assert any(c["action"].get("node") == "dir:auth" for c in choices)
    assert choices[-1]["action"] == {"action": "map", "node": "repo"}
    before = pool.choices()
    pool.observe({"action": "map", "node": "dir:auth"}, '{"entries":[{"id":"invented"}]}', "failure")
    assert pool.choices() == before


def test_unavailable_usage_is_not_zero_and_roles_have_same_terra_reasoner():
    assert pilot.totals([{"input_tokens": None}])["input_tokens"] is None
    assert pilot.totals([])["input_tokens"] == 0
    assert "request_context" in pilot.reasoner_schema("selected")["properties"]["action"]["enum"]
    assert "edit" in pilot.reasoner_schema("full")["properties"]["action"]["enum"]
    assert "restore" in pilot.reasoner_schema("working-set")["properties"]["action"]["enum"]


def test_selector_receives_descriptors_but_reasoner_keeps_original_source(tmp_path):
    state = pilot.SelectedWorkingState(tmp_path / "archive", "s")
    state.task("Explain login; preserve rate limiting")
    code = "def login():\n    return UNIQUE_SOURCE_MARKER\n"
    state.observe("source", {"node": "symbol:pydantic/auth.py:login"}, json.dumps({
        "id": "symbol:pydantic/auth.py:login", "path": "pydantic/auth.py", "status": "fresh", "mode": "auto",
        "source": code, "hash": sha256(code.encode()).hexdigest(), "lines": [1, 2]}))
    for consume in (False, True):
        if consume:
            state.assistant({"action": "escalate", "answer": "Read complete"})
        assert "UNIQUE_SOURCE_MARKER" not in json.dumps(pilot.selector_context(state))
        assert "UNIQUE_SOURCE_MARKER" in json.dumps(state.render(role="reasoner"))
        assert "rate limiting" in json.dumps(pilot.selector_context(state))
    assert pilot.requested_action({"node": "symbol:pydantic/auth.py:login", "mode": "full"})["mode"] == "full"


def test_hybrid_counts_both_roles_and_passes_exact_source_to_reasoner(tmp_path):
    source = "def login():\n    return broken\n"
    node = "symbol:pydantic/example.py:login"
    task = {"id": "case", "symptom": "Login returns the wrong value", "impact_question": "Preserve callers",
            "hidden_test": "unused.py", "path": "pydantic/example.py"}
    selector_steps = iter([{"action": "source", "node": node, "mode": "auto"},
                           {"action": "escalate", "answer": "Ready for reasoning"}])
    reasoner_steps = iter([{"action": "edit", "path": "pydantic/example.py", "old": "broken", "new": "fixed", "answer": ""},
                          {"action": "test", "answer": ""}, {"action": "answer", "answer": "Fixed and tested"}])
    seen = []

    def call(action):
        return {"decision": action, "text": json.dumps(action), "input_tokens": 100, "output_tokens": 10,
                "cache_read_input_tokens": 0, "elapsed_ms": 5, "cost_usd": None,
                "call_valid": True, "usage_available": True}

    def selector(self, task, evidence, candidates, retention, artifacts):
        action = next(selector_steps)
        return {"action": action, "retention": [], "call": call(action)}

    def reasoner(context, **kwargs):
        assert kwargs["model"] == "gpt-5.6-terra"
        seen.append(json.dumps(context))
        return call(next(reasoner_steps))

    def tool(action, *args, **kwargs):
        if action["action"] == "map":
            result = {"node": {"id": "repo"}, "entries": [{"id": node, "name": "login", "type": "symbol"}]}
        elif action["action"] == "source":
            result = {"id": node, "path": "pydantic/example.py", "status": "fresh", "mode": "auto",
                      "source": source, "hash": sha256(source.encode()).hexdigest(), "lines": [1, 2]}
        else:
            return action["action"], "done", {"passed": True}
        return action["action"], json.dumps(result), {"status": "fresh"}

    with patch.object(pilot.old, "worktree"), patch.object(pilot.old, "remove_worktree"), \
            patch.object(pilot.old, "tool", side_effect=tool), \
            patch.object(pilot.old, "pytest", return_value={"passed": True}), \
            patch.object(pilot.old, "command") as command, \
            patch.object(pilot.CodexDecisionSelector, "select", selector), \
            patch.object(pilot, "model_call", side_effect=reasoner):
        command.return_value.stdout = "patch"
        result = pilot.run_case(tmp_path, task, "selected", tmp_path / "index", tmp_path / "out", Path("/unused"), 6)
    assert result["status"] == "complete" and result["final_patch_pass"]
    assert result["by_role"]["selector"]["calls"] == 2
    assert result["by_role"]["reasoner"]["calls"] == 3
    assert result["totals"]["input_tokens"] == 500
    assert "return broken" in seen[0]
    assert "return broken" not in seen[1]  # Edited source was invalidated.


def latest_descriptor(state):
    messages = pilot.selector_context(state)
    latest = next(message["content"] for message in messages if message["content"].startswith("Latest evidence descriptor:\n"))
    return json.loads(latest.split("\n", 1)[1])


def source_result(**changes):
    code = "def human_readable():\n    return 'SOURCE_BYTES'"
    return {"id": "symbol:pydantic/types.py:ByteSize.human_readable", "path": "pydantic/types.py",
            "status": "fresh", "mode": "auto", "source": code,
            "hash": sha256(code.encode()).hexdigest(), "lines": [2182, 2211], **changes}


@pytest.mark.parametrize("status", ["stale", "deleted", "fresh"])
def test_null_source_is_unavailable_and_retains_observed_recovery_hint(tmp_path, status):
    state = pilot.SelectedWorkingState(tmp_path, "s")
    result = source_result(status=status, source=None, hash=None, hint="Run codemesh index, then request the symbol")
    state.observe("source", {"node": result["id"], "mode": "full"}, json.dumps(result))
    descriptor = latest_descriptor(state)
    assert not descriptor["exact_source_available_to_reasoner"]
    assert not descriptor["source_view_available_to_reasoner"]
    assert descriptor["source_bytes"] == 0
    assert descriptor["source_descriptor"]["hint"] == result["hint"]
    assert descriptor["recovery_action"] == {"action": "read", "path": "pydantic/types.py", "start": 2174, "limit": 46}
    assert "navigation hints" in descriptor["recovery_basis"]


def test_exact_body_preview_and_oversized_latest_have_distinct_availability(tmp_path):
    state = pilot.SelectedWorkingState(tmp_path / "regular", "s")
    result = source_result()
    state.observe("source", {"node": result["id"]}, json.dumps(result))
    descriptor = latest_descriptor(state)
    assert descriptor["exact_source_available_to_reasoner"]
    assert descriptor["view_kind"] == "complete_symbol"
    assert descriptor["source_bytes"] == len(result["source"].encode())
    state.assistant({"action": "source", "node": result["id"]})
    preview = source_result(omittedRanges=[{"start": 2190, "end": 2200}], hint="Bounded preview; request full")
    state.observe("source", {"node": result["id"]}, json.dumps(preview))
    descriptor = latest_descriptor(state)
    assert descriptor["source_view_available_to_reasoner"]
    assert not descriptor["exact_source_available_to_reasoner"]
    assert descriptor["view_kind"] == "bounded_preview"
    assert descriptor["source_descriptor"]["omittedRanges"] == preview["omittedRanges"]
    assert descriptor["source_descriptor"]["hint"] == preview["hint"]
    huge = pilot.SelectedWorkingState(tmp_path / "huge", "s", evidence_bytes=100)
    huge.observe("source", {"node": result["id"]}, json.dumps(result))
    descriptor = latest_descriptor(huge)
    assert descriptor["view_kind"] == "complete_symbol"
    assert not descriptor["exact_source_available_to_reasoner"]
    assert descriptor["delivery"] == "latest_only_not_resident"
    huge.assistant({"action": "escalate"})
    huge.observe("control", {}, "Ready for reasoner")
    assert "SOURCE_BYTES" not in json.dumps(huge.render(role="reasoner"))


def test_unchanged_response_requires_resident_matching_original_source(tmp_path):
    state = pilot.SelectedWorkingState(tmp_path / "with", "s")
    result = source_result()
    state.observe("source", {"node": result["id"]}, json.dumps(result))
    state.assistant({"action": "source", "node": result["id"], "if_hash": result["hash"]})
    unchanged = {**result, "status": "unchanged", "source": None}
    state.observe("source", {"node": result["id"]}, json.dumps(unchanged))
    descriptor = latest_descriptor(state)
    assert descriptor["exact_source_available_to_reasoner"]
    assert descriptor["source_bytes"] == 0
    assert descriptor["resident_source_bytes"] == len(result["source"].encode())
    empty = pilot.SelectedWorkingState(tmp_path / "without", "s")
    empty.observe("source", {"node": result["id"]}, json.dumps(unchanged))
    assert not latest_descriptor(empty)["exact_source_available_to_reasoner"]


def test_error_or_invalid_hash_never_claims_exact_source(tmp_path):
    state = pilot.SelectedWorkingState(tmp_path, "s")
    result = source_result(hash="incorrect")
    state.observe("source", {"node": result["id"]}, json.dumps(result))
    assert not latest_descriptor(state)["exact_source_available_to_reasoner"]
    state.observe("source", {"node": result["id"]}, json.dumps(source_result(hint="Retry after refresh")), error="tool failure")
    descriptor = latest_descriptor(state)
    assert not descriptor["exact_source_available_to_reasoner"] and descriptor["source_bytes"] == 0
    assert descriptor["error"] == "tool failure"
    assert descriptor["source_descriptor"]["hint"] == "Retry after refresh"


@pytest.mark.parametrize("path", ["../outside.py", "/tmp/outside.py", "pydantic/../outside.py", "pydantic\\types.py", ".", "bad\0path"])
def test_recovery_rejects_unsafe_observed_paths(path):
    assert pilot.source_recovery_action(source_result(path=path, status="stale")) is None


@pytest.mark.parametrize("lines,expected", [([3, 9000], (1, 160)), ([True, 5], (1, 60)), ([8, 2], (1, 60)), ([9], (1, 60))])
def test_recovery_ranges_are_bounded_navigation_hints(lines, expected):
    result = pilot.source_recovery_action(source_result(status="stale", lines=lines))
    assert (result["start"], result["limit"]) == expected


def test_candidate_pool_does_not_complete_failed_source_and_reopens_recovery_read():
    pool = pilot.ActionCandidates()
    result = source_result(status="stale", source=None, hash=None)
    action = {"action": "source", "node": result["id"], "mode": "full"}
    recovery = pilot.source_recovery_action(result)
    pool.observe(recovery, "pydantic/types.py lines 2174-2174 of 3000:\n2174: previous read")
    assert pilot.action_key(recovery) in pool.completed
    pool.add(action, "Read symbol")
    pool.observe(action, json.dumps(result))
    assert pilot.action_key(action) not in pool.completed
    assert pilot.action_key(recovery) not in pool.completed
    assert any(item["action"] == recovery for item in pool.choices())
    assert any("navigation only" in item["label"] for item in pool.choices() if item["action"] == recovery)
    pool.observe(action, json.dumps(source_result()))
    assert pilot.action_key(action) in pool.completed
    pool.observe(action, "failed", error="failed")
    assert pilot.action_key(action) not in pool.completed


def test_preview_offers_full_source_and_read_descriptor_is_a_resident_live_span(tmp_path):
    pool = pilot.ActionCandidates()
    preview = source_result(omittedRanges=[{"start": 2190, "end": 2200}])
    pool.observe({"action": "source", "node": preview["id"], "mode": "auto"}, json.dumps(preview))
    assert any(item["action"] == {"action": "source", "node": preview["id"], "mode": "full"} for item in pool.choices())
    state = pilot.SelectedWorkingState(tmp_path, "s")
    original = "pydantic/types.py lines 2174-2175 of 3000:\n2174: def human_readable():\n2175:     return 'CURRENT_CODE'"
    state.observe("read", {"path": "pydantic/types.py", "start": 2174, "limit": 46}, original)
    descriptor = latest_descriptor(state)
    assert descriptor["exact_source_available_to_reasoner"]
    assert descriptor["view_kind"] == "live_file_span"
    assert not descriptor["complete_symbol_available_to_reasoner"]
    assert descriptor["source_descriptor"]["path"] == "pydantic/types.py"
    state.assistant({"action": "escalate"})
    state.observe("control", {}, "Handoff")
    assert "CURRENT_CODE" in json.dumps(state.render(role="reasoner"))
    assert "CURRENT_CODE" not in json.dumps(pilot.selector_context(state))


def test_post_edit_stale_source_recovers_through_live_read_before_handoff(tmp_path):
    task = {"id": "recovery", "symptom": "Wrong return value", "impact_question": "Preserve callers",
            "hidden_test": "unused.py", "path": "pydantic/types.py"}
    original = source_result(source="def human_readable():\n    return broken", lines=[20, 21])
    original["hash"] = sha256(original["source"].encode()).hexdigest()
    stale = {**original, "status": "stale", "source": None, "hash": None, "hint": "Reindex before requesting this symbol"}
    recovery = pilot.source_recovery_action(stale)
    request = {"action": "source", "node": original["id"], "mode": "full"}
    selector_steps = iter([request, {"action": "escalate"}, request, recovery, {"action": "escalate"}])
    reasoner_steps = iter([
        {"action": "edit", "path": original["path"], "old": "broken", "new": "fixed"},
        {"action": "test"}, {"action": "request_context", "node": original["id"], "mode": "full"},
        {"action": "answer", "answer": "Verified current source and tests"}])
    live = "pydantic/types.py lines 12-13 of 80:\n12: def human_readable():\n13:     return fixed"
    source_calls = 0
    reasoner_prompts = []
    recovery_seen = []

    def call(action):
        return {"decision": action, "text": json.dumps(action), "input_tokens": 100, "output_tokens": 10,
                "cache_read_input_tokens": 0, "elapsed_ms": 5, "cost_usd": None,
                "call_valid": True, "usage_available": True}

    def selector(self, task, evidence, candidates, retention, artifacts):
        action = next(selector_steps)
        if action == recovery:
            descriptor = json.loads(next(message["content"].split("\n", 1)[1] for message in evidence
                                         if message["content"].startswith("Latest evidence descriptor:")))
            assert descriptor["source_descriptor"]["status"] == "stale"
            assert not descriptor["exact_source_available_to_reasoner"]
            assert descriptor["source_descriptor"]["hint"] == stale["hint"]
            assert any(candidate["action"] == recovery for candidate in candidates)
            recovery_seen.append(True)
        return {"action": action, "retention": [], "call": call(action)}

    def reasoner(context, **kwargs):
        reasoner_prompts.append(json.dumps(context))
        return call(next(reasoner_steps))

    def tool(action, *args, **kwargs):
        nonlocal source_calls
        if action["action"] == "map":
            return "map", json.dumps({"node": {"id": "repo"}, "entries": [{"id": original["id"], "name": "human_readable"}]}), {}
        if action["action"] == "source":
            source_calls += 1
            result = original if source_calls == 1 else stale
            return "source", json.dumps(result), {"status": result["status"]}
        if action["action"] == "read":
            return "read", live, {"backend": "filesystem"}
        return action["action"], "done", {"passed": True}

    with patch.object(pilot.old, "worktree"), patch.object(pilot.old, "remove_worktree"), \
            patch.object(pilot.old, "tool", side_effect=tool), \
            patch.object(pilot.old, "pytest", return_value={"passed": True}), \
            patch.object(pilot.old, "command") as command, \
            patch.object(pilot.CodexDecisionSelector, "select", selector), \
            patch.object(pilot, "model_call", side_effect=reasoner):
        command.return_value.stdout = "patch"
        result = pilot.run_case(tmp_path, task, "selected", tmp_path / "index", tmp_path / "out", Path("/unused"), 10)
    assert result["status"] == "complete" and result["final_patch_pass"]
    assert recovery_seen == [True]
    assert "return broken" in reasoner_prompts[0]
    assert "return broken" not in reasoner_prompts[-1]
    assert "13:     return fixed" in reasoner_prompts[-1]
    assert result["by_role"]["selector"]["calls"] == 5
    assert result["by_role"]["reasoner"]["calls"] == 4
