#!/usr/bin/env python3
"""
Codemesh Eval Harness

Runs baseline vs codemesh-augmented Claude tasks and compares results.
Uses `claude --print --output-format json` for clean single-blob JSON output.

Usage:
    python3 eval/run_eval.py                  # Run all tasks
    python3 eval/run_eval.py discovery-1      # Run specific task(s)
    python3 eval/run_eval.py --dry-run        # Print what would run, no Claude calls
"""

from __future__ import annotations

import subprocess
import json
import os
import sys
import time
import shutil
from pathlib import Path
from typing import Optional, List, Dict

SCRIPT_DIR = Path(__file__).parent
TASKS_FILE = SCRIPT_DIR / "tasks.json"
MCP_CONFIG = SCRIPT_DIR / "mcp-config.json"
RESULTS_DIR = SCRIPT_DIR / "results"
TARGET_CWD = "/tmp/pydantic"

CODEMESH_SYSTEM_PROMPT = """CRITICAL INSTRUCTION: You MUST use codemesh_* MCP tools as your PRIMARY method for code exploration.

DO NOT use Grep or Glob to search for code. Those tools are disabled. Instead:

1. ALWAYS start with codemesh_query to find relevant files and symbols.
   Example: codemesh_query({ query: "validator" })

2. Use codemesh_context to get full context for a specific file before reading it.
   Example: codemesh_context({ path: "pydantic/main.py" })

3. Use codemesh_impact to check dependencies before suggesting changes.
   Example: codemesh_impact({ path: "pydantic/fields.py" })

4. Only use Read for targeted file reads AFTER codemesh_query tells you which files matter.

5. After reading code, call codemesh_enrich to record what you learned.
   Example: codemesh_enrich({ path: "pydantic/main.py", summary: "Defines BaseModel..." })

The codemesh knowledge graph has 13,000+ pre-indexed symbols from this codebase. Use it."""

CODEMESH_SKILLS_DIR = str(SCRIPT_DIR.parent / "skills")

# ── Colors ────────────────────────────────────────────────────────────────────

GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"


def load_tasks(filter_ids: Optional[List[str]] = None) -> List[Dict]:
    """Load tasks from tasks.json, optionally filtering by ID."""
    with open(TASKS_FILE) as f:
        data = json.load(f)
    tasks = data["tasks"]
    if filter_ids:
        tasks = [t for t in tasks if t["id"] in filter_ids]
    return tasks


def run_claude(prompt: str, mode: str) -> dict:
    """Run claude --print and return parsed JSON result."""
    result: Optional[subprocess.CompletedProcess[str]] = None
    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--max-budget-usd", "1.00",
    ]

    if mode == "codemesh":
        cmd.extend(["--mcp-config", str(MCP_CONFIG)])
        cmd.extend(["--append-system-prompt", CODEMESH_SYSTEM_PROMPT])
        # Disable Grep and Glob to force discovery through codemesh
        cmd.extend(["--disallowedTools", "Grep,Glob"])
        # Explicitly allow MCP tools — REQUIRED or they're blocked
        for tool in [
            "mcp__codemesh__codemesh_query",
            "mcp__codemesh__codemesh_context",
            "mcp__codemesh__codemesh_enrich",
            "mcp__codemesh__codemesh_workflow",
            "mcp__codemesh__codemesh_impact",
            "mcp__codemesh__codemesh_status",
        ]:
            cmd.extend(["--allowedTools", tool])
        # Also allow Read and Bash for targeted reading
        cmd.extend(["--allowedTools", "Read"])
        cmd.extend(["--allowedTools", "Bash"])

    # Build environment -- pop CLAUDECODE to avoid nested session error
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=TARGET_CWD,
            env=env,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if result.returncode != 0:
            return {
                "error": result.stderr.strip(),
                "result": "",
                "cost_usd": 0,
                "num_turns": 0,
                "duration_ms": elapsed_ms,
            }

        parsed = json.loads(result.stdout.strip())
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
        return {
            "error": "timeout (300s)",
            "result": "",
            "cost_usd": 0,
            "num_turns": 0,
            "duration_ms": elapsed_ms,
        }
    except json.JSONDecodeError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        stdout_snippet = result.stdout[:500] if result and result.stdout else ""
        return {
            "error": f"JSON parse error: {e}",
            "result": stdout_snippet,
            "cost_usd": 0,
            "num_turns": 0,
            "duration_ms": elapsed_ms,
        }


def save_result(mode: str, task_id: str, result: dict, task_meta: dict) -> None:
    """Save a single result as JSON."""
    out_dir = RESULTS_DIR / mode
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id,
        "category": task_meta.get("category", ""),
        "prompt": task_meta.get("prompt", ""),
        "mode": mode,
        **result,
    }
    with open(out_dir / f"{task_id}.json", "w") as f:
        json.dump(payload, f, indent=2)


