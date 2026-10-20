#!/usr/bin/env python3.11
"""Paired, resumed-session evaluation of map-first code navigation.

Claude chooses each tool call. The filesystem arm has grep and bounded reads;
the map arm has live Codemesh map/source plus the same filesystem fallback.
Both arms receive identical task phases and a fresh-session handoff.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import subprocess
import time
import uuid
from typing import Any

from grep_router import ReadRouter, native_read, route_grep


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS = ROOT / "eval" / "map_navigation_tasks.json"
SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["map", "source", "grep", "read", "answer"]},
        "node": {"type": "string"},
        "relation": {"type": "string"},
        "cursor": {"type": "string"},
        "query": {"type": "string"},
        "path": {"type": "string"},
        "start": {"type": "integer"},
        "limit": {"type": "integer"},
        "answer": {"type": "string"},
    },
    "required": ["action", "answer"],
    "additionalProperties": False,
}, separators=(",", ":"))


def schema_for_mode(mode: str) -> str:
    schema = json.loads(SCHEMA)
    schema["properties"]["action"]["enum"] = (
        ["map", "source", "grep", "read", "answer"] if mode == "map"
        else ["grep", "read", "answer"]
    )
    return json.dumps(schema, separators=(",", ":"))


def system_prompt(mode: str) -> str:
    common = (
        "You are investigating a pinned source repository. The tool catalog is navigation, "
        "not evidence. Base factual claims only on tool observations and your own verified "
        "handoff memory. Choose exactly one action per response using the JSON schema. "
        "For a tool call, leave answer empty. For a final answer, use action=answer and cite "
        "source file and line when observed. Answer in at most 140 words. "
        "A tool result arrives in the next user message."
    )
    if mode == "map":
        return common + (
            " Available actions: map(node='repo' or a returned exact ID, optional relation "
            "contains/calls/called_by/imports/imported_by/extends and cursor); source(node=exact "
            "symbol ID); grep(query); read(path, start, limit). Start with the live Codemesh "
            "map for discovery, expand only relevant branches, then read exact symbols. "
            "Use grep/read if the index does not expose needed source. Map entries and "
            "signatures alone do not prove function behavior."
        )
    if mode == "routed":
        return common + (
            " Available actions: grep(query) for repository-wide literal search; "
            "read(path, start, limit) for numbered source lines; answer. "
            "Search results may include verified symbol spans and read results may "
            "include a verified AST tail beyond the requested slice. Read source "
            "before making behavioral claims."
        )
    return common + (
        " Available actions: grep(query) for repository-wide literal search; "
        "read(path, start, limit) for numbered source lines; answer. "
        "Use grep to locate code and read the relevant source before answering."
    )


def model_call(message: str, session_id: str, mode: str, *, resume: bool,
               structured: bool, cwd: Path, budget_usd: float) -> dict[str, Any]:
    cmd = [
        "claude", "--print", "--model", "haiku", "--tools", "",
        "--output-format", "json", "--max-budget-usd", str(budget_usd),
        "--setting-sources", "", "--effort", "low",
        "--system-prompt", system_prompt(mode),
    ]
    cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
    if structured:
        cmd += ["--json-schema", schema_for_mode(mode)]
    started = time.monotonic()
    try:
        process = subprocess.run(cmd, input=message, text=True, capture_output=True,
                                 cwd=cwd, timeout=180)
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
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
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
        result["error"] = result["error"] or (
            "Missing structured_output: " + (payload.get("result") or process.stderr or process.stdout)[-1200:]
        )
    return result


def safe_path(repo: Path, path: str) -> Path:
    candidate = (repo / path).resolve()
    if not candidate.is_relative_to(repo.resolve()) or not candidate.is_file():
        raise ValueError("Path must name a file inside the target repository")
    return candidate


def tool_call(decision: dict[str, Any], mode: str, repo: Path,
              read_router: ReadRouter | None = None) -> tuple[str, str, dict[str, Any] | None]:
    action = decision.get("action")
    if action in ("map", "source"):
        if action == "map" and mode != "map":
            raise ValueError("Map is unavailable in this mode")
        if action == "source" and mode != "map":
            raise ValueError("Codemesh tools are unavailable in filesystem mode")
        node = decision.get("node") or "repo"
        cmd = ["node", str(ROOT / "dist" / "cli.js"), "explore", action]
        if action == "map":
            if node != "repo":
                cmd += ["--node", node]
            relation = decision.get("relation") or "contains"
            if relation != "contains":
                cmd += ["--relation", relation]
            if decision.get("cursor"):
                cmd += ["--cursor", decision["cursor"]]
            cmd += ["--limit", "30"]
        else:
            if not node.startswith("symbol:"):
                raise ValueError("source requires an exact symbol ID")
            cmd.append(node)
        env = os.environ.copy()
        env["CODEMESH_PROJECT_ROOT"] = str(repo)
        process = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT,
                                 env=env, timeout=30)
        if process.returncode:
            raise ValueError(process.stderr.strip()[-1500:] or "Codemesh call failed")
        data = json.loads(process.stdout)
        data.pop("projectRoot", None)
        if action == "source":
            data.pop("absolutePath", None)
        return action, json.dumps(data, separators=(",", ":")), None
    if action == "grep":
        query = (decision.get("query") or "").strip()
        if not query or len(query) > 120:
            raise ValueError("grep requires a literal query of at most 120 characters")
        if mode == "routed":
            observation, route = route_grep(query, repo)
            return action, observation, route
        from grep_router import native_grep
        return action, native_grep(query, repo), None
    if action == "read":
        path = (decision.get("path") or "").removeprefix("./")
        start = decision.get("start") or 1
        limit = decision.get("limit") or 100
        if not isinstance(start, int) or not isinstance(limit, int) or start < 1 or limit < 1 or limit > 180:
            raise ValueError("read requires start >= 1 and 1 <= limit <= 180")
        if mode == "routed":
            if read_router is None:
                raise ValueError("Read router was not initialized")
            observation, route = read_router.route_read(path, start, limit, repo)
            return action, observation, route
        return action, native_read(path, start, limit, repo), None
    raise ValueError("Unknown action")


def phase_prompt(phase: dict[str, Any], mode: str, memory: str | None,
                 required_reads: int) -> str:
    intro = "Use the map/source tools to choose a code path." if mode == "map" else \
        "Use grep/read to choose a code path."
    request = (
        f"Task phase {phase['id']}: {phase['question']}\n{intro}\n" +
        (f"Read at least {required_reads} source unit(s) before answering."
         if required_reads else "Reuse verified source already seen; read again only if needed.")
    )
    return f"Compaction memory from prior work:\n{memory}\n\n{request}" if memory is not None else request


def run_trajectory(workflow: dict[str, Any], mode: str, repeat: int,
                   repo: Path, out: Path, max_turns: int,
                   budget_usd: float) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    resume = False
    status = "complete"
    memory = ""
    phases: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    tool_elapsed_ms = 0
    read_router = ReadRouter() if mode == "routed" else None
    for phase_index, phase in enumerate(workflow["phases"]):
        if phase_index == 2:
            instruction = (
                "Write a handoff memory for a fresh agent in at most 120 words. "
                "Keep verified source facts, exact guards and delegates, and citations needed "
                "for the next scenario. List unresolved facts. Do not answer the scenario yet."
            )
            result = model_call(instruction, session_id, mode, resume=True,
                                structured=False, cwd=out, budget_usd=budget_usd)
            calls.append({"kind": "compaction", "message": instruction, **result})
            if result.get("error"):
                status = "compaction_error"
                break
            memory = result["answer"]
            session_id = str(uuid.uuid4())
            resume = False
            if read_router is not None:
                read_router.reset_session()
        # A forced read in phase two penalizes an agent that already inspected
        # the sibling source in phase one. Only the first phase requires a read.
        required_reads = phase["min_reads"] if phase_index == 0 else 0
        message = phase_prompt(phase, mode, memory if phase_index == 2 else None,
                               required_reads)
        record: dict[str, Any] = {"id": phase["id"], "question": phase["question"],
                                  "actions": [], "answer": None}
        source_reads: set[str] = set()
        for turn in range(1, max_turns + 1):
            result = model_call(message, session_id, mode, resume=resume,
                                structured=True, cwd=out, budget_usd=budget_usd)
            calls.append({"kind": "decision", "phase": phase["id"], "turn": turn,
                          "message": message, **result})
            resume = True
            if result.get("error"):
                status = "model_error"
                break
            decision = result["decision"]
            action = decision.get("action")
            if action == "answer":
                if len(source_reads) < required_reads:
                    record["actions"].append({"action": "premature_answer"})
                    message = "Tool protocol: read the required source before answering."
                    continue
                record["answer"] = decision.get("answer") or ""
                record["actions"].append({"action": "answer"})
                break
            started = time.monotonic()
            try:
                actual, observation, route = tool_call(decision, mode, repo, read_router)
                error = None
            except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                actual, observation, error, route = str(action), f"Tool error: {exc}", str(exc), None
            tool_elapsed_ms += int((time.monotonic() - started) * 1000)
            key = decision.get("node") if action == "source" else \
                f"{decision.get('path')}:{decision.get('start') or 1}" if action == "read" else None
            if action in ("source", "read") and not error and key:
                source_reads.add(key)
            record["actions"].append({"action": actual, "input": decision,
                                      "observation": observation, "error": error,
                                      "route": route,
                                      "observation_bytes": len(observation.encode())})
            message = (
                f"Tool result for {actual}:\n{observation}\n\n"
                f"Distinct source reads in this phase: {len(source_reads)}. "
                "Choose another action or answer from observed evidence."
            )
        else:
            status = "turn_limit"
        phases.append(record)
        if status != "complete" or record["answer"] is None:
            break
    totals = {key: sum(call.get(key) or 0 for call in calls) for key in (
        "input_tokens", "uncached_input_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens", "output_tokens", "cost_usd", "elapsed_ms")}
    actions = [action for phase in phases for action in phase["actions"]]
    reads = [action for action in actions if action["action"] in ("source", "read") and not action.get("error")]
    read_keys = [action["input"].get("node") if action["action"] == "source" else
                 (action["input"].get("path"), action["input"].get("start") or 1) for action in reads]
    return {
        "workflow": workflow["id"], "mode": mode, "repeat": repeat,
        "status": status, "phases": phases, "compaction_memory": memory,
        "calls": calls, "totals": totals, "tool_elapsed_ms": tool_elapsed_ms,
        "tool_calls": sum(action["action"] not in ("answer", "premature_answer") for action in actions),
        "source_reads": len(reads), "repeat_reads": len(read_keys) - len(set(map(str, read_keys))),
        "map_calls": sum(action["action"] == "map" for action in actions),
        "grep_calls": sum(action["action"] == "grep" for action in actions),
        "indexed_grep_calls": sum((action.get("route") or {}).get("backend") == "codemesh"
                                  for action in actions if action.get("action") == "grep"),
        "native_grep_calls": sum((action.get("route") or {}).get("backend") == "native"
                                 for action in actions if action.get("action") == "grep"),
        "hybrid_grep_calls": sum((action.get("route") or {}).get("backend") == "hybrid"
                                 for action in actions if action.get("action") == "grep"),
        "routed_read_calls": sum((action.get("route") or {}).get("backend") == "codemesh"
                                 for action in actions if action.get("action") == "read"),
        "dedup_read_calls": sum((action.get("route") or {}).get("reason") == "repeat_read"
                                for action in actions if action.get("action") == "read"),
        "filesystem_fallbacks": sum(action["action"] in ("grep", "read") for action in actions) if mode == "map" else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "results" / "map-navigation")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-turns", type=int, default=20,
                        help="Decision limit per phase, identical in both arms")
    parser.add_argument("--budget-usd", type=float, default=1.0,
                        help="Per-call Claude CLI budget")
    parser.add_argument("--modes", default="filesystem,map,routed")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workflow", action="append")
    args = parser.parse_args()
    config = json.loads(args.tasks.read_text())
    commit = subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True).strip()
    if commit != config["commit"]:
        parser.error(f"Target commit mismatch: {commit} != {config['commit']}")
    workflows = [item for item in config["workflows"] if not args.workflow or item["id"] in args.workflow]
    modes = args.modes.split(",")
    if (not workflows or args.repeats < 1 or args.max_turns < 1 or args.budget_usd <= 0
            or not modes or len(set(modes)) != len(modes)
            or any(mode not in ("filesystem", "map", "routed") for mode in modes)):
        parser.error("No workflows or invalid repeat/turn/budget/modes")
    args.out.mkdir(parents=True, exist_ok=True)
    plan = [(workflow, mode, repeat) for workflow in workflows
            for mode in modes for repeat in range(1, args.repeats + 1)]
    random.Random(args.seed).shuffle(plan)
    results = []
    checkpoint = args.out / "runs.jsonl"
    checkpoint.write_text("")
    for index, (workflow, mode, repeat) in enumerate(plan, 1):
        print(f"[{index}/{len(plan)}] {workflow['id']} {mode} repeat {repeat}", flush=True)
        row = run_trajectory(workflow, mode, repeat, args.repo, args.out,
                             args.max_turns, args.budget_usd)
        results.append(row)
        with checkpoint.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        print(f"  {row['status']}; {row['tool_calls']} tools; {row['source_reads']} reads; "
              f"{row['totals']['input_tokens']} input; ${row['totals']['cost_usd']:.6f}", flush=True)
    by_mode = {}
    for mode in modes:
        rows = [row for row in results if row["mode"] == mode]
        by_mode[mode] = {
            "trajectories": len(rows), "complete": sum(row["status"] == "complete" for row in rows),
            **{key: sum(row[key] for row in rows) for key in (
                "tool_calls", "source_reads", "repeat_reads", "map_calls", "grep_calls",
                "filesystem_fallbacks", "indexed_grep_calls", "native_grep_calls",
                "hybrid_grep_calls", "routed_read_calls", "dedup_read_calls",
                "tool_elapsed_ms")},
            **{key: sum(row["totals"][key] for row in rows) for key in (
                "input_tokens", "uncached_input_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "output_tokens", "cost_usd", "elapsed_ms")},
        }
    report = {"repository": config["repository"], "commit": commit,
              "model": "haiku", "repeats": args.repeats, "modes": modes,
              "per_call_budget_usd": args.budget_usd,
              "max_turns_per_phase": args.max_turns, "results": results, "by_mode": by_mode,
              "method": "Resumed model sessions with simulated tool observations; fresh session after model-written handoff. Filesystem mode has literal grep and bounded reads. Map mode has live Codemesh map/source and identical fallback. Routed mode annotates fresh indexed declarations in shadow-native grep output, and provides numbered reads with verified AST tail evidence and exact-repeat deduplication. The routing safety gate still executes native grep, so CPU savings are not measured. Initial indexing cost excluded; report model cost from Claude CLI."}
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(by_mode, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
