#!/usr/bin/env python3
"""
Multi-session eval: proves the "gets smarter over time" claim.

Session 1: Explores a topic, enriches the graph
Session 2: Asks a follow-up question on the same topic
Session 3: Asks a different question that touches overlapping files

Measures whether sessions 2 and 3 are faster/cheaper because of
what session 1 wrote back.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Dict, List

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
RESULTS_DIR = SCRIPT_DIR / "results" / "multi_session"

CODEMESH_TOOLS = [
    "mcp__codemesh__codemesh_explore",
    "mcp__codemesh__codemesh_trace",
    "mcp__codemesh__codemesh_enrich",
    "mcp__codemesh__codemesh_workflow",
    "mcp__codemesh__codemesh_status",
]

CODEMESH_PROMPT = """You MUST use codemesh_* MCP tools. Grep and Glob are disabled.

Tools: codemesh_explore (search/context/impact), codemesh_trace (follow call chains).

MANDATORY WORKFLOW:

STEP 1 — DECOMPOSE: Break the question into sub-topics as a numbered checklist.

STEP 2 — EXPLORE: Search for each sub-topic. Use trace for call chains. Read files for details.

STEP 3 — VERIFY & ENRICH: Check your checklist is complete. Then ENRICH the graph — for EACH key file you explored, call codemesh_enrich with a summary of what you learned. Also call codemesh_workflow for any multi-file flows you traced. This is MANDATORY.

