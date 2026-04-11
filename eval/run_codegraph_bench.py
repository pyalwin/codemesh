#!/usr/bin/env python3
"""
CodeGraph benchmark replication for Codemesh.
Runs the same queries CodeGraph published on the same repos,
comparing baseline vs codemesh performance.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
BENCH_FILE = SCRIPT_DIR / "codegraph_benchmark.json"
RESULTS_DIR = SCRIPT_DIR / "results" / "codegraph_bench"
MCP_CONFIG_TEMPLATE = SCRIPT_DIR / "mcp-config.json"

CODEMESH_SYSTEM_PROMPT = """CRITICAL INSTRUCTION: You MUST use codemesh_* MCP tools. Grep and Glob are disabled.

YOUR PRIMARY TOOL IS codemesh_explore. It takes a task description and returns the COMPLETE connected subgraph with FULL SOURCE CODE for every symbol — files, call chains, callers, imports, everything. ONE call, complete picture.

Example: codemesh_explore({ task: "How does Session.request() flow to URLSession?" })

This returns all relevant symbols with their actual source code, call relationships, and file structure. You do NOT need to Read files — the source is in the response. Trust the graph results.

Other tools available:
- codemesh_query — quick search if you need a specific symbol name
- codemesh_context — detailed view of a single file
- codemesh_trace — follow a specific call chain
- codemesh_impact — reverse dependency analysis

