#!/usr/bin/env python3
"""
Head-to-head benchmark: Codemesh vs CodeGraph vs Baseline.
Runs the same queries on the same repos, captures tool calls via stream-json,
then judges output quality.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
RESULTS_DIR = SCRIPT_DIR / "results" / "head_to_head"

# ── MCP Configs ─────────────────────────────────────────────────────

def write_codemesh_mcp_config(project_root: str) -> Path:
    config = {
        "mcpServers": {
            "codemesh": {
                "command": "node",
                "args": [str(PROJECT_DIR / "dist" / "index.js")],
                "env": {"CODEMESH_PROJECT_ROOT": project_root},
            }
        }
    }
    path = RESULTS_DIR / "codemesh-mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return path


def write_codegraph_mcp_config(project_root: str) -> Path:
    config = {
        "mcpServers": {
            "codegraph": {
                "command": "codegraph",
                "args": ["serve", "--mcp", "--path", project_root],
            }
        }
    }
    path = RESULTS_DIR / "codegraph-mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return path


# ── System Prompts ──────────────────────────────────────────────────

CODEMESH_PROMPT = """You MUST use codemesh_* MCP tools. Grep and Glob are disabled.

You have two exploration tools:

1. codemesh_explore — omni-tool with 3 actions:
   - action='search' — find things by text query
   - action='context' — see relations and symbols of a file/symbol
   - action='impact' — find reverse dependencies

2. codemesh_trace — CRITICAL for tracing execution flows. Takes a symbol name and follows the ENTIRE call chain to leaf nodes, returning every function in the path. Use this AFTER explore to follow a specific flow to completion. Do NOT stop mid-trace — always use codemesh_trace to follow call chains to their end.
   Example: codemesh_trace({ symbol: "Session.request", depth: 5 })

Workflow: explore to find entry points → trace to follow call chains → Read only if you need specific code not in trace results.

Every response includes projectRoot so you know the absolute path."""

CODEGRAPH_PROMPT = """You MUST use codegraph_* MCP tools. Grep and Glob are disabled.

