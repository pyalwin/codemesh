#!/usr/bin/env python3
"""Run dependent code-investigation workflows with a simulated read tool.

Each trajectory has two dependent investigation phases, a model-generated
compaction memory, and a post-compaction scenario. Claude chooses read calls
and final answers; the harness supplies either numbered source or ID capsules.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import re
import subprocess
import time
from typing import Any

from capsule_v2 import format_v2
from experiment import apply_summaries, extract_unit, repo_commit


HERE = Path(__file__).resolve().parent
SYSTEM = (
    "You are a source-restricted code investigation agent. The unit catalog is "
    "navigation, not evidence. Request code reads before making factual claims. "
    "Use only observations in the conversation and your compaction memory. "
    "Follow the structured action schema exactly."
)
DECISION_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["read", "read_source", "answer"]},
        "unit": {"type": "string"},
        "answer": {"type": "string"},
    },
    "required": ["action", "unit", "answer"],
    "additionalProperties": False,
}, separators=(",", ":"))


def model_call(prompt: str, *, structured: bool, out: Path) -> dict[str, Any]:
    cmd = [
        "claude", "--print", "--model", "haiku", "--tools", "",
        "--no-session-persistence", "--output-format", "json",
        "--max-budget-usd", "0.05", "--setting-sources", "",
        "--effort", "low", "--system-prompt", SYSTEM,
    ]
    if structured:
        cmd += ["--json-schema", DECISION_SCHEMA]
    started = time.monotonic()
    try:
        process = subprocess.run(cmd, input=prompt, text=True, capture_output=True, cwd=out, timeout=180)
        payload = json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"error": str(exc), "elapsed_ms": int((time.monotonic() - started) * 1000)}
    usage = payload.get("usage") or {}
    result = {
        "answer": payload.get("result") or "",
        "decision": payload.get("structured_output") if structured else None,
        "error": payload.get("result") if payload.get("is_error") else None,
        "input_tokens": sum(usage.get(k, 0) or 0 for k in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"
        )),
        "uncached_input_tokens": usage.get("input_tokens", 0) or 0,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "cost_usd": payload.get("total_cost_usd", 0) or 0,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "model_ids": list((payload.get("modelUsage") or {}).keys()),
        "exit_code": process.returncode,
    }
    if structured and not isinstance(result["decision"], dict):
        result["error"] = result["error"] or "Missing structured_output"
    return result


def make_registry(units: list[dict[str, Any]]) -> dict[str, Any]:
    registry: dict[str, Any] = {"units": {}, "evidence": {}}
    counter = 0
    for index, unit in enumerate(units, 1):
        alias = f"U{index}"
        registry["units"][alias] = {
            "unit_id": unit["id"], "name": unit["name"], "path": unit["path"],
            "lines": unit["lines"], "source_sha256": unit["source_sha256"],
        }
        physical_lines = sorted({line for items in unit["evidence"].values() for item in items
                                 for line in (item["line"], item.get("guard_line")) if line is not None})
        for line in physical_lines:
            counter += 1
            registry["evidence"][f"E{counter}"] = {
                "unit": alias, "path": unit["path"], "line": line,
                "source_sha256": unit["source_sha256"],
            }
    return registry


def render_read(unit_alias: str, mode: str, question: str, units: dict[str, dict[str, Any]], registry: dict[str, Any], *, source: bool = False) -> str:
    info = registry["units"][unit_alias]
    unit = units[info["unit_id"]]
    if mode == "raw" or source:
        start = int(unit["lines"].split("-", 1)[0])
        lines = "\n".join(f"{start + offset}: {line}" for offset, line in enumerate(unit["source"].rstrip().splitlines()))
        return f"{unit_alias} {unit['id']}\n```python\n{lines}\n```"
    capsule = format_v2([unit], question)
    capsule = capsule.replace(f"### {unit['id']}\n", f"### {unit_alias} {unit['name']}\n", 1)
    capsule = re.sub(r"^Version: sha256:[a-f0-9]+\n", "", capsule, flags=re.MULTILINE)
    capsule = re.sub(r"^Source: [^\n]+\n", "", capsule, flags=re.MULTILINE)
    by_ref = {(item["path"], item["line"]): alias for alias, item in registry["evidence"].items()}
    pattern = re.compile(re.escape(unit["path"]) + r":(\d+)(?!\d)")
    def replace(match: re.Match[str]) -> str:
        key = unit["path"], int(match.group(1))
        if key not in by_ref:
            raise ValueError(f"Missing source evidence alias: {key}")
        return by_ref[key]
    capsule = pattern.sub(replace, capsule)
    if mode in ("ids_guard", "ids_guard_nosource") and unit["evidence"].get("raises"):
        clauses = []
        for item in unit["evidence"]["raises"]:
            guard = " and ".join(item["guards"])
            guard_alias = by_ref.get((unit["path"], item["guard_line"])) if item["guard_line"] else None
            raise_alias = by_ref[(unit["path"], item["line"])]
            prefix = f"{guard_alias} if {guard}: " if guard_alias else ""
            clauses.append(f"- {prefix}{raise_alias} raise {item['exception']}")
        capsule += "\nGuarded raise evidence:\n" + "\n".join(clauses)
    return capsule


def expand_answer(answer: str, registry: dict[str, Any]) -> str:
    def replace_evidence_range(match: re.Match[str]) -> str:
        first = registry["evidence"].get(match.group(1))
        last = registry["evidence"].get(match.group(2))
        if first and last and first["path"] == last["path"]:
            return f"{first['path']}:{first['line']}-{last['line']}"
        return match.group()
    answer = re.sub(r"\b(E\d+)-(E\d+)\b", replace_evidence_range, answer)

    answer = re.sub(r"\b(U\d+):(\d+)(?:-(\d+))?\b", lambda m: (
        f"{registry['units'][m.group(1)]['path']}:{m.group(2)}" + (f"-{m.group(3)}" if m.group(3) else "")
        if m.group(1) in registry["units"] else m.group()
    ), answer)

    def replace(match: re.Match[str]) -> str:
        alias = match.group()
        if alias in registry["evidence"]:
            item = registry["evidence"][alias]
            return f"{item['path']}:{item['line']}"
        if alias in registry["units"]:
            item = registry["units"][alias]
            return f"{item['path']}:{item['lines']}"
        return alias
    return re.sub(r"\b(?:E|U)\d+\b", replace, answer)


def prompt_for(workflow: dict[str, Any], phase: dict[str, Any], mode: str, history: list[str], reads: int) -> str:
    catalog = "\n".join(f"{alias} {unit_id.partition('::')[2]}" for alias, unit_id in (
        (f"U{i}", value) for i, value in enumerate(workflow["units"], 1)
    ))
    citation = "E evidence IDs" if mode != "raw" else "file:line references"
    actions = "read(U#) for a source unit; read_source(U#) for a full-source fallback; answer" if mode != "raw" else "read(U#) for a source unit; answer"
    requirement = (
        f"This phase requires at least {phase['min_reads']} read(s); only {reads} have happened. Choose read now."
        if reads < phase["min_reads"] else
        "Answer if evidence is sufficient; otherwise choose a read."
    )
    return (
        f"Pinned source catalog (names are navigation only):\n{catalog}\n\n"
        f"Available actions: {actions}. Choose one action per response. "
        f"For a read set unit to U# and answer to an empty string. For a final answer set unit to an empty string. "
        f"Answer in at most 140 words and cite {citation} for key claims. "
        "Never infer unobserved call arguments or branch behavior.\n\n"
        "Conversation so far:\n" + "\n\n".join(history) + "\n\n"
        f"Current phase: {phase['id']}. {requirement}"
    )


def compact_memory(history: list[str], mode: str, out: Path) -> dict[str, Any]:
    citation = "E and U IDs" if mode != "raw" else "file and line citations"
    prompt = (
        "Write a handoff memory for the next agent turn, at most 120 words. "
        "Keep only verified facts needed to answer the final scenario and their "
        f"{citation}. Preserve exact guards, delegate names, and call arguments. "
        "List any unresolved issue. Do not answer a new question.\n\n"
        "Conversation:\n" + "\n\n".join(history)
    )
    return {"prompt": prompt, **model_call(prompt, structured=False, out=out)}


def run_trajectory(workflow: dict[str, Any], mode: str, repeat: int, units: dict[str, dict[str, Any]], out: Path) -> dict[str, Any]:
    selected = [units[unit_id] for unit_id in workflow["units"]]
    registry = make_registry(selected)
    history: list[str] = []
    phases: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    memory = ""
    status = "complete"
    for phase_index, phase in enumerate(workflow["phases"]):
        if phase_index == 2:
            summary = compact_memory(history, mode, out)
            calls.append({"kind": "compaction", **summary})
            if summary.get("error"):
                status = "compaction_error"
                break
            memory = summary["answer"]
            history = [f"Compaction memory from preceding investigation:\n{memory}"]
        history.append(f"User: {phase['question']}")
        phase_record: dict[str, Any] = {"id": phase["id"], "question": phase["question"], "actions": [], "answer": None}
        read_aliases: set[str] = set()
        for turn in range(1, 7):
            prompt = prompt_for(workflow, phase, mode, history, len(read_aliases))
            result = model_call(prompt, structured=True, out=out)
            calls.append({"kind": "decision", "phase": phase["id"], "turn": turn, "prompt": prompt, "prompt_bytes": len(prompt.encode()), **result})
            if result.get("error"):
                status = "model_error"
                break
            decision = result["decision"]
            action, alias = decision.get("action"), decision.get("unit")
            if action == "answer" and len(read_aliases) >= phase["min_reads"]:
                answer = decision.get("answer") or ""
                phase_record["answer_with_ids"] = answer if mode != "raw" else None
                phase_record["answer"] = expand_answer(answer, registry)
                phase_record["actions"].append({"action": "answer"})
                history.append(f"Assistant: {answer}")
                break
            if action == "answer":
                history.append("Tool protocol: a source read is required before answering this phase.")
                phase_record["actions"].append({"action": "premature_answer"})
                continue
            if action not in ("read", "read_source") or alias not in registry["units"] or (action == "read_source" and mode == "raw"):
                history.append("Tool protocol: invalid action or unit. Choose an available read or answer.")
                phase_record["actions"].append({"action": "invalid", "unit": alias})
                continue
            observation = render_read(alias, mode, phase["question"], units, registry, source=(action == "read_source"))
            read_aliases.add(alias)
            phase_record["actions"].append({"action": action, "unit": alias, "observation": observation, "observation_bytes": len(observation.encode())})
            history.append(f"Assistant requested {action}({alias}).\nTool observation:\n{observation}")
        else:
            status = "turn_limit"
        phases.append(phase_record)
        if status != "complete" or phase_record["answer"] is None:
            break
    totals = {
        key: sum(call.get(key) or 0 for call in calls)
        for key in ("input_tokens", "uncached_input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens", "cost_usd", "elapsed_ms")
    }
    reads = [action["unit"] for phase in phases for action in phase["actions"] if action["action"] in ("read", "read_source")]
    return {
        "workflow": workflow["id"], "mode": mode, "repeat": repeat, "status": status,
        "phases": phases, "compaction_memory": memory,
        "calls": calls, "totals": totals, "tool_reads": len(reads),
        "repeat_reads": len(reads) - len(set(reads)),
        "source_fallbacks": sum(action["action"] == "read_source" for phase in phases for action in phase["actions"]),
        "registry": registry,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=HERE / "agent_workflows.json")
    parser.add_argument("--out", type=Path, default=HERE.parent / "results" / "agent-workflows")
    parser.add_argument("--summary-file", type=Path, default=HERE.parent / "results" / "capsules-pydantic" / "summaries.json")
    parser.add_argument("--modes", default="raw,ids")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workflow", action="append")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if repo_commit(args.repo) != config["commit"]:
        parser.error("Pinned repository commit does not match workflow config")
    modes = args.modes.split(",")
    if not modes or len(set(modes)) != len(modes) or any(mode not in ("raw", "ids", "ids_guard") for mode in modes):
        parser.error("--modes must select distinct values from raw,ids,ids_guard")
    workflows = [item for item in config["workflows"] if not args.workflow or item["id"] in args.workflow]
    if not workflows or args.repeats < 1:
        parser.error("No workflows or invalid repeat count")
    ids = dict.fromkeys(unit_id for workflow in workflows for unit_id in workflow["units"])
    units = {unit_id: extract_unit(args.repo, unit_id) for unit_id in ids}
    apply_summaries(units, args.summary_file)
    args.out.mkdir(parents=True, exist_ok=True)
    plan = [(workflow, mode, repeat) for workflow in workflows for mode in modes for repeat in range(1, args.repeats + 1)]
    random.Random(args.seed).shuffle(plan)
    results = []
    checkpoint = args.out / "runs.jsonl"
    checkpoint.write_text("")
    for index, (workflow, mode, repeat) in enumerate(plan, 1):
        print(f"[{index}/{len(plan)}] {workflow['id']} {mode} repeat {repeat}", flush=True)
        result = run_trajectory(workflow, mode, repeat, units, args.out)
        results.append(result)
        with checkpoint.open("a") as handle:
            handle.write(json.dumps(result) + "\n")
        print(f"  {result['status']}; {result['tool_reads']} reads; {result['totals']['input_tokens']} input; ${result['totals']['cost_usd']:.6f}", flush=True)
    by_mode: dict[str, dict[str, Any]] = {}
    for mode in modes:
        rows = [row for row in results if row["mode"] == mode]
        by_mode[mode] = {
            "trajectories": len(rows), "complete": sum(row["status"] == "complete" for row in rows),
            "tool_reads": sum(row["tool_reads"] for row in rows),
            "repeat_reads": sum(row["repeat_reads"] for row in rows),
            "source_fallbacks": sum(row["source_fallbacks"] for row in rows),
            **{key: sum(row["totals"][key] for row in rows) for key in (
                "input_tokens", "uncached_input_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "output_tokens", "cost_usd", "elapsed_ms"
            )},
        }
    report = {
        "repository": config["repository"], "commit": config["commit"],
        "model": "haiku", "effort": "low", "repeats": args.repeats, "seed": args.seed,
        "method": "Model-chosen reads and answers; two dependent phases, model-generated compaction, then a post-compaction scenario. Same catalog and questions in both modes.",
        "workflows": workflows, "by_mode": by_mode, "results": results,
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {args.out / 'report.json'}")
    return int(any(row["status"] != "complete" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
