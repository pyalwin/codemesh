"""Selector contract checks with scripted providers only; no paid calls."""

from copy import deepcopy
import json
import math

import pytest

from decisions_selector import CodexDecisionSelector, ESTIMATE, selection_schema


CANDIDATES = [{"value": "read_field", "label": "Read the exact field resolver",
               "action": {"action": "source", "node": "symbol:pydantic/fields.py:get_default",
                          "mode": "full", "answer": ""}},
              {"value": "find_calls", "label": "Find callers",
               "action": {"action": "search", "query": "get_default", "answer": ""}}]
RETENTION = [{"handle": "map-old", "action": "map", "summary": "Unrelated subsystem", "protected": False},
             {"handle": "search-old", "action": "search", "summary": "Alternative candidate", "protected": False}]


def decision(**overrides):
    return {"choice": "read_field", "confidence": 0.9, "reason": "Exact resolver is relevant.", "refused": False,
            "retention": [{"handle": "map-old", "decision": "archive", "confidence": 0.99,
                           "reason": "Unrelated to field defaults."},
                          {"handle": "search-old", "decision": "defer", "confidence": 0.7,
                           "reason": "Alternative remains uncertain."}], **overrides}


def call(value=None, **overrides):
    return {"decision": value if value is not None else decision(), "call_valid": True,
            "usage_available": True, "input_tokens": 1234, "cache_read_input_tokens": 100,
            "output_tokens": 57, "cost_usd": None, "elapsed_ms": 13,
            "raw_events": [{"type": "turn.completed", "usage": {"input_tokens": 1234}}],
            "stderr": "", "input_artifacts": {"directory": "/audit/call-1"}, **overrides}


def select(value=None, *, records=RETENTION, **kwargs):
    row = value or call()
    selector = CodexDecisionSelector(caller=lambda *args, **kw: row, **kwargs)
    return selector.select("Repair the factory.", {"source": "exact evidence"}, CANDIDATES, deepcopy(records))


def assert_safe(result):
    assert result["action"]["action"] == "escalate"
    assert all(item["decision"] == "keep" for item in result["retention"])


def test_selector_only_maps_a_precomputed_action_and_preserves_actual_accounting(tmp_path):
    row = call()
    captured = {}

    def provider(transcript, **kwargs):
        captured.update(kwargs, transcript=transcript)
        return row

    selector = CodexDecisionSelector(caller=provider)
    result = selector.select("Repair factory", {"code": "original evidence"}, CANDIDATES, RETENTION, tmp_path)
    assert result["action"] == CANDIDATES[0]["action"]
    result["action"]["node"] = "different"
    assert CANDIDATES[0]["action"]["node"].startswith("symbol:")
    assert result["call"] is row  # including raw response, error diagnostics and usage
    assert result["retention"][0]["decision"] == "archive"
    assert all(ESTIMATE in item["reason"] for item in result["retention"])
    assert captured["model"] == "gpt-6-luna" and captured["effort"] == "low"
    assert captured["artifact_dir"] == tmp_path
    payload = json.loads(captured["transcript"][0]["content"])
    assert payload["candidates"] == CANDIDATES
    assert payload["evidence"] == {"code": "original evidence"}
    assert captured["schema"]["properties"]["choice"]["enum"] == ["read_field", "find_calls", "escalate"]
    assert "action" not in captured["schema"]["properties"]
    assert "not a calibrated" in captured["system"]


@pytest.mark.parametrize("override", [
    {"choice": "invented_action"}, {"choice": []}, {"confidence": math.nan},
    {"confidence": math.inf}, {"confidence": -0.1}, {"confidence": 1.1},
    {"confidence": True}, {"confidence": "0.99"}, {"confidence": 0.74},
    {"reason": ""}, {"reason": 5}, {"refused": True}, {"refused": "false"},
    {"tool_arguments": {"path": "invented.py"}},
])
def test_invalid_refused_or_low_confidence_actions_escalate_without_removal(override):
    row = call(decision(**override))
    result = select(row)
    assert_safe(result)
    assert result["call"] is row
    assert result["call"]["input_tokens"] == 1234


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "missing", "extra", "nan", "decision", "reason"])
def test_nested_retention_is_fully_validated_before_any_action_or_archive(mutation):
    value = decision()
    if mutation == "unknown":
        value["retention"][0]["handle"] = "not-supplied"
    elif mutation == "duplicate":
        value["retention"][1]["handle"] = "map-old"
    elif mutation == "missing":
        value["retention"].pop()
    elif mutation == "extra":
        value["retention"][0]["tool_args"] = {"path": "fake.py"}
    elif mutation == "nan":
        value["retention"][0]["confidence"] = math.nan
    elif mutation == "decision":
        value["retention"][0]["decision"] = "delete_archive"
    elif mutation == "reason":
        value["retention"][0]["reason"] = ""
    assert_safe(select(call(value)))