STEP 4 — WRITE: Complete answer with one section per sub-topic + file reference table."""

GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"


def write_mcp_config(project_root: str) -> Path:
    config = {
        "mcpServers": {
            "codemesh": {
                "command": "node",
                "args": [str(PROJECT_DIR / "dist" / "index.js")],
                "env": {"CODEMESH_PROJECT_ROOT": project_root},
            }
        }
    }
    path = RESULTS_DIR / "mcp-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
    return path


def run_claude(prompt: str, cwd: str, mcp_config: Path, use_codemesh: bool = True) -> Dict:
    cmd = [
        "claude", "--print",
        "--output-format", "json",
        "--model", "opus",
        "--max-budget-usd", "2.00",
    ]

    if use_codemesh:
        cmd.extend(["--mcp-config", str(mcp_config)])
        cmd.extend(["--append-system-prompt", CODEMESH_PROMPT])
        cmd.extend(["--disallowedTools", "Grep,Glob"])
        for tool in CODEMESH_TOOLS:
            cmd.extend(["--allowedTools", tool])
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

        if result.returncode != 0:
            return {"error": result.stderr.strip()[:200], "cost_usd": 0, "num_turns": 0, "duration_ms": elapsed_ms}

        parsed = json.loads(result.stdout.strip())
        return {
            "result": parsed.get("result", ""),
            "cost_usd": parsed.get("cost_usd", 0) or parsed.get("total_cost_usd", 0),
            "num_turns": parsed.get("num_turns", 0),
            "duration_ms": parsed.get("duration_ms", 0) or elapsed_ms,
        }
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "cost_usd": 0, "num_turns": 0, "duration_ms": int((time.monotonic() - start) * 1000)}
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse: {e}", "cost_usd": 0, "num_turns": 0, "duration_ms": int((time.monotonic() - start) * 1000)}


def get_graph_stats(project_root: str) -> Dict:
    env = os.environ.copy()
    env["CODEMESH_PROJECT_ROOT"] = project_root
    result = subprocess.run(
        ["node", str(PROJECT_DIR / "dist" / "cli.js"), "status"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    stats = {"raw": result.stdout.strip(), "concepts": 0, "workflows": 0, "stale": 0}
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        # Match "  concept: 11" but NOT "Stale concepts: 0"
        if line.startswith("concept:"):
            try:
                stats["concepts"] = int(line.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("workflow:"):
            try:
                stats["workflows"] = int(line.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("Stale"):
            try:
                stats["stale"] = int(line.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
    return stats


SCENARIOS = [
    {
        "id": "alamofire",
        "repo": "/tmp/alamofire",
        "sessions": [
            {
                "id": "session-1",
                "prompt": "Trace how a request flows from Session.request() through to the URLSession layer. Be thorough — trace every step.",
                "purpose": "Initial exploration + enrichment",
            },
            {
                "id": "session-2",
                "prompt": "How does Alamofire handle request retrying and retry policies? What files are involved?",
                "purpose": "Follow-up on overlapping code — should benefit from session 1 enrichment",
            },
            {
                "id": "session-3",
                "prompt": "Explain how response serialization works in Alamofire — from raw URLSession data to typed Swift objects.",
                "purpose": "Different topic but overlapping files — should benefit from session 1 enrichment of Session.swift, Request.swift",
            },
        ],
    },
]


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"{BLUE}{BOLD}=== Multi-Session Eval: Does the Graph Get Smarter? ==={NC}")
    print()

    for scenario in SCENARIOS:
        repo = scenario["repo"]
        sid = scenario["id"]

        if not Path(repo).exists():
            print(f"{RED}Repo not found: {repo}{NC}")
            continue

        print(f"{BOLD}{sid}{NC} ({repo})")

        # Fresh index — no prior concepts
        print(f"  Resetting graph (fresh index, no concepts)...")
        import shutil
        codemesh_dir = Path(repo) / ".codemesh"
        if codemesh_dir.exists():
            shutil.rmtree(codemesh_dir)

        env = os.environ.copy()
        env["CODEMESH_PROJECT_ROOT"] = repo
        subprocess.run(
            ["node", str(PROJECT_DIR / "dist" / "cli.js"), "index"],
            capture_output=True, env=env, timeout=120,
        )

        mcp_config = write_mcp_config(repo)
        stats_before = get_graph_stats(repo)
        print(f"  Graph before: {stats_before.get('concepts', 0)} concepts, {stats_before.get('workflows', 0)} workflows")

        all_session_results = []

        for session in scenario["sessions"]:
            sess_id = session["id"]
            prompt = session["prompt"]
            purpose = session["purpose"]

            print()
            print(f"  {YELLOW}[{sess_id}]{NC} {purpose}")
            print(f"  Query: \"{prompt[:70]}...\"")

            # Check graph state before this session
            stats = get_graph_stats(repo)
            concepts_before = stats.get("concepts", 0)

            # Run the session
            result = run_claude(prompt, repo, mcp_config)

            if result.get("error"):
                print(f"  {RED}FAILED: {result['error'][:100]}{NC}")
                all_session_results.append({"session": sess_id, "error": result["error"]})
                continue

            cost = result["cost_usd"]
            turns = result["num_turns"]
            duration = result["duration_ms"] / 1000

            # Check graph state after this session
            stats_after = get_graph_stats(repo)
            concepts_after = stats_after.get("concepts", 0)
            concepts_added = concepts_after - concepts_before

            print(f"  {GREEN}Done: turns={turns}, cost=${cost:.4f}, time={duration:.1f}s{NC}")
            print(f"  Graph: {concepts_before} → {concepts_after} concepts (+{concepts_added})")

            session_result = {
                "session": sess_id,
                "purpose": purpose,
                "prompt": prompt,
                "turns": turns,
                "cost": cost,
                "time_s": duration,
                "concepts_before": concepts_before,
                "concepts_after": concepts_after,
                "concepts_added": concepts_added,
                "response_length": len(result.get("result", "")),
            }
            all_session_results.append(session_result)

            # Save individual session result
            out_dir = RESULTS_DIR / sid
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / f"{sess_id}.json", "w") as f:
                json.dump({**session_result, "response": result.get("result", "")}, f, indent=2)

        # Print comparison
        print()
        print(f"  {BOLD}{BLUE}=== Session Comparison ==={NC}")
        print(f"  {'Session':<12s} {'Turns':>6s} {'Cost':>9s} {'Time':>7s} {'Concepts+':>10s} {'Purpose'}")
        print(f"  {'-'*80}")

        for r in all_session_results:
            if "error" in r:
                print(f"  {r['session']:<12s} {'FAILED':>6s}")
                continue
            print(
                f"  {r['session']:<12s} "
                f"{r['turns']:>6d} "
                f"${r['cost']:>8.4f} "
                f"{r['time_s']:>6.1f}s "
                f"{'+' + str(r['concepts_added']):>9s} "
                f"{r['purpose'][:40]}"
            )

        # Final graph state
        final_stats = get_graph_stats(repo)
        print()
        print(f"  Final graph: {final_stats.get('concepts', 0)} concepts, {final_stats.get('workflows', 0)} workflows")

        # Save summary
        with open(RESULTS_DIR / sid / "summary.json", "w") as f:
            json.dump({
                "scenario": sid,
                "sessions": all_session_results,
                "final_graph": final_stats,
            }, f, indent=2)

    print()
    print(f"{GREEN}{BOLD}=== Multi-session eval complete ==={NC}")


if __name__ == "__main__":
    main()
