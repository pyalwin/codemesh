import json

import pytest

import report_controlled_next as report


def call(tokens=10, **changes):
    return {"kind": "decision", "phase": 0, "turn": 1, "input_tokens": tokens,
            "uncached_input_tokens": tokens, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "output_tokens": 2, "elapsed_ms": 100,
            "cost_usd": None, "call_valid": True, "usage_available": True, **changes}


def row(arm="filesystem", repeat=1, tokens=10, **changes):
    return {"attempt_id": f"{arm}-{repeat}", "task": "task", "repeat": repeat,
            "mode": arm, "status": "complete", "calls": [call(tokens)],
            "first_patch_pass": True, "final_patch_pass": True, "handoff": "Observed cause",
            "phases": [{"phase": p, "answer": "done", "actions": []} for p in range(3)],
            **changes}


def plan(root, repeats=1, stages=("pilot", "full"), blocks=(1,)):
    runs = [{"id": f"{stage}-{block}-{variant}", "stage": stage, "variant": variant,
             "suite": "new" if stage == "pilot" else "combined", "block": block,
             "seed": 42 + block, "path": f"{stage}/{block}/{variant}", "task_ids": ["task"],
             "repeats": repeats}
            for stage in stages for block in blocks for variant in report.VARIANTS]
    matrix = {"schema_version": 1, "arms": list(report.ARMS),
              "common_config": {"provider": "codex", "model": "gpt-5.6-terra"}, "runs": runs}
    (root / "matrix.json").write_text(json.dumps(matrix))
    return matrix


