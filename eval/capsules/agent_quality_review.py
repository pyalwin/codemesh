#!/usr/bin/env python3
"""Attach hash-pinned manual answer and handoff-memory rubrics to agent runs."""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["mode"]].append(row)
    result = {}
    for mode, items in groups.items():
        phases = [phase for row in items for phase in row["phases"]]
        finals = [row["phases"][-1] for row in items if len(row["phases"]) == 3]
        result[mode] = {
            "trajectories": len(items),
            "all_phase_answers_acceptable": sum(all(p["quality"]["acceptable"] for p in row["phases"]) for row in items),
            "phase_answers_acceptable": sum(p["quality"]["acceptable"] for p in phases),
            "phase_answers_total": len(phases),
            "final_answers_acceptable": sum(p["quality"]["acceptable"] for p in finals),
            "final_answers_total": len(finals),
            "final_core_conclusions_correct": sum(p["quality"].get("core_conclusion_correct", False) for p in finals),
            "final_rubric_facts_met": sum(sum(p["quality"]["rubric_hits"]) for p in finals),
            "final_rubric_facts_total": sum(len(p["quality"]["rubric_hits"]) for p in finals),
            "memories_acceptable": sum(row["memory_quality"]["acceptable"] for row in items),
            "memory_facts_met": sum(sum(row["memory_quality"]["rubric_hits"]) for row in items),
            "memory_facts_total": sum(len(row["memory_quality"]["rubric_hits"]) for row in items),
            "false_answer_claims": sum(p["quality"]["false_claim"] for p in phases),
            "false_memory_claims": sum(row["memory_quality"]["false_claim"] for row in items),
            "phase_answers_with_unresolved_evidence_ids": sum(
                bool(re.search(r"(?<![A-Za-z])E\d+(?:-\d+)?", phase["answer"] or ""))
                for phase in phases
            ),
        }
    return result


def attach(report: dict[str, Any], labels: dict[str, Any]) -> dict[str, Any]:
    workflows = {workflow["id"]: workflow for workflow in report["workflows"]}
    answer_labels = {(x["workflow"], x["mode"], x["repeat"], x["phase"]): x for x in labels["answers"]}
    memory_labels = {(x["workflow"], x["mode"], x["repeat"]): x for x in labels["memories"]}
    seen_answers = set()
    seen_memories = set()
    for row in report["results"]:
        key = row["workflow"], row["mode"], row["repeat"]
        workflow = workflows[row["workflow"]]
        if key not in memory_labels:
            raise ValueError(f"Missing memory label for {key}")
        memory = memory_labels[key]
        seen_memories.add(key)
        if digest(row["compaction_memory"]) != memory["memory_sha256"]:
            raise ValueError(f"Changed memory for {key}")
        if len(memory["rubric_hits"]) != len(workflow["memory_rubric"]):
            raise ValueError(f"Memory rubric length mismatch for {key}")
        row["memory_quality"] = {
            "rubric_hits": memory["rubric_hits"], "false_claim": memory.get("false_claim", False),
            "acceptable": all(memory["rubric_hits"]) and not memory.get("false_claim", False),
            "note": memory.get("note", ""), "reviewed_sha256": memory["memory_sha256"],
        }
        phases = {p["id"]: p for p in workflow["phases"]}
        for phase in row["phases"]:
            phase_key = (*key, phase["id"])
            if phase_key not in answer_labels:
                raise ValueError(f"Missing answer label for {phase_key}")
            label = answer_labels[phase_key]
            seen_answers.add(phase_key)
            if digest(phase["answer"] or "") != label["answer_sha256"]:
                raise ValueError(f"Changed answer for {phase_key}")
            if len(label["rubric_hits"]) != len(phases[phase["id"]]["rubric"]):
                raise ValueError(f"Answer rubric length mismatch for {phase_key}")
            if phase["id"].startswith("post_compaction_") and not isinstance(label.get("core_conclusion_correct"), bool):
                raise ValueError(f"Missing core-conclusion label for {phase_key}")
            phase["quality"] = {
                "rubric_hits": label["rubric_hits"], "false_claim": label.get("false_claim", False),
                "acceptable": all(label["rubric_hits"]) and not label.get("false_claim", False),
                "core_conclusion_correct": label.get("core_conclusion_correct") if phase["id"].startswith("post_compaction_") else None,
                "note": label.get("note", ""), "reviewed_sha256": label["answer_sha256"],
            }
    if seen_answers != set(answer_labels) or seen_memories != set(memory_labels):
        raise ValueError("Labels include answers or memories absent from reports")
    report["quality_method"] = (
        "One-reviewer, unblinded manual audit against frozen workflow and memory rubrics. "
        "A phase is acceptable only when every rubric fact is present and no false behavioral claim was identified. "
        "Citation precision is not scored. This is exploratory, not an independent holdout."
    )
    report["quality_summary"] = summary(report["results"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    labels = json.loads(args.labels.read_text())
    attach(report, labels)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    result = {
        "method": report["quality_method"],
        "source_report": str(args.report),
        "by_mode": report["quality_summary"],
        "usage": report["by_mode"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    lines = ["# Dependent-agent quality audit", "", result["method"], "",
             "| Mode | Core conclusion | Final acceptable | Final facts | Memory acceptable | Memory facts | Input tokens | Cached reads | Cost |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for mode, quality in result["by_mode"].items():
        usage = result["usage"][mode]
        lines.append(
            f"| {mode} | {quality['final_core_conclusions_correct']}/{quality['final_answers_total']} | "
            f"{quality['final_answers_acceptable']}/{quality['final_answers_total']} | "
            f"{quality['final_rubric_facts_met']}/{quality['final_rubric_facts_total']} | "
            f"{quality['memories_acceptable']}/{quality['trajectories']} | "
            f"{quality['memory_facts_met']}/{quality['memory_facts_total']} | "
            f"{usage['input_tokens']:,} | {usage['cache_read_input_tokens']:,} | ${usage['cost_usd']:.6f} |"
        )
    args.out.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
