#!/usr/bin/env python3
"""
A/B harness: compares old vs new skills/codemesh.md across a bank of questions
against the codemesh repo itself. Records tool calls, cost, turns.

Usage:
  python3 eval/skill_ab_test.py [--model sonnet] [--runs 1] [--questions all|q1|q2|...]

Writes per-run JSON to /tmp/codemesh-ab-test/ and prints a summary table.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from typing import Dict, List

PROJECT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path("/tmp/codemesh-ab-test")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# 15 questions across categories. Category is used only for reporting.
QUESTIONS = [
    # ── lookup: where is X? (should collapse to 1 tool ideally) ───────────
    ("q1",  "lookup", "Where is PageRank computed in this codebase?"),
    ("q2",  "lookup", "Where is the SqliteBackend class defined?"),
    ("q3",  "lookup", "Where are stopwords used for FTS5 query filtering?"),
    ("q4",  "lookup", "Where do we store symbol embeddings for semantic search?"),
    ("q5",  "lookup", "Where is the tree-sitter parser invoked for each file?"),
    # ── comprehension: how does X work? (should be 1-3 tools) ─────────────
    ("q6",  "comprehension", "How does the codemesh_answer tool assemble its response?"),
    ("q7",  "comprehension", "How does incremental indexing skip unchanged files?"),
    ("q8",  "comprehension", "How does semantic search combine with FTS5 results?"),
    ("q9",  "comprehension", "How does the indexer attribute a call site to its containing symbol?"),
    ("q10", "comprehension", "How does PageRank get computed and stored on graph nodes?"),
    # ── trace: what calls/uses X? (should surface callers + callees) ─────
    ("q11", "trace", "What calls buildMapTree and what does that function do?"),
    ("q12", "trace", "What functions use semanticSearch from the embeddings module?"),
    ("q13", "trace", "What tools depend on the StorageBackend interface?"),
    # ── architecture: broader questions (may need 2-3 tools) ─────────────
    ("q14", "architecture", "What MCP tools does the codemesh server expose and what does each do?"),
    ("q15", "architecture", "How does codemesh process a file from disk to searchable graph nodes?"),
]

ALLOWED_TOOLS = [
    "mcp__codemesh__codemesh_answer",
    "mcp__codemesh__codemesh_explore",
    "mcp__codemesh__codemesh_trace",
    "mcp__codemesh__codemesh_enrich",
    "mcp__codemesh__codemesh_workflow",
    "mcp__codemesh__codemesh_status",
    "Read",
    "Bash",
]


def write_mcp_config() -> Path:
    config = {
        "mcpServers": {
            "codemesh": {
                "command": "node",
                "args": [str(PROJECT / "dist" / "index.js")],
                "env": {"CODEMESH_PROJECT_ROOT": str(PROJECT)},
            }
        }
    }
    path = RESULTS_DIR / "mcp.json"
    path.write_text(json.dumps(config, indent=2))
    return path


def run_one(question: str, skill_text: str, model: str, label: str) -> Dict:
    mcp = write_mcp_config()
    cmd = [
        "claude", "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        "--mcp-config", str(mcp),
        "--append-system-prompt", skill_text,
        "--max-budget-usd", "2.00",
        "--disallowedTools", "Grep,Glob",
    ]
    for tool in ALLOWED_TOOLS:
        cmd.extend(["--allowedTools", tool])

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd, input=question, capture_output=True, text=True,
            timeout=600, cwd=str(PROJECT), env=env,
        )
        wall_ms = int((time.monotonic() - start) * 1000)
    except subprocess.TimeoutExpired:
        return {
            "label": label, "question": question,
            "error": "timeout", "tool_calls": [],
            "tool_count": 0, "cost_usd": 0, "num_turns": 0,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "response": "",
        }

    tool_calls: List[Dict] = []
    final: Dict = {}
    response = ""
    for line in (result.stdout or "").strip().split("\n"):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_calls.append({
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                    })
                elif block.get("type") == "text":
                    txt = block.get("text", "").strip()
                    if txt:
                        response = txt
        elif ev.get("type") == "result":
            final = ev

    return {
        "label": label,
        "question": question,
        "tool_calls": tool_calls,
        "tool_count": len(tool_calls),
        "cost_usd": final.get("total_cost_usd", 0),
        "num_turns": final.get("num_turns", 0),
        "duration_ms": final.get("duration_ms", wall_ms),
        "response": final.get("result", response),
    }


def summarize(runs: List[Dict]) -> Dict:
    if not runs:
        return {"tool_count": 0, "num_turns": 0, "cost_usd": 0, "duration_ms": 0}
    return {
        "tool_count_mean": statistics.mean(r["tool_count"] for r in runs),
        "num_turns_mean": statistics.mean(r["num_turns"] for r in runs),
        "cost_usd_mean": statistics.mean(r["cost_usd"] for r in runs),
        "duration_ms_mean": statistics.mean(r["duration_ms"] for r in runs),
        "duration_ms_stdev": statistics.stdev(r["duration_ms"] for r in runs) if len(runs) > 1 else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--runs", type=int, default=1, help="Runs per cell for variance")
    ap.add_argument("--questions", default="all")
    args = ap.parse_args()

    old_skill = (RESULTS_DIR / "old-skill.md").read_text()
    new_skill = (RESULTS_DIR / "new-skill.md").read_text()

    if args.questions == "all":
        selected = QUESTIONS
    else:
        ids = set(args.questions.split(","))
        selected = [q for q in QUESTIONS if q[0] in ids]

    # Keyed by (qid, label) -> list of runs
    all_runs: Dict[tuple, List[Dict]] = {}

    for qid, category, q in selected:
        for label, skill in (("OLD", old_skill), ("NEW", new_skill)):
            key = (qid, label)
            all_runs[key] = []
            for run_idx in range(args.runs):
                run_label = f"{qid}-{label}-r{run_idx + 1}"
                print(f"→ running {run_label} ({args.model}) [{category}]")
                r = run_one(q, skill, args.model, run_label)
                out = RESULTS_DIR / f"{run_label}.json"
                out.write_text(json.dumps(r, indent=2))
                all_runs[key].append(r)
                print(f"  tools={r['tool_count']} turns={r['num_turns']} cost=${r['cost_usd']:.4f} time={r['duration_ms']/1000:.1f}s")

    # Per-question summary table
    print()
    print(f"{'QID':<5} {'Cat':<14} {'Tools O→N':<11} {'Turns O→N':<11} {'Cost O→N':<18} {'Time O→N':<14}")
    print("-" * 80)
    total_old_tools = total_new_tools = 0
    total_old_cost = total_new_cost = 0.0
    total_old_time = total_new_time = 0
    for qid, category, q in selected:
        o = summarize(all_runs[(qid, "OLD")])
        n = summarize(all_runs[(qid, "NEW")])
        print(
            f"{qid:<5} {category:<14} "
            f"{o['tool_count_mean']:>2.0f}→{n['tool_count_mean']:<2.0f}      "
            f"{o['num_turns_mean']:>2.0f}→{n['num_turns_mean']:<2.0f}      "
            f"${o['cost_usd_mean']:.3f}→${n['cost_usd_mean']:.3f}    "
            f"{o['duration_ms_mean']/1000:>4.1f}s→{n['duration_ms_mean']/1000:<4.1f}s"
        )
        total_old_tools += o['tool_count_mean']
        total_new_tools += n['tool_count_mean']
        total_old_cost += o['cost_usd_mean']
        total_new_cost += n['cost_usd_mean']
        total_old_time += o['duration_ms_mean']
        total_new_time += n['duration_ms_mean']
    print("-" * 80)
    print(
        f"{'TOT':<5} {'':<14} "
        f"{total_old_tools:>4.0f}→{total_new_tools:<4.0f}"
        f"  {'':<9}"
        f"${total_old_cost:.3f}→${total_new_cost:.3f}    "
        f"{total_old_time/1000:>4.1f}s→{total_new_time/1000:<4.1f}s"
    )

    # Per-category summary
    print()
    print("By category:")
    categories = sorted(set(c for _, c, _ in selected))
    for cat in categories:
        cat_qs = [q for q in selected if q[1] == cat]
        co_t = sum(summarize(all_runs[(q[0], "OLD")])['tool_count_mean'] for q in cat_qs)
        cn_t = sum(summarize(all_runs[(q[0], "NEW")])['tool_count_mean'] for q in cat_qs)
        co_c = sum(summarize(all_runs[(q[0], "OLD")])['cost_usd_mean'] for q in cat_qs)
        cn_c = sum(summarize(all_runs[(q[0], "NEW")])['cost_usd_mean'] for q in cat_qs)
        co_d = sum(summarize(all_runs[(q[0], "OLD")])['duration_ms_mean'] for q in cat_qs)
        cn_d = sum(summarize(all_runs[(q[0], "NEW")])['duration_ms_mean'] for q in cat_qs)
        n_qs = len(cat_qs)
        print(
            f"  {cat:<14} n={n_qs}  tools: {co_t:.0f}→{cn_t:.0f}  "
            f"cost: ${co_c:.3f}→${cn_c:.3f}  "
            f"time: {co_d/1000:.0f}s→{cn_d/1000:.0f}s"
        )

    # Summary delta
    print()
    tools_pct = 100 * (total_new_tools - total_old_tools) / total_old_tools if total_old_tools else 0
    cost_pct = 100 * (total_new_cost - total_old_cost) / total_old_cost if total_old_cost else 0
    time_pct = 100 * (total_new_time - total_old_time) / total_old_time if total_old_time else 0
    print(f"Overall: tools {tools_pct:+.1f}%  cost {cost_pct:+.1f}%  time {time_pct:+.1f}%  (NEW vs OLD)")


if __name__ == "__main__":
    main()