def print_comparison_table(results: dict) -> None:
    """Print a formatted comparison table of baseline vs codemesh results."""
    # Collect task IDs from results
    task_ids = sorted(set(
        list(results.get("baseline", {}).keys()) +
        list(results.get("codemesh", {}).keys())
    ))

    if not task_ids:
        print(f"{YELLOW}No results to compare.{NC}")
        return

    # Column widths
    w_task = 20
    w_cat = 12
    w_base = 16
    w_cm = 16
    w_delta = 12

    sep_line = (
        f"{'=' * w_task}={'=' * w_cat}={'=' * w_base}={'=' * w_cm}={'=' * w_delta}"
    )

    print(f"\n{BOLD}{BLUE}=== Comparison: Turns ==={NC}\n")
    print(
        f"{'Task':<{w_task}} {'Category':<{w_cat}} "
        f"{'Base Turns':>{w_base}} {'CM Turns':>{w_cm}} "
        f"{'Delta':>{w_delta}}"
    )
    print(sep_line)

    for tid in task_ids:
        b = results.get("baseline", {}).get(tid, {})
        c = results.get("codemesh", {}).get(tid, {})
        cat = b.get("category", c.get("category", ""))
        b_turns = b.get("num_turns", 0)
        c_turns = c.get("num_turns", 0)
        if b_turns > 0:
            delta_pct = ((c_turns - b_turns) / b_turns) * 100
            delta_str = f"{delta_pct:+.1f}%"
        else:
            delta_str = "N/A"

        color = GREEN if delta_str.startswith("-") else (RED if delta_str.startswith("+") else NC)
        print(
            f"{tid:<{w_task}} {cat:<{w_cat}} "
            f"{b_turns:>{w_base}} {c_turns:>{w_cm}} "
            f"{color}{delta_str:>{w_delta}}{NC}"
        )

    print()
    print(f"{BOLD}{BLUE}=== Comparison: Cost ==={NC}\n")
    print(
        f"{'Task':<{w_task}} {'Category':<{w_cat}} "
        f"{'Base Cost':>{w_base}} {'CM Cost':>{w_cm}} "
        f"{'Delta':>{w_delta}}"
    )
    print(sep_line)

    for tid in task_ids:
        b = results.get("baseline", {}).get(tid, {})
        c = results.get("codemesh", {}).get(tid, {})
        cat = b.get("category", c.get("category", ""))
        b_cost = b.get("cost_usd", 0)
        c_cost = c.get("cost_usd", 0)
        if b_cost > 0:
            delta_pct = ((c_cost - b_cost) / b_cost) * 100
            delta_str = f"{delta_pct:+.1f}%"
        else:
            delta_str = "N/A"

        color = GREEN if delta_str.startswith("-") else (RED if delta_str.startswith("+") else NC)
        print(
            f"{tid:<{w_task}} {cat:<{w_cat}} "
            f"{'$' + f'{b_cost:.4f}':>{w_base}} {'$' + f'{c_cost:.4f}':>{w_cm}} "
            f"{color}{delta_str:>{w_delta}}{NC}"
        )

    print()
    print(f"{BOLD}{BLUE}=== Comparison: Duration ==={NC}\n")
    print(
        f"{'Task':<{w_task}} {'Category':<{w_cat}} "
        f"{'Base (s)':>{w_base}} {'CM (s)':>{w_cm}} "
        f"{'Delta':>{w_delta}}"
    )
    print(sep_line)

    for tid in task_ids:
        b = results.get("baseline", {}).get(tid, {})
        c = results.get("codemesh", {}).get(tid, {})
        cat = b.get("category", c.get("category", ""))
        b_dur = b.get("duration_ms", 0) / 1000
        c_dur = c.get("duration_ms", 0) / 1000
        if b_dur > 0:
            delta_pct = ((c_dur - b_dur) / b_dur) * 100
            delta_str = f"{delta_pct:+.1f}%"
        else:
            delta_str = "N/A"

        color = GREEN if delta_str.startswith("-") else (RED if delta_str.startswith("+") else NC)
        print(
            f"{tid:<{w_task}} {cat:<{w_cat}} "
            f"{b_dur:>{w_base}.1f} {c_dur:>{w_cm}.1f} "
            f"{color}{delta_str:>{w_delta}}{NC}"
        )

    print()