Use codegraph_context as your primary tool — it returns full source code sections for any task.
Use codegraph_search to find specific symbols.
Use codegraph_callers/codegraph_callees to trace call chains.
Do NOT re-read files that codegraph already returned source code for. The source sections are complete and authoritative."""

CODEMESH_TOOLS = [
    "mcp__codemesh__codemesh_explore",
    "mcp__codemesh__codemesh_trace",
    "mcp__codemesh__codemesh_enrich",
    "mcp__codemesh__codemesh_workflow",
    "mcp__codemesh__codemesh_status",
]

CODEGRAPH_TOOLS = [
    "mcp__codegraph__codegraph_search",
    "mcp__codegraph__codegraph_context",
    "mcp__codegraph__codegraph_callers",
    "mcp__codegraph__codegraph_callees",
    "mcp__codegraph__codegraph_impact",
    "mcp__codegraph__codegraph_node",
    "mcp__codegraph__codegraph_files",
    "mcp__codegraph__codegraph_status",
]

# ── Colors ──────────────────────────────────────────────────────────

GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"


# ── Runner ──────────────────────────────────────────────────────────

def run_mode(
    prompt: str,
    mode: str,
    cwd: str,
    mcp_config: Optional[Path] = None,
    system_prompt: str = "",
    allowed_tools: Optional[List[str]] = None,
) -> Dict:
    """Run claude --print with stream-json to capture tool calls."""
    cmd = [
        "claude", "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--model", "opus",
        "--max-budget-usd", "2.00",
    ]

    if mcp_config:
        cmd.extend(["--mcp-config", str(mcp_config)])
    if system_prompt:
        cmd.extend(["--append-system-prompt", system_prompt])
    if mode != "baseline":
        cmd.extend(["--disallowedTools", "Grep,Glob"])
    if allowed_tools:
        for tool in allowed_tools:
            cmd.extend(["--allowedTools", tool])
        if mode != "baseline":
            cmd.extend(["--allowedTools", "Read"])
            cmd.extend(["--allowedTools", "Bash"])

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=600, cwd=cwd, env=env,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {"error": "timeout", "duration_ms": elapsed_ms, "tool_calls": [], "response": ""}

    # Parse stream-json events
    tool_calls = []
    final_result = {}
    response_text = ""

    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue

        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input", {})
                    tool_calls.append({"name": name, "input": inp})
                elif block.get("type") == "text" and block.get("text", "").strip():
                    response_text = block["text"]  # keep last text block

        elif ev.get("type") == "result":
            final_result = ev

    return {
        "tool_calls": tool_calls,
        "tool_call_count": len(tool_calls),
        "response": final_result.get("result", response_text),
        "cost_usd": final_result.get("total_cost_usd", 0),
        "num_turns": final_result.get("num_turns", 0),
        "duration_ms": final_result.get("duration_ms", elapsed_ms),
    }


def parse_tool_calls(tool_calls: List[Dict], prefix: str = "") -> Dict[str, int]:
    """Count tool calls by category."""
    counts: Dict[str, int] = {}
    for tc in tool_calls:
        name = tc["name"].replace("mcp__codemesh__", "").replace("mcp__codegraph__", "")
        counts[name] = counts.get(name, 0) + 1
    return counts


def _call_judge(prompt: str) -> Dict:
    """Call claude haiku as judge and parse JSON response."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    try:
        result = subprocess.run(
            ["claude", "--print", "--output-format", "json", "--model", "haiku",
             "--max-budget-usd", "0.10"],
            input=prompt, capture_output=True, text=True, timeout=60, env=env,
        )
        parsed = json.loads(result.stdout.strip())
        text = parsed.get("result", "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text)
    except Exception as e:
        return {"overall": 0, "notes": f"Judge error: {e}"}


def judge_response(query: str, response: str, rubric: str) -> Dict:
    """Score a single response independently against the rubric."""
    prompt = f"""Score this response (1-10) on: completeness, accuracy, depth, relevance, actionability.

Question: {query}
Rubric: {rubric}

Response:
{response}

Respond with ONLY JSON: {{"completeness": N, "accuracy": N, "depth": N, "relevance": N, "actionability": N, "overall": N, "notes": "one sentence"}}"""
    return _call_judge(prompt)


def judge_pairwise(query: str, rubric: str, response_a: str, label_a: str, response_b: str, label_b: str) -> Dict:
    """Compare two responses head-to-head. Returns which is better and why."""
    prompt = f"""You are comparing two AI assistant responses to the same question.

Question: {query}
Rubric: {rubric}

=== Response A ({label_a}) ===
{response_a[:3000]}

=== Response B ({label_b}) ===
{response_b[:3000]}

Compare these two responses. Which is better overall? Score each dimension (1-10) for both.

Respond with ONLY JSON:
{{
  "winner": "A" or "B" or "tie",
  "a_score": N,
  "b_score": N,
  "a_strengths": "one sentence",
  "b_strengths": "one sentence",
  "verdict": "one sentence explaining the winner"
}}"""
    return _call_judge(prompt)


# ── Benchmark Definitions ───────────────────────────────────────────

BENCHMARKS = [
    {
        "id": "alamofire",
        "repo": "https://github.com/Alamofire/Alamofire.git",
        "local_path": "/tmp/alamofire",
        "query": "Trace how a request flows from Session.request() through to the URLSession layer",
        "rubric": "Should trace Session.request() → Request creation → URLSessionTask creation → URLSession delegation. Should identify Session.swift, Request.swift, SessionDelegate.swift, DataRequest.swift.",
    },
    {
        "id": "excalidraw",
        "repo": "https://github.com/excalidraw/excalidraw.git",
        "local_path": "/tmp/excalidraw",
        "query": "How does collaborative editing and real-time sync work?",
        "rubric": "Should identify the collab module, WebSocket/HTTP transport, scene reconciliation, and conflict resolution mechanisms. Should mention key files in the collab/ directory.",
    },
]


# ── Main ────────────────────────────────────────────────────────────

def main() -> None:
    filter_ids = [a for a in sys.argv[1:] if not a.startswith("--")]
    skip_judge = "--skip-judge" in sys.argv

    benchmarks = BENCHMARKS
    if filter_ids:
        benchmarks = [b for b in benchmarks if b["id"] in filter_ids]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"{BLUE}{BOLD}=== Head-to-Head: Codemesh vs CodeGraph vs Baseline ==={NC}")
    print(f"  Running {len(benchmarks)} benchmark(s)")
    print()

    all_results = []

    for bench in benchmarks:
        bid = bench["id"]
        local_path = bench["local_path"]
        query = bench["query"]

        print(f"{BOLD}{bid}{NC}")
        print(f"  Query: \"{query[:70]}...\"")

        # Ensure repo exists
        if not Path(local_path).exists():
            print(f"  {RED}Repo not found at {local_path}. Clone it first.{NC}")
            continue

        # Write MCP configs
        cm_config = write_codemesh_mcp_config(local_path)
        cg_config = write_codegraph_mcp_config(local_path)

        # Re-index both
        print(f"  Indexing...")
        env = os.environ.copy()
        env["CODEMESH_PROJECT_ROOT"] = local_path
        subprocess.run(
            ["node", str(PROJECT_DIR / "dist" / "cli.js"), "index"],
            capture_output=True, env=env, timeout=120,
        )

        modes = {
            "baseline": {"mcp": None, "prompt": "", "tools": None},
            "codemesh": {"mcp": cm_config, "prompt": CODEMESH_PROMPT, "tools": CODEMESH_TOOLS},
            "codegraph": {"mcp": cg_config, "prompt": CODEGRAPH_PROMPT, "tools": CODEGRAPH_TOOLS},
        }

        results: Dict[str, Dict] = {}

        # Run all 3 modes in parallel
        def run_one(mode: str) -> tuple:
            cfg = modes[mode]
            print(f"  {YELLOW}[{mode}]{NC} Running...")
            r = run_mode(
                query, mode, local_path,
                mcp_config=cfg["mcp"],
                system_prompt=cfg["prompt"],
                allowed_tools=cfg["tools"],
            )
            return mode, r

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(run_one, m): m for m in modes}
            for future in as_completed(futures):
                mode, result = future.result()
                results[mode] = result
                if result.get("error"):
                    print(f"  {RED}[{mode}] FAILED: {result['error']}{NC}")
                else:
                    tc = result["tool_call_count"]
                    cost = result["cost_usd"]
                    dur = result["duration_ms"] / 1000
                    print(f"  {GREEN}[{mode}] {tc} calls, ${cost:.4f}, {dur:.1f}s{NC}")

        # Save raw results
        for mode, result in results.items():
            out_dir = RESULTS_DIR / mode
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / f"{bid}.json", "w") as f:
                json.dump({"benchmark": bid, "query": query, "mode": mode, **result}, f, indent=2)

        # Phase A: Independent quality scoring
        scores: Dict[str, Dict] = {}
        pairwise: Dict[str, Dict] = {}
        if not skip_judge:
            print(f"  {BLUE}Judging: independent scores...{NC}")
            for mode in modes:
                r = results.get(mode, {})
                resp = r.get("response", "")
                if resp:
                    scores[mode] = judge_response(query, resp, bench["rubric"])
                    print(f"    {mode:<12s} {scores[mode].get('overall', '?')}/10 — {scores[mode].get('notes', '')[:80]}")

            # Phase B: Pairwise comparisons
            print(f"  {BLUE}Judging: pairwise comparisons...{NC}")
            pairs = [
                ("codemesh", "baseline"),
                ("codegraph", "baseline"),
                ("codemesh", "codegraph"),
            ]
            for mode_a, mode_b in pairs:
                resp_a = results.get(mode_a, {}).get("response", "")
                resp_b = results.get(mode_b, {}).get("response", "")
                if resp_a and resp_b:
                    pw = judge_pairwise(query, bench["rubric"], resp_a, mode_a, resp_b, mode_b)
                    key = f"{mode_a}_vs_{mode_b}"
                    pairwise[key] = pw
                    winner = pw.get("winner", "?")
                    verdict = pw.get("verdict", "")[:80]
                    winner_label = mode_a if winner == "A" else (mode_b if winner == "B" else "tie")
                    print(f"    {mode_a} vs {mode_b}: {BOLD}{winner_label}{NC} — {verdict}")

        # Collect entry
        entry: Dict = {"id": bid, "query": query}
        for mode in modes:
            r = results.get(mode, {})
            tc_counts = parse_tool_calls(r.get("tool_calls", []))
            entry[mode] = {
                "tool_calls": r.get("tool_call_count", 0),
                "tool_breakdown": tc_counts,
                "cost": r.get("cost_usd", 0),
                "time_s": r.get("duration_ms", 0) / 1000,
                "turns": r.get("num_turns", 0),
                "quality": scores.get(mode, {}).get("overall", "?"),
                "quality_notes": scores.get(mode, {}).get("notes", ""),
            }
        entry["pairwise"] = pairwise
        all_results.append(entry)
        print()

    # Print comparison table
    print(f"{BOLD}{BLUE}=== Head-to-Head Results ==={NC}")
    print()
    print(f"{'Repo':<16s}  {'Mode':<12s}  {'Calls':>5s}  {'Time':>7s}  {'Cost':>9s}  {'Quality':>7s}  Tool Breakdown")
    print("-" * 110)

    for entry in all_results:
        for mode in ["baseline", "codemesh", "codegraph"]:
            d = entry.get(mode, {})
            tc = d.get("tool_calls", 0)
            t = d.get("time_s", 0)
            c = d.get("cost", 0)
            q = d.get("quality", "?")
            breakdown = d.get("tool_breakdown", {})
            bd_str = ", ".join(f"{k}={v}" for k, v in sorted(breakdown.items()))

            label = entry["id"] if mode == "baseline" else ""
            print(f"{label:<16s}  {mode:<12s}  {tc:>5d}  {t:>6.1f}s  ${c:>8.4f}  {q:>5}/10  {bd_str}")
        print()

    # Pairwise results
    print(f"{BOLD}{BLUE}=== Pairwise Comparisons ==={NC}")
    print()
    for entry in all_results:
        pw = entry.get("pairwise", {})
        if not pw:
            continue
        print(f"{BOLD}{entry['id']}{NC}")
        for key, result in pw.items():
            winner = result.get("winner", "?")
            a_label, b_label = key.split("_vs_")
            winner_name = a_label if winner == "A" else (b_label if winner == "B" else "TIE")
            a_score = result.get("a_score", "?")
            b_score = result.get("b_score", "?")
            verdict = result.get("verdict", "")
            print(f"  {a_label:<12s} vs {b_label:<12s} → {BOLD}{winner_name}{NC} ({a_label}={a_score}, {b_label}={b_score})")
            print(f"    {result.get('a_strengths', '')[:80]}")
            print(f"    {result.get('b_strengths', '')[:80]}")
            print(f"    Verdict: {verdict}")
            print()

    # Save summary
    with open(RESULTS_DIR / "summary.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Results saved to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