def write_run(root, entry, rows=(), calls=None, **changes):
    directory = root / entry["path"]
    directory.mkdir(parents=True, exist_ok=True)
    config = {"provider": "codex", "model": "gpt-5.6-terra", "build_fingerprint": "same",
              "protocol_fingerprint": entry["suite"], "task_ids": entry["task_ids"],
              "modes": list(report.ARMS), "repeats": entry["repeats"], "seed": entry["seed"],
              **report.VARIANTS[entry["variant"]], **changes}
    (directory / "run_config.json").write_text(json.dumps(config))
    (directory / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if calls is not None:
        (directory / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
    return directory


def test_planned_counts_include_unstarted_runs_and_pilot_is_separate(tmp_path):
    matrix = plan(tmp_path)
    write_run(tmp_path, matrix["runs"][0], [row(tokens=10000)])
    write_run(tmp_path, matrix["runs"][4], [row(tokens=10)])
    result = report.build_report(tmp_path)
    pilot, full = result["stages"]["pilot"], result["stages"]["full"]
    assert (pilot["graded_trajectories"], pilot["expected_trajectories"]) == (1, 8)
    assert (full["graded_trajectories"], full["expected_trajectories"]) == (1, 8)
    assert full["cells"]["A"]["filesystem"]["usage"]["input_tokens"] == 10
    assert not result["complete"]
    assert "INCOMPLETE" in report.markdown(result)


def test_matching_uses_task_block_and_repeat_and_keeps_quality_failures(tmp_path):
    matrix = plan(tmp_path, repeats=2, stages=("full",), blocks=(1, 2))
    write_run(tmp_path, matrix["runs"][0], [row(tokens=100), row(repeat=2, tokens=10000)])
    write_run(tmp_path, matrix["runs"][1], [row(tokens=50, final_patch_pass=False)])
    write_run(tmp_path, matrix["runs"][4], [row(tokens=1000)])
    result = report.build_report(tmp_path)["stages"]["full"]
    comparison = next(c for c in result["comparisons"] if c["name"] == "B vs A: filesystem")
    assert comparison["matched_pairs"] == 1
    assert comparison["usage_change_percent"]["input_tokens"] == -50
    assert comparison["quality_change_percentage_points"]["final_patch_pass"] == -100
    assert comparison["quality_change_percentage_points"]["workflow_complete"] == 0
    assert comparison["unmatched_baseline"] == [["combined", 1, "task", 2], ["combined", 2, "task", 1]]
    assert result["cells"]["A"]["filesystem"]["per_block"]["2"]["usage"]["input_tokens"] == 1000


@pytest.mark.parametrize("change", [{"model": "other"}, {"seed": 3}, {"tool_contract": "lean"},
                                    {"search_tie_break": "path-depth"}, {"hydrate_on_resume": True}])
def test_wrong_run_configuration_rejected(tmp_path, change):
    matrix = plan(tmp_path)
    write_run(tmp_path, matrix["runs"][0], **change)
    with pytest.raises(ValueError, match="differs from plan"):
        report.build_report(tmp_path)


@pytest.mark.parametrize("field", ["build_fingerprint", "protocol_fingerprint", "max_turns"])
def test_unplanned_configuration_drift_rejected(tmp_path, field):
    matrix = plan(tmp_path)
    write_run(tmp_path, matrix["runs"][0])
    write_run(tmp_path, matrix["runs"][1], **{field: "changed"})
    with pytest.raises(ValueError, match="incompatible common|fingerprint changed"):
        report.build_report(tmp_path)


def test_expected_config_is_validated_even_when_only_one_run_exists(tmp_path):
    matrix = plan(tmp_path)
    matrix["runs"][0]["expected_config"] = {"protocol_fingerprint": "frozen"}
    (tmp_path / "matrix.json").write_text(json.dumps(matrix))
    write_run(tmp_path, matrix["runs"][0])
    with pytest.raises(ValueError, match="protocol_fingerprint"):
        report.build_report(tmp_path)


def test_failed_and_inflight_usage_is_retained_with_unknowns(tmp_path):
    matrix = plan(tmp_path)
    failed = row(status="model_error", attempt_id="failed", calls=[call(7, call_valid=False, error="failed")])
    calls = [call(10, mode="filesystem", task="task", repeat=1, attempt_id="filesystem-1"),
             call(7, mode="filesystem", task="task", repeat=1, attempt_id="failed", call_valid=False, error="failed"),
             call(0, mode="filesystem", task="task", repeat=1, attempt_id="running", usage_available=False)]
    write_run(tmp_path, matrix["runs"][0], [row(), failed], calls=calls)
    result = report.build_report(tmp_path)["stages"]["pilot"]
    cell = result["cells"]["A"]["filesystem"]
    assert cell["usage"]["input_tokens"] == 10
    assert cell["runner_error_attempts"] == 1
    assert cell["all_attempts"]["usage"]["input_tokens"] is None
    assert cell["all_attempts"]["usage"]["known_subtotal"]["input_tokens"] == 17
    assert cell["all_attempts"]["usage"]["unknown_calls"]["input_tokens"] == 1
    assert cell["all_attempts"]["usage"]["elapsed_ms"] == 300
    assert cell["all_attempts"]["usage"]["cost_usd"] is None
    assert result["inflight_attempts"][0]["attempt_id"] == "running"
    assert result["attempts"][1]["errors"] == ["failed"]
    assert "inputs/failed" in result["attempts"][1]["links"]["inputs_directory"]


def test_partial_final_line_ignored_but_completed_corruption_rejected(tmp_path):
    matrix = plan(tmp_path)
    directory = write_run(tmp_path, matrix["runs"][0], [row()])
    path = directory / "runs.jsonl"
    with path.open("a") as file:
        file.write('{"attempt_id":')
    assert report.build_report(tmp_path)["stages"]["pilot"]["ignored_incomplete_journal_tails"] == ["pilot-1-A"]
    with path.open("a") as file:
        file.write("\n")
    with pytest.raises(ValueError, match="Invalid completed JSON"):
        report.build_report(tmp_path)


def test_duplicate_graded_rows_and_unexpected_keys_rejected(tmp_path):
    matrix = plan(tmp_path)
    write_run(tmp_path, matrix["runs"][0], [row(), row()])
    with pytest.raises(ValueError, match="Duplicate graded"):
        report.build_report(tmp_path)
    write_run(tmp_path, matrix["runs"][0], [row(task="unexpected")])
    with pytest.raises(ValueError, match="unexpected task"):
        report.build_report(tmp_path)


def test_complete_matrix_workflow_separate_and_first_edit_caveat(tmp_path):
    matrix = plan(tmp_path)
    for entry in matrix["runs"]:
        write_run(tmp_path, entry, [row(arm, first_patch_pass=False,
            mutation_paths=["a.py", "b.py"], first_correct_edit={"edit_count": 2, "tool_calls_before_edit": 2})
            for arm in report.ARMS])
    result = report.build_report(tmp_path)
    assert result["complete"]
    cell = result["stages"]["full"]["cells"]["D"]["codemesh"]
    assert cell["quality"] == {"final_patch_pass": 1, "workflow_complete": 1, "first_patch_pass": 0}
    assert cell["first_correct_edit"]["edit_counts"] == [2]
    assert "first individual edit" in result["definitions"]["first_patch_pass"]
    limited = row(status="turn_limit")
    assert report.quality(limited)["final_patch_pass"]
    assert not report.quality(limited)["workflow_complete"]


def test_first_call_usage_not_labelled_cold_and_cost_zero_is_known():
    data = row(calls=[call(100, cache_read_input_tokens=50), call(200)])
    result = report.aggregate([data])
    assert result["first_call_usage"]["input_tokens"] == 100
    assert result["subsequent_call_usage"]["input_tokens"] == 200
    assert report.usage([call(cost_usd=0)])["cost_usd"] == 0
    assert report.usage([call(cost_usd=None)])["cost_usd"] is None


def test_hydration_omissions_and_graph_direction_are_observed():
    data = row(phases=[{"phase": 2, "actions": [
        {"action": "hydrate", "observation": json.dumps({"references": [{"status": "missing"}], "omitted": {"sources": 2}})},
        {"action": "inspect", "input": {"direction": "both"},
         "observation": json.dumps({"limits": {"direction": "both"}, "calls": [{"status": "unresolved"}]})}]}])
    result = report.aggregate([data])
    assert result["hydration"] == {"calls": 1, "errors": 0, "omitted_sources": 2, "reference:missing": 1}
    assert result["inspect_directions"] == {"requested:both": 1, "response:both": 1}
    assert result["unresolved_inspect_callsites"] == 1


def test_missing_config_or_path_escape_rejected(tmp_path):
    matrix = plan(tmp_path)
    directory = write_run(tmp_path, matrix["runs"][0], [row()])
    (directory / "run_config.json").unlink()
    with pytest.raises(ValueError, match="without run_config"):
        report.build_report(tmp_path)
    matrix["runs"][0]["path"] = "../../elsewhere"
    (tmp_path / "matrix.json").write_text(json.dumps(matrix))
    with pytest.raises(ValueError, match="escapes"):
        report.build_report(tmp_path)


def test_matched_seeds_and_attempt_flags_are_checked(tmp_path):
    matrix = plan(tmp_path)
    write_run(tmp_path, matrix["runs"][0], [row(tool_contract="lean")])
    with pytest.raises(ValueError, match="attempt field differs"):
        report.build_report(tmp_path)
    matrix["runs"][1]["seed"] += 1
    (tmp_path / "matrix.json").write_text(json.dumps(matrix))
    with pytest.raises(ValueError, match="same block seed"):
        report.build_report(tmp_path)
