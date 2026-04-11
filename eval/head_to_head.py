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

CODEMESH_PROMPT = """You MUST use codemesh_* MCP tools for global discovery. Grep and Glob are disabled. LSP is available for exact navigation.

TWO-TIER NAVIGATION:

TIER 1 — CODEMESH (global discovery, "where is everything?"):
- codemesh_explore(action='search') — find files and symbols across the whole codebase
- codemesh_explore(action='context') — get symbol metadata: signatures, call chains, imports, concepts
- codemesh_trace — follow a call chain through the graph

TIER 2 — LSP (exact navigation, "what exactly is this?"):
- Use LSP for go-to-definition when you know a symbol but need its exact location
- Use LSP for find-references to see all callers of a specific function
- LSP resolves type ambiguity that the graph can't — if codemesh returns 5 "request" methods, LSP tells you which one is actually called

WORKFLOW: codemesh_explore to find the area → LSP to navigate precisely within it → Read only the specific lines you need.
Every response includes projectRoot for absolute file paths.

MANDATORY WORKFLOW:

STEP 1 — DECOMPOSE: Before any tool calls, break the question into sub-topics. Write them out as a numbered checklist. This checklist is your contract — you are NOT done until every item is covered.

STEP 2 — EXPLORE:
- For TRACE questions: search for the entry point, then codemesh_trace to follow the call chain.
- For COMPREHENSION questions: search for EACH sub-topic SEPARATELY.

STEP 2.5 — LSP NAVIGATE (use this BEFORE Read):
When codemesh gives you a symbol name, use LSP to resolve it precisely:
- LSP go-to-definition → exact file and line (replaces guessing + Read)
- LSP find-references → all callers across the codebase
- This is FASTER and MORE PRECISE than reading files to search for symbols. Use it.

STEP 3 — VERIFY & ENRICH: Go through your decomposition checklist. Is every sub-topic covered? If ANY item is unchecked, go back to Step 2. Then ENRICH the graph — for each key file you explored, call codemesh_enrich with a summary of what you learned. This is MANDATORY, not optional. It makes future sessions faster.

STEP 4 — WRITE: Structure your final answer with these sections:
1. Overview (2-3 sentences)
2. One section PER sub-topic from your decomposition, with file names and key functions
3. File reference table listing EVERY file involved and its role
Your answer must be COMPLETE — cover every sub-topic fully. Do not abbreviate or truncate."""

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

CODEMESH_CLI_PROMPT = """You have a CLI tool called 'codemesh' for global codebase discovery. Use it via Bash. Grep and Glob are disabled. LSP is available for exact navigation.

TWO-TIER NAVIGATION:

TIER 1 — CODEMESH CLI (global discovery via Bash):
- `codemesh explore search "query"` — find files and symbols across the codebase
- `codemesh explore context path/to/file.swift` — get symbol metadata, call chains, imports
- `codemesh explore context path/to/file.swift --symbol name` — focus on a specific symbol
- `codemesh explore trace symbolName --depth 5` — follow a call chain through the graph
- `codemesh explore impact path/to/file.swift` — reverse dependency analysis

TIER 2 — LSP (exact navigation):
- Use LSP for go-to-definition when codemesh gives you a symbol but you need its exact location
- Use LSP for find-references to see all callers
- LSP resolves ambiguity the graph can't

WORKFLOW: codemesh CLI to find the area → LSP to navigate precisely → Read only the specific lines you need.

MANDATORY WORKFLOW:

STEP 1 — DECOMPOSE: Break the question into sub-topics as a numbered checklist.

STEP 2 — EXPLORE: Use codemesh CLI via Bash for each sub-topic.
- For TRACE questions: `codemesh explore trace symbolName --depth 5`
- For COMPREHENSION: search for EACH sub-topic separately, then get context on key files.

STEP 2.5 — LSP NAVIGATE: When codemesh gives you a symbol name, use LSP BEFORE Read:
- LSP go-to-definition → exact file and line (faster than Read + search)
- LSP find-references → all callers across the codebase
Use LSP to resolve each key symbol, then Read only the specific lines you need.

STEP 3 — VERIFY: Check your checklist. If gaps, explore more.

STEP 4 — WRITE: Complete answer with one section per sub-topic + file reference table."""

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

EVAL_MODEL = "opus"  # overridden by --model flag

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
        "--model", EVAL_MODEL,
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
            cmd.extend(["--allowedTools", "LSP"])

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    # CLI mode needs CODEMESH_PROJECT_ROOT so Bash calls to `codemesh` work
    if mode == "codemesh-cli":
        env["CODEMESH_PROJECT_ROOT"] = cwd
        # Ensure codemesh CLI is on PATH
        env["PATH"] = str(PROJECT_DIR / "node_modules" / ".bin") + ":" + env.get("PATH", "")

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
    {
        "id": "vscode",
        "repo": "https://github.com/microsoft/vscode.git",
        "local_path": "/tmp/vscode",
        "query": "How does the extension host communicate with the main process?",
        "rubric": "Should identify the extension host process architecture, IPC mechanism (MessagePort/RPC), the ExtensionHostMain entry point, and the protocol layer. Key files in src/vs/workbench/services/extensions/.",
    },
    {
        "id": "swift-compiler",
        "repo": "https://github.com/swiftlang/swift.git",
        "local_path": "/tmp/swift",
        "query": "How does the Swift compiler handle error diagnostics?",
        "rubric": "Should identify the diagnostics engine, DiagnosticEngine class, diagnostic message emission pipeline, and how diagnostics flow from parsing/type-checking to user-visible error messages. Key files in lib/AST/ and include/swift/AST/.",
    },
]


# ── Main ────────────────────────────────────────────────────────────

def main() -> None:
    global EVAL_MODEL, RESULTS_DIR

    args = sys.argv[1:]
    skip_judge = "--skip-judge" in args

    # Extract --model value
    for i, a in enumerate(args):
        if a == "--model" and i + 1 < len(args):
            EVAL_MODEL = args[i + 1]

    filter_ids = [a for a in args if not a.startswith("--") and a not in ("opus", "sonnet", "haiku")]

    # Model-specific results directory
    if EVAL_MODEL != "opus":
        RESULTS_DIR = SCRIPT_DIR / "results" / f"head_to_head_{EVAL_MODEL}"

    benchmarks = BENCHMARKS
    if filter_ids:
        benchmarks = [b for b in benchmarks if b["id"] in filter_ids]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"{BLUE}{BOLD}=== Head-to-Head: Codemesh vs CodeGraph vs Baseline ==={NC}")
    print(f"  Model: {BOLD}{EVAL_MODEL}{NC}")
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
            "codemesh-cli": {"mcp": None, "prompt": CODEMESH_CLI_PROMPT, "tools": None},
            "codegraph": {"mcp": cg_config, "prompt": CODEGRAPH_PROMPT, "tools": CODEGRAPH_TOOLS},
        }

        results: Dict[str, Dict] = {}

        # Run all 4 modes in parallel
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

        with ThreadPoolExecutor(max_workers=4) as executor:
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
                ("codemesh-cli", "baseline"),
                ("codegraph", "baseline"),
                ("codemesh-cli", "codemesh"),
                ("codemesh-cli", "codegraph"),
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
        for mode in ["baseline", "codemesh", "codemesh-cli", "codegraph"]:
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
