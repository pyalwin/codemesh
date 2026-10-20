#!/usr/bin/env python3
"""Attach a frozen, manually reviewed rubric audit to benchmark reports.

Labels are tied to answer SHA-256 hashes. A changed or new answer cannot inherit
an old quality label; it must be reviewed and added to the label file first.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


def answer_hash(answer: str) -> str:
    return sha256(answer.encode("utf-8")).hexdigest()


def row_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return row["task"], row["mode"], row["repeat"]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["mode"]].append(row)
    return {
        mode: {
            "answers": len(items),
            "rubric_facts_met": sum(sum(row["quality"]["rubric_hits"]) for row in items),
            "rubric_facts_total": sum(len(row["quality"]["rubric_hits"]) for row in items),
            "rubric_complete_answers": sum(row["quality"]["rubric_complete"] for row in items),
            "answers_with_false_claim": sum(row["quality"]["false_claim"] for row in items),
            "acceptable_answers": sum(row["quality"]["acceptable"] for row in items),
        }
        for mode, items in groups.items()
    }


def attach_quality(report: dict[str, Any], labels: dict[tuple[str, str, int], dict[str, Any]]) -> dict[str, Any]:
    tasks = {task["id"]: task for task in report["tasks"]}
    for row in report["results"]:
        key = row_key(row)
        if key not in labels:
            raise ValueError(f"No manual quality label for {key}")
        label = labels[key]
        answer = row["model_result"].get("answer") or ""
        if answer_hash(answer) != label["answer_sha256"]:
            raise ValueError(f"Answer changed since manual review: {key}")
        hits = label["rubric_hits"]
        if len(hits) != len(tasks[row["task"]]["rubric"]) or not all(type(hit) is bool for hit in hits):
            raise ValueError(f"Rubric label count/type mismatch: {key}")
        false_claim = label.get("false_claim", False)
        row["quality"] = {
            "rubric_hits": hits,
            "rubric_complete": all(hits),
            "false_claim": false_claim,
            "acceptable": all(hits) and not false_claim,
            "note": label.get("note", ""),
            "reviewed_answer_sha256": label["answer_sha256"],
        }
    report["quality_method"] = (
        "One-reviewer manual audit against the prespecified task rubric. "
        "rubric_hits align by index with each task's rubric. Exact argument bindings "
        "are required where a rubric spells them out. Acceptable means every rubric "
        "fact is present and no identified false behavioral claim. Source-line citation "
        "precision and writing style were not scored. This is not a blind holdout."
    )
    report["quality_summary"] = summarize(report["results"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True, help="Combined quality summary JSON")
    args = parser.parse_args()
    data = json.loads(args.labels.read_text(encoding="utf-8"))
    labels = {row_key(row): row for row in data["rows"]}
    if len(labels) != len(data["rows"]):
        raise ValueError("Duplicate manual quality label")
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.reports]
    keys = [row_key(row) for report in reports for row in report["results"]]
    if len(keys) != len(set(keys)) or set(keys) != set(labels):
        raise ValueError("Reports and manual quality labels must have the same unique answers")
    all_rows = []
    for path, report in zip(args.reports, reports):
        attach_quality(report, labels)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        all_rows.extend(report["results"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    combined = {
        "method": reports[0]["quality_method"],
        "source_reports": [str(path) for path in args.reports],
        "by_mode": summarize(all_rows),
        "by_category": {
            category: summarize([row for row in all_rows if row["category"] == category])
            for category in sorted({row["category"] for row in all_rows})
        },
    }
    args.out.write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")
    usage = {mode: report["totals"][mode] for report in reports for mode in report["totals"]}
    markdown = [
        "# Manual answer-quality audit", "",
        f"One reviewer scored the {len(all_rows)} saved answers against the prespecified rubrics. "
        "A response is acceptable when it includes every rubric fact and has no identified false behavioral claim. "
        "Exact argument bindings count where the rubric spells them out. Citation precision and writing style were not graded.",
        "", "| Context | Acceptable answers | Rubric facts met | Input tokens | Answer cost |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for mode, quality in combined["by_mode"].items():
        costs = usage[mode]
        markdown.append(
            f"| {mode} | {quality['acceptable_answers']}/{quality['answers']} | "
            f"{quality['rubric_facts_met']}/{quality['rubric_facts_total']} | "
            f"{costs['actual_input_tokens']:,} | ${costs['model_cost_usd']:.6f} |"
        )
    modes = list(combined["by_mode"])
    markdown += [
        "", "## By question group", "",
        "| Group | " + " | ".join(modes) + " |",
        "| --- | " + " | ".join("---:" for _ in modes) + " |",
    ]
    for category, modes in combined["by_category"].items():
        values = [f"{modes[mode]['acceptable_answers']}/{modes[mode]['answers']}" for mode in combined["by_mode"]]
        markdown.append(f"| {category} | {' | '.join(values)} |")
    markdown += [
        "", "This audit is unblinded and covers one repository revision and one model. "
        "The questions were used to refine v2. See the JSON report and answer-level notes for each judgment.", "",
    ]
    args.out.with_suffix(".md").write_text("\n".join(markdown), encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
