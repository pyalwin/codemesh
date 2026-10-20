#!/usr/bin/env python3
"""Report the controlled A/B/C/D matrix from local journals; never calls models.

The matrix.json plan is authoritative, including unstarted cells. Pilot and full
stages have independent denominators and are never pooled. Safe while runners
append: a partial final JSONL line is ignored and marked as incomplete.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from report_vnext_ablation import (
    GRADED, USAGE, aggregate as prior_aggregate, change, metric_value, model_calls,
)

ARMS = ("filesystem", "codemesh")
VARIANTS = {
    "A": {"tool_contract": "legacy", "search_tie_break": "pagerank", "hydrate_on_resume": False},
    "B": {"tool_contract": "lean", "search_tie_break": "pagerank", "hydrate_on_resume": False},
    "C": {"tool_contract": "lean", "search_tie_break": "path-depth", "hydrate_on_resume": False},
    "D": {"tool_contract": "lean", "search_tie_break": "path-depth", "hydrate_on_resume": True},
}
COUNTERS = ("source_observation_bytes", "tool_elapsed_ms", "tool_calls", "model_calls",
            "same_context_repeat_source_blocks", "cross_context_repeat_source_blocks",
            "evicted_context_repeat_source_blocks", "repeat_source_blocks")
INVARIANTS = ("commit", "build_fingerprint", "provider", "cli_version", "model", "effort",
              "model_catalog_sha256", "context_transport", "context_policy", "evidence_bytes",
              "ledger_entries", "source_mode", "handoff_references", "bundle_reads",
              "bundle_limits", "max_turns", "budget_usd")


def journal(path: Path) -> tuple[list[tuple[int, dict]], bool]:
    if not path.exists():
        return [], False
    data = path.read_text()
    lines, rows = data.splitlines(), []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            if number == len(lines) and not data.endswith("\n"):
                return rows, True
            raise ValueError(f"Invalid completed JSON line: {path}:{number}")
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object: {path}:{number}")
        rows.append((number, value))
    return rows, False


def usage(calls: list[dict]) -> dict:
    values = {key: [metric_value(c, key) for c in calls] for key in USAGE}
    return {
        **{key: sum(vs) if vs and all(v is not None for v in vs) else None
           for key, vs in values.items()},
        "known_subtotal": {key: sum(v for v in vs if v is not None) for key, vs in values.items()},
        "unknown_calls": {key: sum(v is None for v in vs) for key, vs in values.items()},
    }


def workflow(row: dict) -> bool:
    phases = row.get("phases", [])
    return (row.get("status") == "complete" and len(phases) == 3
            and {p.get("phase") for p in phases} == {0, 1, 2}
            and all(p.get("answer") is not None for p in phases)
            and bool(row.get("handoff", "").strip()))


def quality(row: dict) -> dict:
    return {"final_patch_pass": row.get("final_patch_pass") is True,
            "workflow_complete": workflow(row),
            "first_patch_pass": row.get("first_patch_pass") is True}


def aggregate(rows: list[dict]) -> dict:
    result = prior_aggregate(rows)
    calls = [c for row in rows for c in model_calls(row)]
    first = [model_calls(row)[0] for row in rows if model_calls(row)]
    subsequent = [c for row in rows for c in model_calls(row)[1:]]
    result["quality"] = {key: sum(quality(r)[key] for r in rows) for key in quality({})}
    result["usage"] = usage(calls)
    result["first_call_usage"] = {"model_calls": len(first), **usage(first)}
    result["subsequent_call_usage"] = {"model_calls": len(subsequent), **usage(subsequent)}
    for key in COUNTERS:
        if key not in result:
            result[key] = sum(r.get(key, 0) or 0 for r in rows)
    result["first_correct_edit"]["edit_counts"] = [r["first_correct_edit"]["edit_count"] for r in rows
        if isinstance(r.get("first_correct_edit"), dict) and "edit_count" in r["first_correct_edit"]]
    result["multi_file_trajectories"] = sum(len(r.get("mutation_paths", [])) > 1 for r in rows)
    result["protocol_invalid_trajectories"] = sum(r.get("call_protocol_valid") is False for r in rows)
    directions, hydration = Counter(), Counter()
    for row in rows:
        for phase in row.get("phases", []):
            for action in phase.get("actions", []):
                try:
                    observation = json.loads(action.get("observation", ""))
                except (ValueError, TypeError):
                    observation = {}
                if not isinstance(observation, dict):
                    observation = {}
                if action.get("action") == "inspect":
                    direction = (action.get("input") or {}).get("direction") or "outgoing"
                    directions[f"requested:{direction}"] += 1
                    actual = observation.get("limits", {}).get("direction")
                    if actual:
                        directions[f"response:{actual}"] += 1
                if action.get("action") == "hydrate":
                    hydration["calls"] += 1
                    hydration["errors"] += bool(action.get("error"))
                    for ref in observation.get("references", []):
                        if isinstance(ref, dict):
                            hydration[f"reference:{ref.get('status', 'unknown')}"] += 1
                    omitted = observation.get("omitted", {})
                    if isinstance(omitted, dict):
                        hydration["omitted_sources"] += omitted.get("sources", 0) or 0
    result["inspect_directions"] = dict(sorted(directions.items()))
    result["hydration"] = dict(sorted(hydration.items()))
    return result


def compare(name: str, before: dict, after: dict) -> dict:
    keys = sorted(before.keys() & after.keys())
    baseline = aggregate([before[k] for k in keys])
    treatment = aggregate([after[k] for k in keys])
    return {"name": name, "matched_pairs": len(keys),
        "baseline": baseline, "treatment": treatment,
        "unmatched_baseline": [list(k) for k in sorted(before.keys() - after.keys())],
        "unmatched_treatment": [list(k) for k in sorted(after.keys() - before.keys())],
        "quality_change_percentage_points": {key: 100 * (treatment["quality"][key] - baseline["quality"][key]) / len(keys)
            if keys else None for key in baseline["quality"]},
        "usage_change_percent": {key: change(baseline["usage"][key], treatment["usage"][key]) for key in USAGE},
        "counter_change_percent": {key: change(baseline[key], treatment[key]) for key in COUNTERS},
        "pairs": [{"key": list(key), "baseline_attempt_id": before[key].get("attempt_id"),
                   "treatment_attempt_id": after[key].get("attempt_id"),
                   "baseline_quality": quality(before[key]), "treatment_quality": quality(after[key]),
                   "baseline_usage": usage(model_calls(before[key])),
                   "treatment_usage": usage(model_calls(after[key]))} for key in keys]}


def inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Run path escapes matrix directory: {relative}")
    return path


def links(root: Path, entry: dict, row: dict, line: int | None = None) -> dict:
    relative = entry["path"]
    attempt = row.get("attempt_id", "unknown")
    directory = inside(root, relative)
    inputs = directory / "inputs" / attempt
    patch = directory / "patches" / f"{row['task']}-{row['mode']}-{row['repeat']}.diff"
    return {"checkpoint": f"{relative}/runs.jsonl" + (f":{line}" if line else ""),
            "calls": f"{relative}/calls.jsonl", "inputs_directory": f"{relative}/inputs/{attempt}",
            "exact_inputs": [str(p.relative_to(root)) for p in sorted(inputs.glob("call-*/*")) if p.is_file()],
            "observations": f"{relative}/observations/{attempt}",
            "patch": str(patch.relative_to(root)) if patch.exists() and row.get("status") in GRADED else None}


def validate_plan(matrix: dict) -> None:
    if matrix.get("schema_version") != 1:
        raise ValueError("Unsupported matrix schema_version")
    if tuple(matrix.get("arms", ARMS)) != ARMS:
        raise ValueError("Matrix must plan filesystem and codemesh arms")
    if not matrix.get("runs"):
        raise ValueError("Matrix must contain planned runs")
    ids, paths, seeds = set(), set(), {}
    for entry in matrix["runs"]:
        if entry["id"] in ids or entry["path"] in paths:
            raise ValueError("Duplicate planned run ID or path")
        ids.add(entry["id"])
        paths.add(entry["path"])
        if entry["variant"] not in VARIANTS or entry["stage"] not in ("pilot", "full"):
            raise ValueError("Unknown planned variant or stage")
        if not entry["task_ids"] or len(set(entry["task_ids"])) != len(entry["task_ids"]):
            raise ValueError("Empty or duplicate task_ids")
        if type(entry["repeats"]) is not int or entry["repeats"] < 1:
            raise ValueError("repeats must be positive")
        key = (entry["stage"], entry["suite"], entry["block"])
        if key in seeds and seeds[key] != entry["seed"]:
            raise ValueError("Matched variants must share the same block seed")
        seeds[key] = entry["seed"]


def build_report(root: Path) -> dict:
    root = root.resolve()
    matrix = json.loads((root / "matrix.json").read_text())
    validate_plan(matrix)
    loaded, configs, tails, invariant, suite_protocols = [], {}, [], None, {}
    for entry in matrix["runs"]:
        directory = inside(root, entry["path"])
        rows, tail = journal(directory / "runs.jsonl")
        call_rows, call_tail = journal(directory / "calls.jsonl")
        config_path = directory / "run_config.json"
        config = json.loads(config_path.read_text()) if config_path.exists() else None
        configs[entry["id"]] = config
        if tail or call_tail:
            tails.append(entry["id"])
        if (rows or call_rows) and config is None:
            raise ValueError(f"{entry['id']}: journals exist without run_config.json")
        if config is not None:
            expected = {**matrix.get("common_config", {}), **entry.get("expected_config", {}),
                        **VARIANTS[entry["variant"]], "task_ids": entry["task_ids"],
                        "modes": list(ARMS), "repeats": entry["repeats"], "seed": entry["seed"]}
            changed = [k for k, v in expected.items() if config.get(k) != v]
            if changed:
                raise ValueError(f"{entry['id']}: run configuration differs from plan: {changed}")
            actual_invariant = {k: config.get(k) for k in INVARIANTS}
            if invariant is not None and invariant != actual_invariant:
                raise ValueError(f"{entry['id']}: incompatible common run configurations")
            invariant = actual_invariant
            protocol = config.get("protocol_fingerprint")
            if entry["suite"] in suite_protocols and suite_protocols[entry["suite"]] != protocol:
                raise ValueError(f"{entry['id']}: suite protocol fingerprint changed")
            suite_protocols[entry["suite"]] = protocol
        expected = {(task, repeat, arm) for task in entry["task_ids"]
                    for repeat in range(1, entry["repeats"] + 1) for arm in ARMS}
        for _, row in rows + call_rows:
            if (row.get("task"), row.get("repeat"), row.get("mode")) not in expected:
                raise ValueError(f"{entry['id']}: unexpected task/repeat/arm")
        for _, row in rows:
            for key in ("provider", "model", "context_policy", "bundle_reads", *VARIANTS[entry["variant"]]):
                if key in row and row[key] != config.get(key):
                    raise ValueError(f"{entry['id']}: attempt field differs from run configuration: {key}")
        loaded.append((entry, rows, call_rows, (directory / "calls.jsonl").exists()))

    stages = {}
    for stage in ("pilot", "full"):
        entries = [item for item in loaded if item[0]["stage"] == stage]
        indices = {v: {a: {} for a in ARMS} for v in VARIANTS}
        expected = {v: {a: set() for a in ARMS} for v in VARIANTS}
        attempts, all_calls, inflight = [], {v: {a: [] for a in ARMS} for v in VARIANTS}, []
        for entry, rows, call_rows, has_journal in entries:
            variant = entry["variant"]
            keys = {(entry["suite"], entry["block"], task, repeat)
                    for task in entry["task_ids"] for repeat in range(1, entry["repeats"] + 1)}
            for arm in ARMS:
                if expected[variant][arm] & keys:
                    raise ValueError("Duplicate planned matched key")
                expected[variant][arm].update(keys)
            finished_ids = {row.get("attempt_id") for _, row in rows}
            for line, row in rows:
                arm = row["mode"]
                key = (entry["suite"], entry["block"], row["task"], row["repeat"])
                if row["status"] in GRADED:
                    if key in indices[variant][arm]:
                        raise ValueError(f"Duplicate graded trajectory: {entry['id']} {arm} {key}")
                    indices[variant][arm][key] = row
                attempts.append({"run": entry["id"], "variant": variant, "arm": arm,
                    "suite": entry["suite"], "block": entry["block"], "task": row["task"], "repeat": row["repeat"],
                    "attempt_id": row.get("attempt_id"), "status": row["status"],
                    "graded": row["status"] in GRADED, "quality": quality(row),
                    "usage": usage(model_calls(row)), "model_calls": len(model_calls(row)),
                    "errors": [c["error"] for c in model_calls(row) if c.get("error")],
                    "links": links(root, entry, row, line)})
            recorded = ([c for _, c in call_rows if c.get("kind") != "memory_error"] if has_journal
                        else [c | {"mode": row["mode"], "attempt_id": row.get("attempt_id")}
                              for _, row in rows for c in model_calls(row)])
            for arm in ARMS:
                all_calls[variant][arm].extend(c for c in recorded if c["mode"] == arm)
            running = sorted({c.get("attempt_id") for c in recorded} - finished_ids)
            for attempt_id in running:
                calls = [c for c in recorded if c.get("attempt_id") == attempt_id]
                first = calls[0]
                inflight.append({"run": entry["id"], "variant": variant, "arm": first["mode"],
                    "attempt_id": attempt_id, "model_calls": len(calls), "usage": usage(calls),
                    "links": links(root, entry, first)})
        cells = {}
        for variant in VARIANTS:
            cells[variant] = {}
            for arm in ARMS:
                mapping, recorded = indices[variant][arm], all_calls[variant][arm]
                selected_attempts = [a for a in attempts if a["variant"] == variant and a["arm"] == arm]
                cells[variant][arm] = {
                    **aggregate(list(mapping.values())), "expected_trajectories": len(expected[variant][arm]),
                    "missing_keys": [list(k) for k in sorted(expected[variant][arm] - mapping.keys())],
                    "completed_attempts": len(selected_attempts),
                    "runner_error_attempts": sum(not a["graded"] for a in selected_attempts),
                    "all_attempts": {"usage": usage(recorded), "model_calls": len(recorded),
                        "invalid_calls": sum(c.get("call_valid") is False or bool(c.get("error")) for c in recorded),
                        "router_errors": sum(c.get("router_error_count", 0) or 0 for c in recorded)},
                    "per_task": {task: aggregate([r for k, r in mapping.items() if k[2] == task])
                                 for task in sorted({k[2] for k in expected[variant][arm]})},
                    "per_block": {str(block): aggregate([r for k, r in mapping.items() if k[1] == block])
                                  for block in sorted({k[1] for k in expected[variant][arm]})},
                }
        comparisons = [compare(f"{v}: CodeMesh vs filesystem", indices[v]["filesystem"], indices[v]["codemesh"])
                       for v in VARIANTS]
        comparisons += [compare(f"{after} vs {before}: {arm}", indices[before][arm], indices[after][arm])
                        for before, after in (("A", "B"), ("B", "C"), ("C", "D")) for arm in ARMS]
        graded = sum(len(mapping) for arms in indices.values() for mapping in arms.values())
        planned = sum(len(keys) for arms in expected.values() for keys in arms.values())
        stage_tails = [e["id"] for e, *_ in entries if e["id"] in tails]
        stages[stage] = {"complete": bool(planned) and graded == planned and not inflight and not stage_tails,
            "graded_trajectories": graded, "expected_trajectories": planned, "cells": cells,
            "comparisons": comparisons, "attempts": attempts, "inflight_attempts": inflight,
            "ignored_incomplete_journal_tails": stage_tails}
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "matrix": matrix, "configurations": configs, "stages": stages,
            "complete": all(s["complete"] for s in stages.values() if s["expected_trajectories"]),
            "definitions": {
                "primary_quality": "Final hidden acceptance and completed three-phase workflow, reported separately.",
                "first_patch_pass": "Acceptance after the first individual edit; generally false for a required two-file repair.",
                "matched_key": "suite, block, task, local repeat; pilot and full never pooled.",
                "first_call": "First recorded model call per trajectory; not a guaranteed cold-cache call.",
                "usage": "Provider input includes cached subset. Null means unavailable; known subtotals retain incomplete spend.",
                "time": "Summed model-call and tool durations; excludes fixture preparation and is not matrix wall time.",
                "cost": "Only provider-reported USD; no dollar saving inferred from tokens or cache percentages.",
                "traces": "Captured CLI inputs, not final provider wire requests; failed retries may have overwritten patch filenames.",
            }}


def number(value: Any) -> str:
    return "unknown" if value is None else f"{value:,}" if type(value) is int else f"{value:.1f}"


def delta(value: float | None) -> str:
    return "unknown" if value is None else f"{value:+.1f}%"


def markdown(report: dict) -> str:
    lines = ["# Controlled next iteration: live evaluation", "",
             "Pilot and full results are separate. Final hidden acceptance and workflow completion are primary. "
             "First-edit acceptance is not comparable between one-file and two-file fixtures.", ""]
    for stage, result in report["stages"].items():
        label = "Complete" if result["complete"] else "INCOMPLETE — provisional snapshot"
        lines += [f"## {stage.title()}: {label}", "",
            f"{result['graded_trajectories']}/{result['expected_trajectories']} graded trajectories. "
            f"{len(result['inflight_attempts'])} unfinished attempts. Turn/context-limit outcomes remain graded.", "",
            "| Cell | Arm | Final / workflow | Calls | Input | Uncached | Cached | Output | Model s | Source bytes |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for variant, arms in result["cells"].items():
            for arm, cell in arms.items():
                u, q, n = cell["usage"], cell["quality"], cell["trajectories"]
                seconds = u["elapsed_ms"] / 1000 if u["elapsed_ms"] is not None else None
                lines.append(f"| {variant} | {arm} | {q['final_patch_pass']}/{n}; {q['workflow_complete']}/{n} | "
                    f"{cell['model_calls']} | {number(u['input_tokens'])} | {number(u['uncached_input_tokens'])} | "
                    f"{number(u['cache_read_input_tokens'])} | {number(u['output_tokens'])} | {number(seconds)} | "
                    f"{cell['source_observation_bytes']:,} |")
        lines += ["", "### Matched comparisons", "",
            "Negative change means less usage. Quality differences are percentage points. "
            "Only matched graded task/block/repeat pairs enter each comparison; failures stay in the denominator.", "",
            "| Comparison | Pairs | Input | Uncached | Output | Model time | Final / workflow pp |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for item in result["comparisons"]:
            u, q = item["usage_change_percent"], item["quality_change_percentage_points"]
            quality_label = " / ".join(number(q[k]) for k in ("final_patch_pass", "workflow_complete"))
            lines.append(f"| {item['name']} | {item['matched_pairs']} | {delta(u['input_tokens'])} | "
                f"{delta(u['uncached_input_tokens'])} | {delta(u['output_tokens'])} | {delta(u['elapsed_ms'])} | {quality_label} |")
        lines += ["", "### All attempts, including failed retries", "",
            "Unknown usage remains unknown. summary.json retains known subtotals, missing-call counts, "
            "per-task/block metrics, phase and first/subsequent-call usage, hydration omissions and rereads.", "",
            "| Cell | Arm | Attempts | Errors | Calls | Invalid | All input | Known input subtotal | Unknown input calls | USD |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
        for variant, arms in result["cells"].items():
            for arm, cell in arms.items():
                a = cell["all_attempts"]
                u = a["usage"]
                lines.append(f"| {variant} | {arm} | {cell['completed_attempts']} | {cell['runner_error_attempts']} | "
                    f"{a['model_calls']} | {a['invalid_calls']} | {number(u['input_tokens'])} | "
                    f"{number(u['known_subtotal']['input_tokens'])} | {u['unknown_calls']['input_tokens']} | {number(u['cost_usd'])} |")
        lines += ["", "### Trace index", "",
                  "| Run | Arm / task / repeat | Status | Trace |", "| --- | --- | --- | --- |"]
        for attempt in result["attempts"]:
            link = attempt["links"]
            refs = f"[attempt]({link['checkpoint']}) · [calls]({link['calls']}) · [inputs]({link['inputs_directory']}/)"
            if link["patch"]:
                refs += f" · [patch]({link['patch']})"
            lines.append(f"| {attempt['run']} | {attempt['arm']} / {attempt['task']} / {attempt['repeat']} | {attempt['status']} | {refs} |")
        for attempt in result["inflight_attempts"]:
            link = attempt["links"]
            lines.append(f"| {attempt['run']} | {attempt['arm']} / {attempt['attempt_id']} | unfinished | "
                         f"[calls]({link['calls']}) · [inputs]({link['inputs_directory']}/) |")
        lines.append("")
    lines += ["## Interpretation limits", "",
        "- A is a fresh baseline; B−A changes contract, C−B changes tie-breaking, D−C adds hydration. "
        "This is an incremental design, not a full factorial. Ranking is a no-op for filesystem; its repeated runs measure variability.",
        "- First recorded calls are reported separately in summary.json but are not guaranteed cold. "
        "Shared caches, server load and unseeded model sampling can affect cache and latency results.",
        "- Bytes are not tokens. USD is unmeasured unless the provider reports it. Cached input is already included in total input.",
        "- Seeded Pydantic repairs do not establish general large-codebase performance. Repeats are not independent new tasks.",
        "- JSON actions are dispatched deterministically through the CLI; spontaneous MCP adoption is not measured.",
        "", "Machine-readable [summary](summary.json) · [matrix plan](matrix.json)", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.root)
    (args.root / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.root / "REPORT.md").write_text(markdown(report))
    print(json.dumps({name: {key: result[key] for key in ("complete", "graded_trajectories", "expected_trajectories")}
                      for name, result in report["stages"].items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