def validate_prerequisites() -> bool:
    """Check that required tools and directories exist."""
    ok = True
    if not shutil.which("claude"):
        print(f"{RED}Error: 'claude' CLI not found. Install Claude Code first.{NC}")
        ok = False
    if not Path(TARGET_CWD).is_dir():
        print(f"{RED}Error: Target codebase not found at {TARGET_CWD}. Clone pydantic first.{NC}")
        ok = False
    if not TASKS_FILE.is_file():
        print(f"{RED}Error: tasks.json not found at {TASKS_FILE}{NC}")
        ok = False
    if not MCP_CONFIG.is_file():
        print(f"{RED}Error: mcp-config.json not found at {MCP_CONFIG}{NC}")
        ok = False
    return ok


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    codemesh_only = "--codemesh-only" in args
    baseline_only = "--baseline-only" in args
    task_filter = [a for a in args if not a.startswith("--")]

    print(f"{BLUE}{BOLD}=== Codemesh Eval Framework ==={NC}")
    print()

    # Load tasks
    tasks = load_tasks(task_filter if task_filter else None)
    if not tasks:
        print(f"{RED}No tasks matched filter: {task_filter}{NC}")
        sys.exit(1)

    print(f"{GREEN}Found {len(tasks)} task(s) to evaluate{NC}")
    for t in tasks:
        print(f"  - {t['id']} ({t['category']})")
    print()

    if dry_run:
        print(f"{YELLOW}=== DRY RUN MODE ==={NC}")
        print()
        for task in tasks:
            for mode in ("baseline", "codemesh"):
                cmd = [
                    "claude", "--print",
                    "--output-format", "json",
                    "--max-budget-usd", "1.00",
                ]
                if mode == "codemesh":
                    cmd.extend(["--mcp-config", str(MCP_CONFIG)])
                    cmd.extend(["--append-system-prompt", CODEMESH_SYSTEM_PROMPT])

                print(f"  {YELLOW}[{mode}]{NC} Task: {BLUE}{task['id']}{NC}")
                print(f"    cwd:    {TARGET_CWD}")
                print(f"    cmd:    {' '.join(cmd)}")
                print(f"    stdin:  {task['prompt'][:80]}...")
                print(f"    output: {RESULTS_DIR}/{mode}/{task['id']}.json")
                print()
        print(f"{GREEN}Dry run complete. No Claude calls were made.{NC}")
        return

    # Validate prerequisites only when actually running
    if not validate_prerequisites():
        sys.exit(1)

    # Create output directories
    (RESULTS_DIR / "baseline").mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "codemesh").mkdir(parents=True, exist_ok=True)

    # Collect results for comparison table
    all_results: dict[str, dict[str, dict]] = {"baseline": {}, "codemesh": {}}

    # Phase 1: Baseline
    if not codemesh_only:
      print(f"{BLUE}--- Phase 1: Baseline (no Codemesh) ---{NC}")
      for task in tasks:
        tid = task["id"]
        cat = task["category"]
        prompt = task["prompt"]
        print(f"  {YELLOW}[baseline]{NC} Running task {BLUE}{tid}{NC} ({cat})...")
        result = run_claude(prompt, "baseline")
        save_result("baseline", tid, result, task)
        if "error" in result:
            print(f"  {RED}[FAIL]{NC} {tid}: {result['error'][:100]}")
        else:
            print(
                f"  {GREEN}[OK]{NC} {tid} — "
                f"turns={result['num_turns']}, "
                f"cost=${result['cost_usd']:.4f}, "
                f"time={result['duration_ms']/1000:.1f}s"
            )
        all_results["baseline"][tid] = {**result, "category": cat}
    print()

    # Phase 2: Codemesh-augmented
    if not baseline_only:
      print(f"{BLUE}--- Phase 2: Codemesh-augmented ---{NC}")
      for task in tasks:
        tid = task["id"]
        cat = task["category"]
        prompt = task["prompt"]
        print(f"  {YELLOW}[codemesh]{NC} Running task {BLUE}{tid}{NC} ({cat})...")
        result = run_claude(prompt, "codemesh")
        save_result("codemesh", tid, result, task)
        if "error" in result:
            print(f"  {RED}[FAIL]{NC} {tid}: {result['error'][:100]}")
        else:
            print(
                f"  {GREEN}[OK]{NC} {tid} — "
                f"turns={result['num_turns']}, "
                f"cost=${result['cost_usd']:.4f}, "
                f"time={result['duration_ms']/1000:.1f}s"
            )
        all_results["codemesh"][tid] = {**result, "category": cat}
    print()

    # Load existing results for tasks we skipped
    for mode in ("baseline", "codemesh"):
        for task in tasks:
            tid = task["id"]
            if tid not in all_results[mode]:
                result_file = RESULTS_DIR / mode / f"{tid}.json"
                if result_file.exists():
                    with open(result_file) as f:
                        data = json.load(f)
                    all_results[mode][tid] = {**data, "category": task["category"]}

    # Phase 3: Comparison
    print(f"{BLUE}--- Phase 3: Comparison ---{NC}")
    print_comparison_table(all_results)
    print(f"{GREEN}{BOLD}=== Eval complete ==={NC}")


if __name__ == "__main__":
    main()
