#!/usr/bin/env python3
"""
LLM-as-judge scorer for Codemesh eval results.
Scores both baseline and codemesh responses against task rubrics using Claude Haiku.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import subprocess

SCRIPT_DIR = Path(__file__).parent
TASKS_FILE = SCRIPT_DIR / "tasks.json"
RESULTS_DIR = SCRIPT_DIR / "results"

# Colors
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
NC = "\033[0m"

JUDGE_SYSTEM_PROMPT = """You are an expert code reviewer evaluating AI assistant responses about the pydantic codebase.

Score the response on these dimensions (1-10 each):

1. **Completeness** — Did it find all the key files and components mentioned in the rubric?
2. **Accuracy** — Is the information factually correct? Are file paths, class names, and function descriptions accurate?
3. **Depth** — Does it explain HOW things work, not just list them? Does it show understanding of the code?
4. **Relevance** — Does it answer what was asked without excessive tangents?
5. **Actionability** — Could a developer use this response to navigate the codebase effectively?

You MUST respond with ONLY valid JSON in this exact format:
{
  "completeness": <1-10>,
  "accuracy": <1-10>,
  "depth": <1-10>,
  "relevance": <1-10>,
  "actionability": <1-10>,
  "overall": <1-10>,
  "notes": "<one sentence explaining the score>"
}"""


def score_response(
    task: dict,
    response_text: str,
    mode: str,
) -> dict:
    """Score a single response using claude --print as judge."""
    user_prompt = f"""## Task
**Question:** {task['prompt']}

**Expected files to find:** {', '.join(task['expected_files'])}

**Rubric:** {task['rubric']}

## Response to evaluate ({mode})

{response_text}

---

