"""Protocol equivalence and explicit evidence safeguards for the lean contract."""

import json

import pytest

from tool_contract import lean_schema, lean_system_prompt
from vnext_agent import schema, system_prompt


@pytest.mark.parametrize("mode", ["filesystem", "codemesh"])
@pytest.mark.parametrize("policy", ["full", "working-set", "focused"])
@pytest.mark.parametrize("bundles", [False, True])
def test_schema_preserves_actions_and_applicable_fields(mode, policy, bundles):
    legacy = json.loads(schema(mode, policy, bundles))
    lean = json.loads(lean_schema(mode, policy, bundles))
    if mode == "filesystem":
        for field in ("node", "relation", "cursor", "mode", "if_hash", "summary"):
            del legacy["properties"][field]
    assert lean == legacy
    assert lean["required"] == ["action", "answer"]
    assert lean["additionalProperties"] is False


@pytest.mark.parametrize("mode", ["filesystem", "codemesh"])
@pytest.mark.parametrize("policy", ["full", "working-set", "focused"])
@pytest.mark.parametrize("bundles", [False, True])
def test_instructions_and_schema_combined_are_shorter(mode, policy, bundles):
    legacy = system_prompt(mode, "auto", policy, bundles) + schema(mode, policy, bundles)
    lean = lean_system_prompt(mode, "auto", policy, bundles) + lean_schema(mode, policy, bundles)
    assert len(lean.encode()) < len(legacy.encode())


def test_critical_evidence_and_execution_rules_remain_explicit():
    prompt = lean_system_prompt("codemesh", context_policy="working-set", bundle_reads=True)
    for required in (
        "Locate with map/search; read with source/inspect.",
        "one JSON action", "Never call native tools", "Include answer", "checkout-relative",
        "Read exact source before editing", "unverified hypotheses", "pydantic/*.py only",
        "occur exactly once", "<=4000", "hidden tests unavailable", "at most 3 runs",
        "exact current source bytes resident in this session", "historical, never current source evidence",
        "re-run source/read for evicted code", "contain no source bytes", "request full for omitted edit code",
        "nextCursor", "max_bytes=512..65536", "Preserve unresolved branches",
        "1..8 explicit spans", "<=160 lines", "at most 4 spans", "24576 JSON bytes",
        "depth=0|1", "direction=outgoing|incoming|both", "are incomplete",
        "default 8 matches (limit 1..20); broaden if insufficient",
        "unused optional fields are null",
    ):
        assert required in prompt


def test_context_only_features_do_not_leak_to_full_history_or_filesystem():
    full = lean_system_prompt("codemesh")
    filesystem = lean_system_prompt("filesystem")
    assert "restore(" not in full
    assert "batch_read(" not in full
    assert "inspect(" not in full
    assert "if_hash" not in filesystem
    assert "recall(" not in filesystem
    assert "max_bytes" not in filesystem


def test_source_mode_is_described_without_changing_action_schema():
    prompt = lean_system_prompt("codemesh", source_mode="full")
    assert "default full" in prompt
    assert "mode=auto|fold|preview|full" in prompt
