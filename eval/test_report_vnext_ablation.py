import json
from pathlib import Path

import pytest

import report_vnext_ablation as report


def config(name, **changes):
    context, hydrate, bundle = report.VARIANTS[name]
    return {"task_ids": ["task"], "repeats": 2, "modes": list(report.ARMS),
            "model": "gpt-6-luna", "provider": "codex", "seed": 42,
            "protocol_fingerprint": "same", "build_fingerprint": "same",
            "context_policy": context, "hydrate_on_resume": hydrate,
            "bundle_reads": bundle, **changes}


def call(tokens=10, **changes):
    return {"kind": "decision", "phase": 0, "turn": 1, "input_tokens": tokens,
            "uncached_input_tokens": tokens, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "output_tokens": 2, "elapsed_ms": 100,
            "cost_usd": None, "call_valid": True, **changes}


def row(arm="filesystem", repeat=1, tokens=10, **changes):
    return {"attempt_id": f"{arm}-{repeat}", "task": "task", "repeat": repeat,
            "mode": arm, "status": "complete", "calls": [call(tokens)],
            "totals": report.usage([call(tokens)]), "first_patch_pass": True,
            "final_patch_pass": True, "phases": [], **changes}


def write_variant(root: Path, name: str, rows=(), calls=None, **changes):
    directory = root / name
    directory.mkdir()
    (directory / "run_config.json").write_text(json.dumps(config(name, **changes)))
    (directory / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if calls is not None:
        (directory / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
    return directory


def test_partial_comparison_uses_matching_keys_only_and_keeps_failed_quality(tmp_path):
    write_variant(tmp_path, "full", [row(repeat=1, tokens=100), row(repeat=2, tokens=10000)])
    write_variant(tmp_path, "working", [row(repeat=1, tokens=50, final_patch_pass=False)])
    result = report.build_report(tmp_path)
    comparison = result["comparisons"]["working_vs_full"]["filesystem"]
    assert result["complete"] is False
    assert (result["graded_trajectories"], result["expected_trajectories"]) == (3, 20)
    assert comparison["matched_pairs"] == 1
    assert comparison["usage_change_percent"]["input_tokens"] == -50
    assert comparison["quality_change_percentage_points"] == -100
    assert comparison["both_successful"]["pairs"] == 0
    assert comparison["unmatched_baseline"] == [["task", 2]]
    assert comparison["usage_change_percent"]["cost_usd"] is None
    assert "Partial snapshot" in report.markdown(result)


@pytest.mark.parametrize("key,value", [("protocol_fingerprint", "different"), ("build_fingerprint", "different"),
                                       ("seed", 5), ("repeats", 3), ("model", "other")])
def test_configuration_drift_rejects_aggregation(tmp_path, key, value):
    write_variant(tmp_path, "full")
    write_variant(tmp_path, "working", **{key: value})
    with pytest.raises(ValueError, match="Incompatible run configurations"):
        report.build_report(tmp_path)


def test_wrong_variant_flags_and_duplicate_successes_are_rejected(tmp_path):
    directory = write_variant(tmp_path, "combined", context_policy="full")
    with pytest.raises(ValueError, match="variant flags"):
        report.build_report(tmp_path)
    (directory / "run_config.json").write_text(json.dumps(config("combined")))
    (directory / "runs.jsonl").write_text(json.dumps(row()) + "\n" + json.dumps(row()) + "\n")
    with pytest.raises(ValueError, match="Duplicate graded"):
        report.build_report(tmp_path)


def test_failed_and_inflight_spend_are_visible_without_affecting_graded_comparisons(tmp_path):
    failed = row(repeat=2, tokens=7, status="model_error", attempt_id="failed")
    calls = [call(10, mode="filesystem", attempt_id="filesystem-1"),
             call(7, mode="filesystem", attempt_id="failed", call_valid=False, router_error_count=1),
             call(3, mode="filesystem", attempt_id="running"),
             call(None, mode="filesystem", attempt_id="running", call_valid=False)]
    write_variant(tmp_path, "working", [row(), failed], calls=calls)
    result = report.build_report(tmp_path)["variants"]["working"]["by_arm"]["filesystem"]
    assert result["usage"]["input_tokens"] == 10
    assert result["all_attempt_usage"]["input_tokens"] is None
    assert result["all_attempt_usage_known_subtotal"]["input_tokens"] == 20
    assert result["all_attempt_unknown_usage_calls"]["input_tokens"] == 1
    assert result["all_attempt_usage"]["cost_usd"] is None
    assert result["runner_errors"] == 1
    assert result["inflight_attempts"] == 1
    assert result["all_attempt_router_errors"] == 1
    assert result["all_attempt_invalid_calls"] == 2


def test_correct_edit_boundary_handles_auto_hydration_and_rejected_actions():
    calls = [call(1), call(2, turn=2), call(3, kind="handoff", phase=2, turn=None),
             call(4, phase=2), call(5, phase=2, turn=2), call(6, phase=2, turn=3)]
    data = row(calls=calls, totals=report.usage(calls), first_correct_edit={"tool_calls_before_edit": 2},
        phases=[{"phase": 0, "actions": [{"action": "source"}, {"action": "answer"}]},
                {"phase": 2, "actions": [{"action": "hydrate"}, {"action": "premature_answer"},
                                         {"action": "edit"}, {"action": "test"}]}])
    assert report.correct_edit_boundary(data) == 4
    aggregate = report.aggregate([data])
    assert aggregate["first_correct_edit"]["through_edit"]["input_tokens"] == 15
    assert aggregate["first_correct_edit"]["after_edit"]["input_tokens"] == 6
    assert aggregate["phase_usage"]["handoff"]["input_tokens"] == 3
    assert aggregate["phase_usage"]["editing"]["input_tokens"] == 15


def test_concurrent_incomplete_journal_tail_is_ignored_but_corruption_is_rejected(tmp_path):
    directory = write_variant(tmp_path, "working", [row()])
    path = directory / "runs.jsonl"
    with path.open("a") as file:
        file.write('{"attempt_id":')
    result = report.build_report(tmp_path)
    assert result["ignored_incomplete_journal_tails"] == ["working"]
    with path.open("a") as file:
        file.write("\n")
    with pytest.raises(ValueError, match="Invalid completed JSON"):
        report.build_report(tmp_path)


def test_complete_matrix_and_zero_cost_remain_distinct_from_unknown(tmp_path):
    rows = [row(arm, repeat) for arm in report.ARMS for repeat in (1, 2)]
    for name in report.VARIANTS:
        write_variant(tmp_path, name, rows)
    result = report.build_report(tmp_path)
    assert result["complete"] is True
    assert result["graded_trajectories"] == 20
    assert result["variants"]["full"]["codemesh_vs_filesystem"]["matched_pairs"] == 2
    assert report.total([{"cost_usd": 0}], "cost_usd") == 0
    assert report.total([{"cost_usd": None}], "cost_usd") is None
    assert report.change(1, 0) == -100
    assert report.change(0, 1) is None


def test_matrix_plan_supplies_expected_count_before_runners_start(tmp_path):
    (tmp_path / "matrix_config.json").write_text(json.dumps({"tasks": ["task"], "repeats": 2,
                                                           "protocol_fingerprint": "same"}))
    result = report.build_report(tmp_path)
    assert result["expected_trajectories"] == 20
    assert result["graded_trajectories"] == 0
    assert result["complete"] is False
    write_variant(tmp_path, "full", protocol_fingerprint="different")
    with pytest.raises(ValueError, match="differs from planned matrix"):
        report.build_report(tmp_path)


def test_unavailable_provider_usage_zero_placeholders_are_not_known_zero(tmp_path):
    missing = call(0, usage_available=False, mode="filesystem", attempt_id="running")
    write_variant(tmp_path, "working", [row()], calls=[missing])
    result = report.build_report(tmp_path)["variants"]["working"]["by_arm"]["filesystem"]
    assert result["all_attempt_usage"]["input_tokens"] is None
    assert result["all_attempt_unknown_usage_calls"]["input_tokens"] == 1
    assert result["all_attempt_usage"]["elapsed_ms"] == 100
    context_limited = row(calls=[missing], status="context_limit")
    assert report.aggregate([context_limited])["usage"]["input_tokens"] is None


def test_inspect_callsites_follow_public_status_and_resolution_contract():
    observation = {"status": "fresh", "sources": [], "calls": [
        {"expression": "local()", "line": 1, "resolution": "same_file_function", "status": "included"},
        {"expression": "dynamic()", "line": 2, "resolution": "unresolved", "status": "unresolved"},
        {"expression": "overloaded()", "line": 3, "resolution": "ambiguous", "status": "ambiguous"},
        {"expression": "maybe()", "line": 4, "resolution": "unverified_graph", "status": "unresolved"},
        {"expression": "too_large()", "line": 5, "resolution": "imported_function", "status": "omitted"}],
        "omitted": {"sources": 1, "calls": 0}}
    data = row(phases=[{"phase": 0, "actions": [{"action": "inspect", "observation": json.dumps(observation)}]}])
    result = report.aggregate([data])
    assert result["unresolved_inspect_callsites"] == 2
    assert result["ambiguous_inspect_callsites"] == 1
    assert result["unverified_graph_inspect_callsites"] == 1
    assert result["inspect_call_statuses"] == {"included": 1, "unresolved": 2, "ambiguous": 1, "omitted": 1}
    assert result["omitted_source_blocks"] == 1


def test_repeated_correct_but_rejected_edits_remain_graded_workflow_failure(tmp_path):
    fixture = {"path": "a.py", "mutant": "value = 0", "old": "value = 1"}
    proposal = {"action": "early_action", "input": {"action": "edit", "path": "a.py",
        "old": "# preserve\nvalue = 0", "new": "# preserve\nvalue = 1"}}
    data = row(status="turn_limit", final_patch_pass=False, first_patch_pass=False,
               phases=[{"phase": 0, "actions": [{"action": "read"}] + [proposal] * 10}])
    result = report.failure_record(data, variant="full", checkpoint=tmp_path / "runs.jsonl", line=1, fixture=fixture)
    assert result["category"] == "staged_workflow_noncompliance"
    assert result["graded"] is True
    assert result["accepted_edits"] == 0
    assert result["phase_gate_rejections"] == 10
    assert result["proposed_patch_matches_fixture_reversal"] is True
    assert len(result["rejected_edit_proposals"]) == 1
    assert data["final_patch_pass"] is False


@pytest.mark.parametrize("status,actions,phase,category", [
    ("turn_limit", [{"action": "search"}] * 12, 0, "investigation_limit_before_edit"),
    ("complete", [{"action": "edit"}], 2, "edited_patch_failed_acceptance"),
    ("turn_limit", [{"action": "edit", "error": "not found"}], 2, "patch_not_applied"),
    ("model_error", [{"action": "search"}], 0, "runner_error"),
])
def test_failure_categories_distinguish_runner_navigation_and_patch_outcomes(tmp_path, status, actions, phase, category):
    data = row(status=status, final_patch_pass=False, phases=[{"phase": phase, "actions": actions}])
    result = report.failure_record(data, variant="full", checkpoint=tmp_path / "runs.jsonl", line=1)
    assert result["category"] == category
    assert result["graded"] == (status != "model_error")


def test_control_rejections_include_passing_runs_and_are_separate_from_errors():
    passing = row(phases=[
        {"phase": 0, "actions": [{"action": "early_action"}, {"action": "early_action"},
                                  {"action": "read", "error": "bad span"}]},
        {"phase": 1, "actions": [{"action": "early_action"}]},
        {"phase": 2, "actions": [{"action": "premature_answer"}, {"action": "edit"}]}])
    failing = row(repeat=2, final_patch_pass=False, phases=[
        {"phase": 0, "actions": [{"action": "early_action"}]}])
    clean = row(repeat=3)
    result = report.aggregate([passing, failing, clean])
    controls = result["control_rejections"]
    assert controls["phase_gate_rejections"] == 4
    assert controls["premature_answer_rejections"] == 1
    assert controls["total"] == 5
    assert controls["affected_trajectories"] == 2
    assert controls["passing_trajectories_affected"] == 1
    assert controls["by_phase"] == {
        "0": {"phase_gate_rejections": 3, "premature_answer_rejections": 0},
        "1": {"phase_gate_rejections": 1, "premature_answer_rejections": 0},
        "2": {"phase_gate_rejections": 0, "premature_answer_rejections": 1}}
    assert result["tool_errors"] == 1
    assert result["model_calls"] == 3
    assert result["usage"]["input_tokens"] == 30


def test_supporting_audit_links_survive_regeneration_and_only_link_existing_files(tmp_path):
    for name in ["CACHE_PREFIX_AUDIT.md", "BUNDLE_TRACE_AUDIT.md"]:
        (tmp_path / name).write_text("# Recorded audit\n")
    result = report.build_report(tmp_path)
    text = report.markdown(result)
    assert "[Cache-prefix audit](CACHE_PREFIX_AUDIT.md)" in text
    assert "[One-hop inspect trace audit](BUNDLE_TRACE_AUDIT.md)" in text
    assert "HYDRATION_PHASE_AUDIT.md" not in text
    assert text == report.markdown(report.build_report(tmp_path))