But START with codemesh_explore for any exploration task."""

MCP_TOOLS = [
    "mcp__codemesh__codemesh_query",
    "mcp__codemesh__codemesh_context",
    "mcp__codemesh__codemesh_enrich",
    "mcp__codemesh__codemesh_workflow",
    "mcp__codemesh__codemesh_impact",
    "mcp__codemesh__codemesh_status",
    "mcp__codemesh__codemesh_trace",
    "mcp__codemesh__codemesh_explore",
]

GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"


def write_mcp_config(project_root: str) -> Path:
    """Write a temporary MCP config pointing to the given project root."""
    config = {
        "mcpServers": {
            "codemesh": {
                "command": "node",
                "args": [str(PROJECT_DIR / "dist" / "index.js")],
                "env": {
                    "CODEMESH_PROJECT_ROOT": project_root
                }
            }
        }
    }
    config_path = RESULTS_DIR / "mcp-config-tmp.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    return config_path


def clone_repo(repo_url: str, local_path: str) -> bool:
    """Clone repo if not already present."""
    if Path(local_path).exists():
        print(f"    Repo already at {local_path}")
        return True
    print(f"    Cloning {repo_url}...")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, local_path],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        print(f"    {RED}Clone failed: {result.stderr[:200]}{NC}")
        return False
    return True


def index_repo(local_path: str) -> Optional[Dict]:
    """Index a repo with Codemesh and return index stats."""
    # Clear existing index
    codemesh_dir = Path(local_path) / ".codemesh"
    if codemesh_dir.exists():
        import shutil
        shutil.rmtree(codemesh_dir)

    env = os.environ.copy()
    env["CODEMESH_PROJECT_ROOT"] = local_path

    result = subprocess.run(
        ["node", str(PROJECT_DIR / "dist" / "cli.js"), "index"],
        capture_output=True, text=True, timeout=600, env=env,
    )

    if result.returncode != 0:
        print(f"    {RED}Index failed: {result.stderr[:200]}{NC}")
        return None

    # Parse index output
    lines = result.stdout.strip().split("\n")
    stats: Dict = {"raw_output": result.stdout.strip()}
    for line in lines:
        if "Indexed" in line:
            stats["files"] = int(line.split()[1])
        if "Symbols" in line:
            stats["symbols"] = int(line.split()[-1])
        if "Edges" in line:
            stats["edges"] = int(line.split()[-1])
        if "Duration" in line:
            stats["duration_ms"] = int(line.split()[-1].replace("ms", ""))

    return stats


def run_claude(prompt: str, mode: str, cwd: str, mcp_config: Optional[Path] = None) -> Dict:
    """Run claude --print and return parsed JSON result."""
    result_obj: Optional[subprocess.CompletedProcess[str]] = None
    cmd = [
        "claude", "--print",
        "--output-format", "json",
        "--model", "opus",
        "--max-budget-usd", "2.00",
    ]

    if mode in ("codemesh", "graph-only") and mcp_config:
        cmd.extend(["--mcp-config", str(mcp_config)])
        cmd.extend(["--disallowedTools", "Grep,Glob"])
        for tool in MCP_TOOLS:
            cmd.extend(["--allowedTools", tool])

        if mode == "codemesh":
            # Graph + targeted reads (practical mode)
            cmd.extend(["--append-system-prompt", CODEMESH_SYSTEM_PROMPT])
            cmd.extend(["--allowedTools", "Read"])
            cmd.extend(["--allowedTools", "Bash"])
        else:
            # Graph-only (apples-to-apples with CodeGraph — no file reads)
            cmd.extend(["--append-system-prompt",
                CODEMESH_SYSTEM_PROMPT + "\n\nIMPORTANT: You can ONLY use codemesh_* tools. "
                "Read and Bash are NOT available. Answer entirely from the knowledge graph."
            ])

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    start = time.monotonic()
    try:
        result_obj = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=600, cwd=cwd, env=env,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if result_obj.returncode != 0:
            return {"error": result_obj.stderr.strip()[:200], "cost_usd": 0, "num_turns": 0, "duration_ms": elapsed_ms}

        parsed = json.loads(result_obj.stdout.strip())
        return {
            "result": parsed.get("result", ""),
            "session_id": parsed.get("session_id", ""),
            "cost_usd": parsed.get("cost_usd", 0) or parsed.get("total_cost_usd", 0),
            "num_turns": parsed.get("num_turns", 0),
            "duration_ms": parsed.get("duration_ms", 0) or elapsed_ms,
            "is_error": parsed.get("is_error", False),
        }
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {"error": "timeout", "cost_usd": 0, "num_turns": 0, "duration_ms": elapsed_ms}
    except json.JSONDecodeError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        stdout_snippet = result_obj.stdout[:500] if result_obj and result_obj.stdout else ""
        return {"error": f"JSON parse: {e}", "result": stdout_snippet, "cost_usd": 0, "num_turns": 0, "duration_ms": elapsed_ms}


def main() -> None:
    with open(BENCH_FILE) as f:
        bench_data = json.load(f)

    benchmarks = bench_data["benchmarks"]

    # Filter by args
    filter_ids = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    if filter_ids:
        benchmarks = [b for b in benchmarks if b["id"] in filter_ids]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"{BLUE}{BOLD}=== CodeGraph Benchmark Replication ==={NC}")
    print(f"  Running {len(benchmarks)} benchmark(s)")
    print()

    all_results: List[Dict] = []

    for bench in benchmarks:
        bid = bench["id"]
        repo = bench["repo"]
        local_path = bench["local_path"]
        query = bench["query"]
        cg = bench["codegraph_results"]

        print(f"{BOLD}{bid}{NC} ({bench['language']}, {cg['files']} files)")
        print(f"  Query: \"{query}\"")

        if dry_run:
            print(f"  {YELLOW}[DRY RUN] Would clone, index, and run baseline + codemesh{NC}")
            print()
            continue

        # Clone
        if not clone_repo(repo, local_path):
            continue

        # Index
        print(f"  Indexing...")
        index_stats = index_repo(local_path)
        if not index_stats:
            continue
        print(f"    {GREEN}Indexed {index_stats.get('files', '?')} files, {index_stats.get('symbols', '?')} symbols in {index_stats.get('duration_ms', '?')}ms{NC}")

        # Write MCP config for this repo
        mcp_config = write_mcp_config(local_path)

        # Run all three modes in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed

        modes = ["baseline", "codemesh", "graph-only"]
        results: Dict[str, Dict] = {}

        def run_mode(mode: str) -> tuple:
            print(f"  {YELLOW}[{mode}]{NC} Running...")
            mcp = mcp_config if mode in ("codemesh", "graph-only") else None
            r = run_claude(query, mode, local_path, mcp)
            return mode, r

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(run_mode, m): m for m in modes}
            for future in as_completed(futures):
                mode, result = future.result()
                results[mode] = result
                if "error" in result:
                    print(f"  {RED}[{mode}] FAILED: {result['error'][:100]}{NC}")
                else:
                    print(f"  {GREEN}[{mode}] turns={result['num_turns']}, cost=${result['cost_usd']:.4f}, time={result['duration_ms']/1000:.1f}s{NC}")

        # Save individual results
        for mode in modes:
            out_dir = RESULTS_DIR / mode
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / f"{bid}.json", "w") as f:
                json.dump({
                    "benchmark_id": bid,
                    "query": query,
                    "mode": mode,
                    "index_stats": index_stats,
                    **results.get(mode, {}),
                }, f, indent=2)

        # Compare
        bl = results.get("baseline", {})
        cm = results.get("codemesh", {})
        go = results.get("graph-only", {})

        entry = {
            "id": bid,
            "language": bench["language"],
            "index_stats": index_stats,
            "codegraph": cg,
            "baseline": {
                "turns": bl.get("num_turns", 0),
                "cost": bl.get("cost_usd", 0),
                "time_s": bl.get("duration_ms", 0) / 1000,
            },
            "codemesh": {
                "turns": cm.get("num_turns", 0),
                "cost": cm.get("cost_usd", 0),
                "time_s": cm.get("duration_ms", 0) / 1000,
            },
            "graph_only": {
                "turns": go.get("num_turns", 0),
                "cost": go.get("cost_usd", 0),
                "time_s": go.get("duration_ms", 0) / 1000,
            },
        }
        all_results.append(entry)
        print()

    if dry_run or not all_results:
        return

    # Print comparison table
    print(f"{BOLD}{BLUE}=== Results: Codemesh vs CodeGraph ==={NC}")
    print()
    print(f"{'Repo':<16s} {'Files':>6s} {'Syms':>6s} "
          f"{'':>3s}{'CodeGraph':>14s} "
          f"{'':>3s}{'Graph-Only':>14s} "
          f"{'':>3s}{'CM+Read':>14s} "
          f"{'':>3s}{'Baseline':>14s}")
    print(f"{'':16s} {'':>6s} {'':>6s} "
          f"{'':>3s}{'calls':>6s} {'time':>7s} "
          f"{'':>3s}{'calls':>6s} {'time':>7s} "
          f"{'':>3s}{'calls':>6s} {'time':>7s} "
          f"{'':>3s}{'calls':>6s} {'time':>7s}")
    print("-" * 115)

    for r in all_results:
        cg = r["codegraph"]
        bl = r["baseline"]
        cm = r["codemesh"]
        go = r["graph_only"]
        idx = r["index_stats"]

        print(
            f"{r['id']:<16s} "
            f"{idx.get('files', '?'):>6} "
            f"{idx.get('symbols', '?'):>6} "
            f"   {cg['with_tool_calls']:>6} {cg['with_time_s']:>6}s "
            f"   {go['turns']:>6} {go['time_s']:>6.1f}s "
            f"   {cm['turns']:>6} {cm['time_s']:>6.1f}s "
            f"   {bl['turns']:>6} {bl['time_s']:>6.1f}s"
        )

    print()
    print(f"{'':16s} {'':>6s} {'':>6s} "
          f"   {'cost':>13s} "
          f"   {'cost':>13s} "
          f"   {'cost':>13s} "
          f"   {'cost':>13s}")
    print("-" * 115)
    for r in all_results:
        cg = r["codegraph"]
        bl = r["baseline"]
        cm = r["codemesh"]
        go = r["graph_only"]
        print(
            f"{r['id']:<16s} {'':>6s} {'':>6s} "
            f"   {'N/A':>13s} "
            f"   ${go['cost']:>11.4f} "
            f"   ${cm['cost']:>11.4f} "
            f"   ${bl['cost']:>11.4f}"
        )

    # Save aggregate results
    with open(RESULTS_DIR / "summary.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print()
    print(f"Results saved to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
