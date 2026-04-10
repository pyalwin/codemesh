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

CODEMESH_SYSTEM_PROMPT = """CRITICAL INSTRUCTION: You MUST use codemesh_* MCP tools as your PRIMARY method for code exploration.

DO NOT use Grep or Glob to search for code. Those tools are disabled. Instead:

1. ALWAYS start with codemesh_query to find relevant files and symbols.
2. Use codemesh_context to get full context for a specific file before reading it.
3. Only use Read for targeted file reads AFTER codemesh tells you which files matter.
4. After reading code, call codemesh_enrich to record what you learned.

The codemesh knowledge graph has pre-indexed symbols from this codebase. Use it."""

MCP_TOOLS = [
    "mcp__codemesh__codemesh_query",
    "mcp__codemesh__codemesh_context",
    "mcp__codemesh__codemesh_enrich",
    "mcp__codemesh__codemesh_workflow",
    "mcp__codemesh__codemesh_impact",
    "mcp__codemesh__codemesh_status",
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

    if mode == "codemesh" and mcp_config:
        cmd.extend(["--mcp-config", str(mcp_config)])
        cmd.extend(["--append-system-prompt", CODEMESH_SYSTEM_PROMPT])
        cmd.extend(["--disallowedTools", "Grep,Glob"])
        for tool in MCP_TOOLS:
            cmd.extend(["--allowedTools", tool])
        cmd.extend(["--allowedTools", "Read"])
        cmd.extend(["--allowedTools", "Bash"])

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

        # Run baseline and codemesh in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: Dict[str, Dict] = {}

        def run_mode(mode: str) -> tuple:
            print(f"  {YELLOW}[{mode}]{NC} Running...")
            r = run_claude(query, mode, local_path, mcp_config if mode == "codemesh" else None)
            return mode, r

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(run_mode, m): m for m in ["baseline", "codemesh"]}
            for future in as_completed(futures):
                mode, result = future.result()
                results[mode] = result
                if "error" in result:
                    print(f"  {RED}[{mode}] FAILED: {result['error'][:100]}{NC}")
                else:
                    print(f"  {GREEN}[{mode}] turns={result['num_turns']}, cost=${result['cost_usd']:.4f}, time={result['duration_ms']/1000:.1f}s{NC}")

        # Save individual results
        for mode in ["baseline", "codemesh"]:
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

        entry = {
            "id": bid,
            "language": bench["language"],
            "index_stats": index_stats,
            "codegraph": cg,
            "codemesh_baseline": {
                "turns": bl.get("num_turns", 0),
                "cost": bl.get("cost_usd", 0),
                "time_s": bl.get("duration_ms", 0) / 1000,
            },
            "codemesh_augmented": {
                "turns": cm.get("num_turns", 0),
                "cost": cm.get("cost_usd", 0),
                "time_s": cm.get("duration_ms", 0) / 1000,
            },
        }
        all_results.append(entry)
        print()

    if dry_run or not all_results:
        return

    # Print comparison table
    print(f"{BOLD}{BLUE}=== Results: Codemesh vs CodeGraph ==={NC}")
    print()
    print(f"{'Repo':<18s} {'Files':>6s} {'Symbols':>8s} "
          f"{'CG calls':>8s} {'CM calls':>8s} "
          f"{'CG time':>8s} {'CM time':>8s} "
          f"{'BL time':>8s} {'BL cost':>9s} {'CM cost':>9s}")
    print("-" * 110)

    for r in all_results:
        cg = r["codegraph"]
        bl = r["codemesh_baseline"]
        cm = r["codemesh_augmented"]
        idx = r["index_stats"]

        print(
            f"{r['id']:<18s} "
            f"{idx.get('files', '?'):>6} "
            f"{idx.get('symbols', '?'):>8} "
            f"{cg['with_tool_calls']:>8} "
            f"{cm['turns']:>8} "
            f"{cg['with_time_s']:>7}s "
            f"{cm['time_s']:>7.1f}s "
            f"{bl['time_s']:>7.1f}s "
            f"${bl['cost']:>8.4f} "
            f"${cm['cost']:>8.4f}"
        )

    # Save aggregate results
    with open(RESULTS_DIR / "summary.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print()
    print(f"Results saved to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