Score this response. Respond with ONLY the JSON object, no other text."""

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    try:
        result = subprocess.run(
            [
                "claude", "--print",
                "--output-format", "json",
                "--model", "haiku",
                "--append-system-prompt", JUDGE_SYSTEM_PROMPT,
            ],
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )

        if result.returncode != 0:
            return {
                "completeness": 0, "accuracy": 0, "depth": 0,
                "relevance": 0, "actionability": 0, "overall": 0,
                "notes": f"Judge error: {result.stderr[:100]}",
            }

        parsed = json.loads(result.stdout.strip())
        text = parsed.get("result", "").strip()
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {
            "completeness": 0, "accuracy": 0, "depth": 0,
            "relevance": 0, "actionability": 0, "overall": 0,
            "notes": f"Judge error: {e}",
        }

    # Handle potential markdown wrapping
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "completeness": 0, "accuracy": 0, "depth": 0,
            "relevance": 0, "actionability": 0, "overall": 0,
            "notes": f"Judge parse error: {text[:100]}",
        }


def main() -> None:
    with open(TASKS_FILE) as f:
        tasks = json.load(f)["tasks"]

    task_filter = [a for a in sys.argv[1:] if not a.startswith("--")]

    if task_filter:
        tasks = [t for t in tasks if t["id"] in task_filter]

    if not tasks:
        print(f"{RED}No tasks matched filter{NC}")
        sys.exit(1)

    print(f"{BLUE}{BOLD}=== Codemesh Quality Evaluation ==={NC}")
    print()

    all_scores: dict[str, dict[str, dict]] = {"baseline": {}, "codemesh": {}}

    for task in tasks:
        tid = task["id"]
        cat = task["category"]
        print(f"{YELLOW}Scoring {tid} ({cat})...{NC}")

        for mode in ("baseline", "codemesh"):
            result_file = RESULTS_DIR / mode / f"{tid}.json"
            if not result_file.exists():
                print(f"  {RED}Missing {mode}/{tid}.json{NC}")
                continue

            with open(result_file) as f:
                data = json.load(f)

            response_text = data.get("result", "")
            if not response_text:
                print(f"  {RED}Empty response for {mode}/{tid}{NC}")
                continue

            scores = score_response(task, response_text, mode)
            all_scores[mode][tid] = {**scores, "category": cat}

            cost = data.get("cost_usd", 0)
            duration = data.get("duration_ms", 0) / 1000
            print(
                f"  {mode:10s} overall={scores['overall']:2d}/10  "
                f"cost=${cost:.4f}  time={duration:.0f}s  "
                f"— {scores.get('notes', '')}"
            )

    print()

    # Print comparison table
    print(f"{BOLD}{BLUE}=== Quality + Cost + Time Comparison ==={NC}")
    print()

    header = (
        f"{'Task':<20s} {'Category':<15s} "
        f"{'BL Score':>8s} {'CM Score':>8s} {'Δ Quality':>10s} "
        f"{'BL Cost':>9s} {'CM Cost':>9s} {'Δ Cost':>9s} "
        f"{'BL Time':>8s} {'CM Time':>8s} {'Δ Time':>9s}"
    )
    print(header)
    print("=" * len(header))

    total_bl_score = 0
    total_cm_score = 0
    total_bl_cost = 0.0
    total_cm_cost = 0.0
    total_bl_time = 0.0
    total_cm_time = 0.0
    count = 0

    for task in tasks:
        tid = task["id"]
        cat = task["category"]

        bl = all_scores["baseline"].get(tid)
        cm = all_scores["codemesh"].get(tid)
        if not bl or not cm:
            continue

        bl_file = RESULTS_DIR / "baseline" / f"{tid}.json"
        cm_file = RESULTS_DIR / "codemesh" / f"{tid}.json"
        with open(bl_file) as f:
            bl_data = json.load(f)
        with open(cm_file) as f:
            cm_data = json.load(f)

        bl_score = bl["overall"]
        cm_score = cm["overall"]
        bl_cost = bl_data.get("cost_usd", 0)
        cm_cost = cm_data.get("cost_usd", 0)
        bl_time = bl_data.get("duration_ms", 0) / 1000
        cm_time = cm_data.get("duration_ms", 0) / 1000

        score_delta = cm_score - bl_score
        cost_delta = ((cm_cost - bl_cost) / bl_cost * 100) if bl_cost > 0 else 0
        time_delta = ((cm_time - bl_time) / bl_time * 100) if bl_time > 0 else 0

        score_color = GREEN if score_delta >= 0 else RED
        cost_color = GREEN if cost_delta <= 0 else RED
        time_color = GREEN if time_delta <= 0 else RED

        score_sign = "+" if score_delta >= 0 else ""
        cost_sign = "+" if cost_delta >= 0 else ""
        time_sign = "+" if time_delta >= 0 else ""

        print(
            f"{tid:<20s} {cat:<15s} "
            f"{bl_score:>5d}/10 {cm_score:>5d}/10 "
            f"{score_color}{score_sign}{score_delta:>+8d}{NC} "
            f"${bl_cost:>8.4f} ${cm_cost:>8.4f} "
            f"{cost_color}{cost_sign}{cost_delta:>7.1f}%{NC} "
            f"{bl_time:>7.0f}s {cm_time:>7.0f}s "
            f"{time_color}{time_sign}{time_delta:>7.1f}%{NC}"
        )

        total_bl_score += bl_score
        total_cm_score += cm_score
        total_bl_cost += bl_cost
        total_cm_cost += cm_cost
        total_bl_time += bl_time
        total_cm_time += cm_time
        count += 1

    if count > 0:
        print("=" * len(header))
        avg_bl = total_bl_score / count
        avg_cm = total_cm_score / count
        avg_delta = avg_cm - avg_bl
        cost_pct = ((total_cm_cost - total_bl_cost) / total_bl_cost * 100)
        time_pct = ((total_cm_time - total_bl_time) / total_bl_time * 100)

        score_color = GREEN if avg_delta >= 0 else RED
        cost_color = GREEN if cost_pct <= 0 else RED
        time_color = GREEN if time_pct <= 0 else RED

        print(
            f"{'AVERAGE':<20s} {'':15s} "
            f"{avg_bl:>6.1f}/10 {avg_cm:>5.1f}/10 "
            f"{score_color}{avg_delta:>+8.1f}{NC} "
            f"${total_bl_cost:>8.4f} ${total_cm_cost:>8.4f} "
            f"{cost_color}{cost_pct:>+7.1f}%{NC} "
            f"{total_bl_time:>7.0f}s {total_cm_time:>7.0f}s "
            f"{time_color}{time_pct:>+7.1f}%{NC}"
        )

    # Detailed breakdown
    print()
    print(f"{BOLD}{BLUE}=== Per-Dimension Breakdown ==={NC}")
    print()

    dims = ["completeness", "accuracy", "depth", "relevance", "actionability"]
    for task in tasks:
        tid = task["id"]
        bl = all_scores["baseline"].get(tid)
        cm = all_scores["codemesh"].get(tid)
        if not bl or not cm:
            continue

        print(f"{BOLD}{tid}{NC} ({task['category']})")
        for dim in dims:
            bl_val = bl.get(dim, 0)
            cm_val = cm.get(dim, 0)
            delta = cm_val - bl_val
            color = GREEN if delta > 0 else (RED if delta < 0 else NC)
            sign = "+" if delta > 0 else ""
            print(f"  {dim:<16s}  baseline={bl_val:2d}  codemesh={cm_val:2d}  {color}{sign}{delta}{NC}")
        print(f"  {'notes':<16s}  BL: {bl.get('notes', '')}")
        print(f"  {'':16s}  CM: {cm.get('notes', '')}")
        print()

    # Save scores
    scores_file = RESULTS_DIR / "quality_scores.json"
    with open(scores_file, "w") as f:
        json.dump(all_scores, f, indent=2)
    print(f"Scores saved to {scores_file}")


if __name__ == "__main__":
    main()