@pytest.mark.parametrize("protected", [
    {"protected": True}, {"action": "source"}, {"action": "read"}, {"action": "test"},
    {"action": "edit"}, {"action": "unknown"}, {"error": "failed"}, {"main_index": True},
    {"node": "repo"}, {"input": {"node": "repo"}}, {"archive_eligible": False},
])
def test_protected_evidence_and_orientation_cannot_be_archived(protected):
    records = deepcopy(RETENTION)
    records[0].update(protected)
    result = select(records=records)
    assert result["action"]["action"] == "source"
    assert result["retention"][0]["decision"] == "keep"
    assert "protected" in result["retention"][0]["reason"]


@pytest.mark.parametrize("pagination", [{"pending_cursor": "page2"}, {"pending_cursor_archived": True}])
def test_paginated_detail_can_be_archived_when_harness_preserves_recovery(pagination):
    records = deepcopy(RETENTION)
    records[0].update(pagination, archive_eligible=True, eligible=False)
    result = select(records=records)
    assert result["retention"][0]["decision"] == "archive"
    assert result["action"] == CANDIDATES[0]["action"]


def test_archive_eligibility_is_independent_of_retained_page_size():
    records = deepcopy(RETENTION)
    records[0].update(archive_eligible=True, eligible=False, bytes=20_000,
                      ineligible_reason="Too large to promote")
    result = select(records=records)
    assert result["retention"][0]["decision"] == "archive"


def test_weak_archive_confidence_defers_instead_of_dropping():
    value = decision()
    value["retention"][0]["confidence"] = 0.94
    result = select(call(value))
    assert result["retention"][0]["decision"] == "defer"
    assert "no removal authorized" in result["retention"][0]["reason"]


def test_confident_explicit_escalation_compacts_valid_navigation_at_handoff():
    result = select(call(decision(choice="escalate")))
    assert result["action"]["action"] == "escalate"
    assert result["retention"][0]["decision"] == "archive"
    assert result["retention"][1]["decision"] == "defer"
    assert "error" not in result  # a legitimate controller choice, not protocol failure


def test_uncertain_escalation_preserves_every_record():
    result = select(call(decision(choice="escalate", confidence=0.7)))
    assert_safe(result)


def test_confident_escalation_still_protects_source_and_low_confidence_alternatives():
    records = deepcopy(RETENTION)
    records[0]["action"] = "source"
    value = decision(choice="escalate")
    value["retention"][1].update(decision="archive", confidence=0.94)
    result = select(call(value), records=records)
    assert result["action"]["action"] == "escalate"
    assert [item["decision"] for item in result["retention"]] == ["keep", "defer"]


@pytest.mark.parametrize("override", [{"error": "refusal"}, {"call_valid": False}, {"usage_available": False}])
def test_provider_failure_preserves_raw_usage_and_authorizes_no_drop(override):
    row = call(**override)
    result = select(row)
    assert_safe(result)
    assert result["call"] is row
    assert result["call"]["raw_events"] == row["raw_events"]


def test_provider_exception_has_unknown_usage_and_is_not_retried():
    calls = []
    def provider(*args, **kwargs):
        calls.append(args)
        raise OSError("unavailable")
    result = CodexDecisionSelector(caller=provider).select("task", {}, CANDIDATES, RETENTION)
    assert_safe(result)
    assert len(calls) == 1
    assert result["call"]["usage_available"] is False
    assert result["call"]["cost_usd"] is None
    assert "input_tokens" not in result["call"]


def test_empty_retention_is_supported_without_inventing_handles():
    result = select(call(decision(retention=[])), records=[])
    assert result["retention"] == []
    assert selection_schema(["read_field"], [])["properties"]["retention"]["maxItems"] == 0


@pytest.mark.parametrize("kind", ["too_many_actions", "too_many_records", "duplicate_value", "reserved_value",
                                 "duplicate_handle", "edit", "nonfinite_payload"])
def test_bad_caller_input_fails_before_any_model_call(kind):
    actions, records, evidence = deepcopy(CANDIDATES), deepcopy(RETENTION), {}
    if kind == "too_many_actions":
        actions *= 11
    elif kind == "too_many_records":
        records *= 5
    elif kind == "duplicate_value":
        actions[1]["value"] = actions[0]["value"]
    elif kind == "reserved_value":
        actions[0]["value"] = "escalate"
    elif kind == "duplicate_handle":
        records[1]["handle"] = records[0]["handle"]
    elif kind == "edit":
        actions[0]["action"] = {"action": "edit", "path": "code.py"}
    elif kind == "nonfinite_payload":
        evidence = {"number": math.nan}
    calls = []
    selector = CodexDecisionSelector(caller=lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError):
        selector.select("task", evidence, actions, records)
    assert calls == []
