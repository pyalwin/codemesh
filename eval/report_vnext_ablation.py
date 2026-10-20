#!/usr/bin/env python3
"""Aggregate live vNext ablations without invoking models or changing fixtures.

Reads append-only checkpoints, so this can also produce an explicitly partial
progress report while variant runners are active. Comparisons use only matched
graded task/repeat pairs; incomplete and failed attempts remain separately visible.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

VARIANTS = {
    "full": ("full", False, False),
    "working": ("working-set", False, False),
    "hydrate": ("working-set", True, False),
    "bundle": ("working-set", False, True),
    "combined": ("working-set", True, True),
}
ARMS = ("filesystem", "codemesh")
GRADED = {"complete", "turn_limit", "context_limit"}
USAGE = ("input_tokens", "uncached_input_tokens", "cache_read_input_tokens",
         "cache_creation_input_tokens", "output_tokens", "elapsed_ms", "cost_usd")
CHANGING = {"context_policy", "hydrate_on_resume", "bundle_reads"}


def metric_value(item: dict, key: str) -> float | int | None:
    # The adapter uses zero placeholders on calls without provider usage. Those
    # are unknown token counts, while measured wall time can still be known.
    if key.endswith("tokens") and item.get("usage_available") is False:
        return None
    value = item.get(key)
    return value if type(value) in (int, float) else None


def total(items: list[dict], key: str) -> float | int | None:
    values = [metric_value(item, key) for item in items]
    return sum(values) if values and all(v is not None for v in values) else None


def usage(items: list[dict]) -> dict:
    return {key: total(items, key) for key in USAGE}


def change(before: float | None, after: float | None) -> float | None:
    return 100 * (after / before - 1) if before and after is not None else None


def journal(path: Path) -> tuple[list[dict], bool]:
    if not path.exists():
        return [], False
    data = path.read_text()
    rows = []
    lines = data.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            if index == len(lines) - 1 and not data.endswith("\n"):
                return rows, True  # A runner may currently be appending this line.
            raise ValueError(f"Invalid completed JSON line in {path}:{index + 1}")
    return rows, False


def model_calls(row: dict) -> list[dict]:
    return [call for call in row.get("calls", []) if call.get("kind") != "memory_error"]


def stage(call: dict) -> str:
    if call.get("kind") in ("handoff", "handoff_retry"):
        return "handoff"
    return {0: "investigation", 1: "impact", 2: "editing"}.get(call.get("phase"), "other")


def correct_edit_boundary(row: dict) -> int | None:
    """Locate the deciding call from the harness's first-correct-edit counter.

    Automatic hydration counts as a tool action; answer and rejected premature
    actions do not. Each nonautomatic phase action corresponds to one turn.
    This uses hidden grading metadata for reporting only, never model input.
    """
    milestone = row.get("first_correct_edit")
    if not milestone:
        return None
    target = milestone["tool_calls_before_edit"]
    count = 0
    for phase in row.get("phases", []):
        turn = 0
        for action in phase.get("actions", []):
            kind = action.get("action")
            turn += kind != "hydrate"
            if kind in ("answer", "premature_answer", "early_action"):
                continue
            if count == target:
                if kind != "edit" or action.get("error"):
                    return None
                for index, call in enumerate(model_calls(row)):
                    if (call.get("kind") == "decision" and call.get("phase") == phase["phase"]
                            and call.get("turn") == turn):
                        return index
                return None
            count += 1
    return None


def aggregate(rows: list[dict]) -> dict:
    calls = [call for row in rows for call in model_calls(row)]
    actions = [a for r in rows for p in r.get("phases", []) for a in p.get("actions", [])]
    counts = Counter(a.get("action") for a in actions)
    controls = {"phase_gate_rejections": "early_action", "premature_answer_rejections": "premature_answer"}
    control_counts = {metric: counts.get(action, 0) for metric, action in controls.items()}
    control_counts["total"] = sum(control_counts.values())
    control_counts["affected_trajectories"] = sum(
        any(a.get("action") in controls.values() for p in r.get("phases", []) for a in p.get("actions", []))
        for r in rows)
    control_counts["passing_trajectories_affected"] = sum(
        bool(r.get("final_patch_pass")) and any(a.get("action") in controls.values()
            for p in r.get("phases", []) for a in p.get("actions", [])) for r in rows)
    control_counts["by_phase"] = {
        str(phase): {metric: sum(a.get("action") == action for r in rows for p in r.get("phases", [])
                                if p.get("phase") == phase for a in p.get("actions", []))
                    for metric, action in controls.items()}
        for phase in (0, 1, 2)}
    before, after, bounded = [], [], 0
    for row in rows:
        boundary = correct_edit_boundary(row)
        if boundary is not None:
            bounded += 1
            before.extend(model_calls(row)[:boundary + 1])
            after.extend(model_calls(row)[boundary + 1:])
    references = Counter()
    call_statuses, call_resolutions = Counter(), Counter()
    unresolved = ambiguous = unverified = omitted = 0
    for action in actions:
        try:
            observation = json.loads(action.get("observation", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(observation, dict):
            continue
        for item in observation.get("references", []):
            if isinstance(item, dict):
                references[item.get("status", "unknown")] += 1
        if action.get("action") == "inspect":
            for call in observation.get("calls", []):
                if not isinstance(call, dict):
                    continue
                status, resolution = call.get("status"), call.get("resolution")
                call_statuses[status or "unknown"] += 1
                call_resolutions[resolution or "unknown"] += 1
                unresolved += status == "unresolved" or resolution == "unresolved"
                ambiguous += status == "ambiguous" or resolution == "ambiguous"
                unverified += resolution == "unverified_graph"
        value = observation.get("omitted", {})
        omitted += value.get("sources", 0) if isinstance(value, dict) else 0
    return {
        "trajectories": len(rows),
        "first_patch_pass": sum(bool(r.get("first_patch_pass")) for r in rows),
        "final_patch_pass": sum(bool(r.get("final_patch_pass")) for r in rows),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "usage": usage(calls),
        "model_calls": len(calls),
        "tool_calls": sum(r.get("tool_calls", 0) for r in rows),
        "tool_elapsed_ms": sum(r.get("tool_elapsed_ms", 0) for r in rows),
        "tool_errors": sum(bool(a.get("error")) for a in actions),
        "control_rejections": control_counts,
        "source_observation_bytes": sum(r.get("source_observation_bytes", 0) for r in rows),
        "total_observation_bytes": sum(r.get("total_observation_bytes", 0) for r in rows),
        "action_counts": dict(sorted((k, v) for k, v in counts.items() if k)),
        "reference_statuses": dict(references), "omitted_source_blocks": omitted,
        "unresolved_inspect_callsites": unresolved,
        "ambiguous_inspect_callsites": ambiguous,
        "unverified_graph_inspect_callsites": unverified,
        "inspect_call_statuses": dict(call_statuses),
        "inspect_call_resolutions": dict(call_resolutions),
        "phase_usage": {name: {"model_calls": len(selected := [c for c in calls if stage(c) == name]),
                                **usage(selected)}
                        for name in ("investigation", "impact", "handoff", "editing", "other")},
        "first_correct_edit": {"resolved_trajectories": bounded,
            "expected_trajectories": sum(r.get("first_correct_edit") is not None for r in rows),
            "through_edit": {"model_calls": len(before), **usage(before)},
            "after_edit": {"model_calls": len(after), **usage(after)}},
    }


def keyed(rows: list[dict], arm: str) -> dict[tuple[str, int], dict]:
    result = {}
    for row in rows:
        if row.get("mode") != arm or row.get("status") not in GRADED:
            continue
        key = row["task"], row["repeat"]
        if key in result:
            raise ValueError(f"Duplicate graded trajectory for {arm} {key}")
        result[key] = row
    return result


def compare(before: dict, after: dict) -> dict:
    keys = sorted(before.keys() & after.keys())
    base, treatment = aggregate([before[k] for k in keys]), aggregate([after[k] for k in keys])
    successes = [k for k in keys if before[k].get("final_patch_pass") and after[k].get("final_patch_pass")]
    return {
        "matched_pairs": len(keys), "keys": [list(k) for k in keys],
        "unmatched_baseline": [list(k) for k in sorted(before.keys() - after.keys())],
        "unmatched_treatment": [list(k) for k in sorted(after.keys() - before.keys())],
        "baseline": base, "treatment": treatment,
        "quality_change_percentage_points": (100 * (treatment["final_patch_pass"] - base["final_patch_pass"]) / len(keys)
                                              if keys else None),
        "usage_change_percent": {key: change(base["usage"][key], treatment["usage"][key]) for key in USAGE},
        "model_calls_change_percent": change(base["model_calls"], treatment["model_calls"]),
        "outcomes": dict(Counter(f"{int(bool(before[k].get('final_patch_pass')))}:{int(bool(after[k].get('final_patch_pass')))}"
                                 for k in keys)),
        "both_successful": {"pairs": len(successes),
            "baseline": aggregate([before[k] for k in successes]),
            "treatment": aggregate([after[k] for k in successes])},
    }


def failure_record(row: dict, *, variant: str, checkpoint: Path, line: int,
                   fixture: dict | None = None) -> dict | None:
    """Describe observed failure modes without overriding hidden acceptance."""
    if row.get("status") in GRADED and row.get("final_patch_pass"):
        return None
    actions = [a for phase in row.get("phases", []) for a in phase.get("actions", [])]
    accepted = [a for a in actions if a.get("action") == "edit" and not a.get("error")]
    early = [a for a in actions if a.get("action") == "early_action"]
    last_phase = max((p["phase"] for p in row.get("phases", [])), default=None)
    if row.get("status") not in GRADED:
        category = "runner_error"
    elif row.get("status") == "context_limit":
        category = "context_limit"
    elif not accepted and len(early) >= 2 and last_phase is not None and last_phase < 2:
        category = "staged_workflow_noncompliance"
    elif not accepted and last_phase is not None and last_phase < 2:
        category = "investigation_limit_before_edit"
    elif accepted:
        category = "edited_patch_failed_acceptance"
    else:
        category = "patch_not_applied"
    proposals = []
    for action in early:
        decision = action.get("input") or {}
        if decision.get("action") != "edit":
            continue
        proposal = {key: decision.get(key) for key in ("path", "old", "new")}
        if proposal not in proposals:
            proposals.append(proposal)
    matches = None
    if fixture is not None:
        matches = any(p["path"] == fixture["path"] and isinstance(p["old"], str)
                      and isinstance(p["new"], str) and p["old"].count(fixture["mutant"]) == 1
                      and p["old"].replace(fixture["mutant"], fixture["old"], 1) == p["new"]
                      for p in proposals)
    return {"variant": variant, "arm": row["mode"], "task": row["task"], "repeat": row["repeat"],
        "attempt_id": row.get("attempt_id"), "status": row["status"], "graded": row["status"] in GRADED,
        "category": category, "last_phase": last_phase, "accepted_edits": len(accepted),
        "phase_gate_rejections": len(early), "rejected_edit_proposals": proposals,
        "proposed_patch_matches_fixture_reversal": matches,
        "action_counts": dict(Counter(a.get("action") for a in actions)),
        "tool_errors": sum(bool(a.get("error")) for a in actions),
        "usage": usage(model_calls(row)), "checkpoint": str(checkpoint.resolve()), "checkpoint_line": line}


def build_report(root: Path) -> dict:
    loaded, invariant, config_source = {}, None, None
    ignored_tail = []
    matrix_path = root / "matrix_config.json"
    matrix = json.loads(matrix_path.read_text()) if matrix_path.exists() else {}
    for name, flags in VARIANTS.items():
        directory = root / name
        manifest = directory / "run_config.json"
        config = json.loads(manifest.read_text()) if manifest.exists() else None
        rows, tail = journal(directory / "runs.jsonl")
        calls, call_tail = journal(directory / "calls.jsonl")
        if tail or call_tail:
            ignored_tail.append(name)
        if rows and config is None:
            raise ValueError(f"{name}: checkpoint exists without run_config.json")
        if config is not None:
            actual = tuple(config.get(key) for key in ("context_policy", "hydrate_on_resume", "bundle_reads"))
            if actual != flags:
                raise ValueError(f"{name}: variant flags {actual!r} differ from expected {flags!r}")
            common = {k: v for k, v in config.items() if k not in CHANGING}
            if invariant is not None and common != invariant:
                differences = [key for key in common.keys() | invariant.keys() if common.get(key) != invariant.get(key)]
                raise ValueError(f"Incompatible run configurations: {config_source} vs {name}: {differences}")
            planned_fields = {**matrix, "task_ids": matrix.get("tasks"),
                              "model_catalog_sha256": matrix.get("catalog_fingerprint")}
            differences = [key for key, value in common.items()
                           if planned_fields.get(key) is not None and value != planned_fields[key]]
            if differences:
                raise ValueError(f"{name}: run configuration differs from planned matrix: {differences}")
            invariant, config_source = common, name
        loaded[name] = {"config": config, "rows": rows, "calls": calls,
                        "calls_journal_exists": (directory / "calls.jsonl").exists()}
    tasks = (invariant or {}).get("task_ids", matrix.get("tasks", []))
    repeats = (invariant or {}).get("repeats", matrix.get("repeats", 0))
    expected_per_arm = len(tasks) * repeats
    variants, indices = {}, {}
    fixture_path = Path(__file__).with_name("edit_workflow_tasks.json")
    fixtures = ({task["id"]: task for task in json.loads(fixture_path.read_text())["tasks"]}
                if fixture_path.exists() else {})
    failures = []
    for name, data in loaded.items():
        rows, calls = data["rows"], data["calls"]
        for line, row in enumerate(rows, 1):
            failure = failure_record(row, variant=name, checkpoint=root / name / "runs.jsonl", line=line,
                                     fixture=fixtures.get(row["task"]))
            if failure is not None:
                failures.append(failure)
        indices[name] = {arm: keyed(rows, arm) for arm in ARMS}
        expected_keys = {(task, repeat) for task in tasks for repeat in range(1, repeats + 1)}
        for arm, mapping in indices[name].items():
            if mapping.keys() - expected_keys:
                raise ValueError(f"{name}/{arm}: unexpected task or repeat in checkpoint")
        variants[name] = {"configured": data["config"] is not None, "by_arm": {}, "per_task": {}}
        for arm in ARMS:
            graded = list(indices[name][arm].values())
            attempts = [r for r in rows if r.get("mode") == arm]
            attempt_calls = ([c for c in calls if c.get("mode") == arm]
                             if data["calls_journal_exists"] else [c for r in attempts for c in model_calls(r)])
            finished_ids = {r.get("attempt_id") for r in attempts}
            variants[name]["by_arm"][arm] = {
                **aggregate(graded), "expected_trajectories": expected_per_arm,
                "completed_attempts": len(attempts), "runner_errors": sum(r.get("status") not in GRADED for r in attempts),
                "all_attempt_usage": usage(attempt_calls), "all_attempt_model_calls": len(attempt_calls),
                "all_attempt_usage_known_subtotal": {key: sum(metric_value(c, key) or 0 for c in attempt_calls)
                                                     for key in USAGE},
                "all_attempt_unknown_usage_calls": {key: sum(metric_value(c, key) is None for c in attempt_calls)
                                                    for key in USAGE},
                "inflight_attempts": len({c.get("attempt_id") for c in attempt_calls if c.get("attempt_id") not in finished_ids}),
                "all_attempt_router_errors": sum(c.get("router_error_count", 0) for c in attempt_calls),
                "all_attempt_invalid_calls": sum(c.get("call_valid") is False or bool(c.get("error")) for c in attempt_calls),
            }
        variants[name]["per_task"] = {task: {arm: aggregate([row for key, row in indices[name][arm].items() if key[0] == task])
                                               for arm in ARMS} for task in tasks}
        variants[name]["codemesh_vs_filesystem"] = compare(indices[name]["filesystem"], indices[name]["codemesh"])
    comparisons = {}
    for base, target in (("full", "working"), ("working", "hydrate"), ("working", "bundle"),
                         ("working", "combined"), ("full", "combined")):
        comparisons[f"{target}_vs_{base}"] = {arm: compare(indices[base][arm], indices[target][arm]) for arm in ARMS}
    finished = sum(len(mapping) for arms in indices.values() for mapping in arms.values())
    expected = expected_per_arm * len(ARMS) * len(VARIANTS)
    supporting_audits = [{"title": title, "path": filename} for filename, title in (
        ("CACHE_PREFIX_AUDIT.md", "Cache-prefix audit"),
        ("HYDRATION_PHASE_AUDIT.md", "Hydration phase audit"),
        ("BUNDLE_TRACE_AUDIT.md", "One-hop inspect trace audit")) if (root / filename).is_file()]
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "root": str(root.resolve()),
        "complete": expected > 0 and finished == expected, "graded_trajectories": finished,
        "expected_trajectories": expected, "common_configuration": invariant, "planned_matrix": matrix or None,
        "ignored_incomplete_journal_tails": ignored_tail, "variants": variants, "comparisons": comparisons,
        "failure_taxonomy": {"counts": dict(Counter(f["category"] for f in failures)), "attempts": failures},
        "supporting_audits": supporting_audits,
        "interpretation": [
            "Negative percentage changes mean fewer tokens, calls, time or reported cost.",
            "Every percentage uses totals over matched graded task/repeat pairs; incomplete pairs are excluded.",
            "All-graded usage includes unsuccessful trajectories that may stop early. Compare quality alongside usage; successful-pair efficiency is a conditional diagnostic, not an unbiased replacement for all-graded results.",
            "Failed and inflight attempts remain in all-attempt usage; unknown usage is null, not zero.",
            "Known-usage subtotals are lower bounds when any calls have unknown usage.",
            "First-correct-edit reporting includes the model decision that produces the accepted edit.",
            "Codex billed USD is unavailable. Input-token changes are not monetary cost changes.",
            "The seed controls execution order, not model randomness. Three tasks do not establish general performance.",
            "The suite contains three distinct one-line repair bugs, each repeated three times, with two hidden tests per bug; it does not run the full Pydantic test suite.",
            "Cross-arm handoffs differ: CodeMesh restores structured pointers and narrative; filesystem restores narrative unless hydration is enabled. This is an end-to-end harness comparison.",
            "source_observation_bytes counts source-tool output envelopes and numbered reads, not raw code-text bytes.",
            "Failure categories describe observed workflow outcomes. A proposed edit matching the seeded reversal is a text-only check, not an applied patch or passed test, and never changes acceptance scoring.",
            "Control rejections count staged-workflow gates and premature answers, including those in eventually passing runs. They are separate from tool/router errors; no tokens or calls are subtracted as a counterfactual.",
            "Variants execute in blocks with up to two concurrent runners. Cached tokens are observed usage, not independent cold-cache samples or a causal cost estimate.",
            "Handoff calls are reported separately from fresh editing, even though the runner labels both phase 2.",
            "Inspect and batch_read are available capabilities; adoption counts show whether models selected them.",
        ]}


def number(value: Any) -> str:
    return "—" if value is None else f"{value:,}" if type(value) is int else f"{value:.2f}"


def percent(value: Any) -> str:
    return "—" if value is None else f"{value:+.1f}%"


def markdown(report: dict) -> str:
    lines = ["# vNext live ablation results", "",
        f"**{'Complete' if report['complete'] else 'Partial snapshot'}:** {report['graded_trajectories']}/{report['expected_trajectories']} graded trajectories.", "",
        "All deltas use matched task/repeat pairs. Negative values mean reductions. Billed USD is unavailable from Codex.", ""]
    if report.get("supporting_audits"):
        links = [f"[{audit['title']}]({audit['path']})" for audit in report["supporting_audits"]]
        lines += ["Supporting mechanism audits: " + " · ".join(links) + ". These separate observed behavior from causal performance claims.", ""]
    lines += ["## Quality and usage", "",
        "| Variant | Arm | Graded | First pass | Final pass | Input | Uncached | Cached | Output | Calls | Errors |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, variant in report["variants"].items():
        for arm, row in variant["by_arm"].items():
            n, u = row["trajectories"], row["usage"]
            lines.append(f"| {name} | {arm} | {n}/{row['expected_trajectories']} | {row['first_patch_pass']}/{n} | {row['final_patch_pass']}/{n} | {number(u['input_tokens'])} | {number(u['uncached_input_tokens'])} | {number(u['cache_read_input_tokens'])} | {number(u['output_tokens'])} | {row['model_calls']} | {row['runner_errors']} |")
    lines += ["", "## CodeMesh versus filesystem within each variant", "",
        "| Variant | Matched pairs | Quality change (pp) | Input change | Uncached change | Cached change | Output change | Model time change |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, variant in report["variants"].items():
        row = variant["codemesh_vs_filesystem"]
        u = row["usage_change_percent"]
        lines.append(f"| {name} | {row['matched_pairs']} | {number(row['quality_change_percentage_points'])} | {percent(u['input_tokens'])} | {percent(u['uncached_input_tokens'])} | {percent(u['cache_read_input_tokens'])} | {percent(u['output_tokens'])} | {percent(u['elapsed_ms'])} |")
    lines += ["", "## Incremental changes within each arm", "",
        "| Comparison | Arm | Matched pairs | Quality change (pp) | Input change | Uncached change | Calls change |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for comparison, arms in report["comparisons"].items():
        for arm, row in arms.items():
            lines.append(f"| {comparison} | {arm} | {row['matched_pairs']} | {number(row['quality_change_percentage_points'])} | {percent(row['usage_change_percent']['input_tokens'])} | {percent(row['usage_change_percent']['uncached_input_tokens'])} | {percent(row['model_calls_change_percent'])} |")
    lines += ["", "## Successful-pair efficiency check", "",
        "Failed attempts can use fewer tokens by ending early. These rows show all matched graded pairs alongside only pairs where both patches pass. The successful subset is conditional and does not replace the quality results above.", "",
        "| Comparison | All pairs | All baseline input | All treatment input | Both-pass pairs | Both-pass baseline input | Both-pass treatment input | Both-pass change |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    efficiency = [(f"{name}: CM vs FS", variant["codemesh_vs_filesystem"])
                  for name, variant in report["variants"].items()]
    efficiency += [(f"{comparison}: {arm}", row) for comparison, arms in report["comparisons"].items()
                   for arm, row in arms.items()]
    for name, row in efficiency:
        passed = row["both_successful"]
        before, after = passed["baseline"]["usage"]["input_tokens"], passed["treatment"]["usage"]["input_tokens"]
        lines.append(f"| {name} | {row['matched_pairs']} | {number(row['baseline']['usage']['input_tokens'])} | {number(row['treatment']['usage']['input_tokens'])} | {passed['pairs']} | {number(before)} | {number(after)} | {percent(change(before, after))} |")
    lines += ["", "## Source bundle adoption and phase usage", "",
        "| Variant | Arm | Inspect | Batch read | Auto hydrate | Grep | Read | Investigation input | Impact input | Handoff input | Editing input |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, variant in report["variants"].items():
        for arm, row in variant["by_arm"].items():
            counts, phases = row["action_counts"], row["phase_usage"]
            values = [str(counts.get(action, 0)) for action in ("inspect", "batch_read", "hydrate", "grep", "read")]
            values += [number(phases[phase]["input_tokens"]) for phase in ("investigation", "impact", "handoff", "editing")]
            lines.append(f"| {name} | {arm} | " + " | ".join(values) + " |")
    lines += ["", "## Staged-workflow control rejections", "",
        "These counts include passing runs. They describe model requests rejected by the harness's phase or evidence rules, separately from tool errors and router errors. All associated model usage remains counted.", "",
        "| Variant | Arm | Graded | Affected runs | Passing runs affected | Phase gate | Premature answer | Phase 0 controls | Phase 1 controls | Phase 2 controls |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, variant in report["variants"].items():
        for arm, row in variant["by_arm"].items():
            control = row["control_rejections"]
            phases = [str(sum(control["by_phase"][str(phase)].values())) for phase in (0, 1, 2)]
            lines.append(f"| {name} | {arm} | {row['trajectories']} | {control['affected_trajectories']} | {control['passing_trajectories_affected']} | {control['phase_gate_rejections']} | {control['premature_answer_rejections']} | " + " | ".join(phases) + " |")
    lines += ["", "## Per-task quality and input", "",
        "| Variant | Task | Arm | Final pass | Input | Calls |",
        "| --- | --- | --- | ---: | ---: | ---: |"]
    for name, variant in report["variants"].items():
        for task, arms in variant["per_task"].items():
            for arm, row in arms.items():
                lines.append(f"| {name} | {task} | {arm} | {row['final_patch_pass']}/{row['trajectories']} | {number(row['usage']['input_tokens'])} | {row['model_calls']} |")
    lines += ["", "## Failure taxonomy", "",
        "A correct proposed repair can still fail the staged workflow if it is never applied. These failures remain in quality and usage totals; runner errors remain ungraded.", "",
        "| Variant | Arm | Task / repeat | Graded | Category | Last phase | Applied edits | Phase gate rejections | Proposed fixture reversal | Evidence |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |"]
    for failure in report["failure_taxonomy"]["attempts"]:
        match = failure["proposed_patch_matches_fixture_reversal"]
        match_label = "yes (unapplied)" if match else "no" if match is False else "—"
        lines.append(f"| {failure['variant']} | {failure['arm']} | {failure['task']} / {failure['repeat']} | {'yes' if failure['graded'] else 'no'} | {failure['category']} | {number(failure['last_phase'])} | {failure['accepted_edits']} | {failure['phase_gate_rejections']} | {match_label} | [checkpoint]({failure['checkpoint']}:{failure['checkpoint_line']}) |")
    lines += ["", "## Interpretation", ""] + [f"- {note}" for note in report["interpretation"]]
    lines += ["", "The JSON report includes exact matched keys, unmatched keys, successful-pair aggregates, through/after-correct-edit usage, all-attempt usage and unknown counters, reference statuses, and per-stage timing.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    report = build_report(args.root)
    args.root.mkdir(parents=True, exist_ok=True)
    output = args.out or args.root / "ablation-report.json"
    document = args.markdown or args.root / "ABLATION_REPORT.md"
    output.write_text(json.dumps(report, indent=2) + "\n")
    document.write_text(markdown(report))
    print(json.dumps({"complete": report["complete"], "graded": report["graded_trajectories"],
                      "expected": report["expected_trajectories"], "json": str(output), "markdown": str(document)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
