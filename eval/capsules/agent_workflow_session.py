#!/usr/bin/env python3
"""Dependent code-task benchmark using real resumed Claude conversations.

Unlike agent_workflow.py's stateless transcript replay, this harness resumes
the same model session across tool observations and task phases. It starts a
fresh session after a model-written handoff memory to simulate compaction.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import subprocess
import time
import uuid
from typing import Any

from agent_workflow import DECISION_SCHEMA, SYSTEM, expand_answer, make_registry, render_read
from experiment import apply_summaries, extract_unit, repo_commit


HERE = Path(__file__).resolve().parent


def session_call(message: str, session_id: str, *, resume: bool, structured: bool, out: Path) -> dict[str, Any]:
    cmd = [
        "claude", "--print", "--model", "haiku", "--tools", "",
        "--output-format", "json", "--max-budget-usd", "0.08",
        "--setting-sources", "", "--effort", "low",
        "--system-prompt", SYSTEM,
    ]
    cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
    if structured:
        cmd += ["--json-schema", DECISION_SCHEMA]
    started = time.monotonic()
    try:
        process = subprocess.run(cmd, input=message, text=True, capture_output=True, cwd=out, timeout=300)
        payload = json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"error": str(exc), "elapsed_ms": int((time.monotonic() - started) * 1000)}
    usage = payload.get("usage") or {}
    result = {
        "answer": payload.get("result") or "",
        "decision": payload.get("structured_output") if structured else None,
        "error": payload.get("result") if payload.get("is_error") else None,
        "session_id": payload.get("session_id"),
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
    if result["session_id"] != session_id:
        result["error"] = result["error"] or "Session ID changed unexpectedly"
    if structured and not isinstance(result["decision"], dict):
        result["error"] = result["error"] or "Missing structured_output"
    return result


def catalog(workflow: dict[str, Any], mode: str) -> str:
    units = "\n".join(f"U{i} {uid.partition('::')[2]}" for i, uid in enumerate(workflow["units"], 1))
    citation = (
        "source file and line" if mode == "raw" else
        "E evidence IDs" if mode == "ids_guard_nosource" else
        "E evidence IDs for capsule facts, or source file and line for full-source fallback"
    )
    tools = (
        "read(U#) for numbered source" if mode == "raw" else
        "read(U#) for a capsule" if mode == "ids_guard_nosource" else
        "read(U#) for a capsule; read_source(U#) for numbered source only after you have read that unit's capsule"
    )
    return (
        f"Pinned source catalog (navigation only):\n{units}\n\n"
        f"Available actions: {tools}, or answer. Choose one per response. "
        "For a read, set unit to U# and answer to an empty string. "
        "For an answer, set unit to an empty string. "
        f"Answer in at most 140 words and cite {citation} for key claims. "
        "Do not infer behavior absent from observations; read source if needed."
    )


def phase_message(phase: dict[str, Any]) -> str:
    return (
        f"Task phase {phase['id']}: {phase['question']}\n"
        f"Read at least {phase['min_reads']} distinct source unit(s) in this phase before answering."
    )


def run_trajectory(workflow: dict[str, Any], mode: str, repeat: int, units: dict[str, dict[str, Any]], out: Path) -> dict[str, Any]:
    selected = [units[unit_id] for unit_id in workflow["units"]]
    registry = make_registry(selected)
    session_id = str(uuid.uuid4())
    resume = False
    calls: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    memory = ""
    status = "complete"
    capsule_seen: set[str] = set()
    for index, phase in enumerate(workflow["phases"]):
        if index == 2:
            instruction = (
                "Write a handoff memory for a fresh agent session in at most 120 words. "
                "Preserve verified source facts, exact guards, call arguments, and citations "
                "needed for the upcoming scenario. List unresolved facts. Do not answer the scenario yet."
            )
            result = session_call(instruction, session_id, resume=True, structured=False, out=out)
            calls.append({"kind": "compaction", "message": instruction, **result})
            if result.get("error"):
                status = "compaction_error"
                break
            memory = result["answer"]
            session_id = str(uuid.uuid4())
            resume = False
        message = (
            f"{catalog(workflow, mode)}\n\nCompaction memory from prior work:\n{memory}\n\n{phase_message(phase)}"
            if index == 2 else
            f"{catalog(workflow, mode)}\n\n{phase_message(phase)}" if index == 0 else
            phase_message(phase)
        )
        record: dict[str, Any] = {"id": phase["id"], "question": phase["question"], "actions": [], "answer": None}
        read_aliases: set[str] = set()
        for turn in range(1, 8):
            result = session_call(message, session_id, resume=resume, structured=True, out=out)
            calls.append({"kind": "decision", "phase": phase["id"], "turn": turn, "message": message, **result})
            resume = True
            if result.get("error"):
                status = "model_error"
                break
            decision = result["decision"]
            action, alias = decision.get("action"), decision.get("unit")
            if action == "answer" and len(read_aliases) >= phase["min_reads"]:
                answer = decision.get("answer") or ""
                record["answer_with_ids"] = answer if mode != "raw" else None
                record["answer_raw"] = answer
                record["answer"] = expand_answer(answer, registry)
                record["actions"].append({"action": "answer"})
                break
            if action == "answer":
                record["actions"].append({"action": "premature_answer"})
                message = "Tool protocol: this phase still requires a source read. Choose read(U#)."
                continue
            if action not in ("read", "read_source") or alias not in registry["units"] or (action == "read_source" and mode in ("raw", "ids_guard_nosource")):
                record["actions"].append({"action": "invalid", "unit": alias})
                message = "Tool protocol: invalid action or unit. Choose an available read or answer."
                continue
            if action == "read_source" and alias not in capsule_seen:
                record["actions"].append({"action": "source_before_capsule", "unit": alias})
                message = f"Tool protocol: read({alias}) capsule first. Request read_source({alias}) only if its capsule leaves a required detail unsupported."
                continue
            observation = render_read(alias, mode, phase["question"], units, registry, source=(action == "read_source"))
            if mode != "raw" and action == "read":
                capsule_seen.add(alias)
            read_aliases.add(alias)
            record["actions"].append({"action": action, "unit": alias, "observation": observation, "observation_bytes": len(observation.encode())})
            message = (
                f"Tool result for {action}({alias}):\n{observation}\n\n"
                f"You have read {len(read_aliases)} distinct unit(s) in this phase. "
                "Choose another read if needed, or answer from evidence."
            )
        else:
            status = "turn_limit"
        phases.append(record)
        if status != "complete" or record["answer"] is None:
            break
    totals = {key: sum(call.get(key) or 0 for call in calls) for key in (
        "input_tokens", "uncached_input_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens", "output_tokens", "cost_usd", "elapsed_ms"
    )}
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
    parser.add_argument("--out", type=Path, default=HERE.parent / "results" / "agent-workflows-session")
    parser.add_argument("--summary-file", type=Path, default=HERE.parent / "results" / "capsules-pydantic" / "summaries.json")
    parser.add_argument("--modes", default="raw,ids,ids_guard")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workflow", action="append")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if repo_commit(args.repo) != config["commit"]:
        parser.error("Pinned repository commit does not match workflow config")
    modes = args.modes.split(",")
    if not modes or len(set(modes)) != len(modes) or any(mode not in ("raw", "ids", "ids_guard", "ids_guard_nosource") for mode in modes):
        parser.error("--modes must select distinct values from raw,ids,ids_guard,ids_guard_nosource")
    workflows = [item for item in config["workflows"] if not args.workflow or item["id"] in args.workflow]
    if not workflows or args.repeats < 1:
        parser.error("No workflows or invalid repeat count")
    unit_ids = dict.fromkeys(uid for workflow in workflows for uid in workflow["units"])
    units = {uid: extract_unit(args.repo, uid) for uid in unit_ids}
    apply_summaries(units, args.summary_file)
    args.out.mkdir(parents=True, exist_ok=True)
    plan = [(workflow, mode, repeat) for workflow in workflows for mode in modes for repeat in range(1, args.repeats + 1)]
    random.Random(args.seed).shuffle(plan)
    results = []
    checkpoint = args.out / "runs.jsonl"
    checkpoint.write_text("")
    for index, (workflow, mode, repeat) in enumerate(plan, 1):
        print(f"[{index}/{len(plan)}] {workflow['id']} {mode} repeat {repeat}", flush=True)
        row = run_trajectory(workflow, mode, repeat, units, args.out)
        results.append(row)
        with checkpoint.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        print(f"  {row['status']}; {row['tool_reads']} reads; "
              f"{row['totals']['input_tokens']} input; {row['totals']['cache_read_input_tokens']} cached; "
              f"${row['totals']['cost_usd']:.6f}", flush=True)
    by_mode = {}
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
        "method": "Real resumed sessions before a model-written handoff; fresh session after compaction. Model-chosen reads from identical catalogs; raw source versus ID capsules.",
        "workflows": workflows, "by_mode": by_mode, "results": results,
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {args.out / 'report.json'}")
    return int(any(row["status"] != "complete" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
