#!/usr/bin/env python3
"""Report recorded selector+reasoner pilot outcomes, with no model calls."""
import argparse
from collections import Counter
import json
from pathlib import Path

from selected_context_eval import POLICIES, totals


def generate(root: Path):
    manifest = json.loads((root / "manifest.json").read_text())
    rows = [json.loads(path.read_text()) for path in sorted(root.glob("*/*/result.json"))]
    groups = {}
    for policy in POLICIES:
        runs = [row for row in rows if row["policy"] == policy]
        calls = []
        for row in runs:
            path = root / policy / row["task"] / "calls.jsonl"
            calls.extend(json.loads(line) for line in path.read_text().splitlines())
        groups[policy] = {"runs": len(runs), "first_patch_passes": sum(row["first_patch_pass"] is True for row in runs),
                          "final_patch_passes": sum(row["final_patch_pass"] is True for row in runs),
                          "statuses": dict(Counter(row["status"] for row in runs)), "totals": totals(calls),
                          "by_role": {role: totals([call for call in calls if call["role"] == role])
                                      for role in ("selector", "reasoner")},
                          "selection_applied": sum(row["selection_applied"] for row in runs),
                          "selection_rejected": sum(row["selection_rejected"] for row in runs),
                          "navigation_handles_archived": sum(row["navigation_handles_archived"] for row in runs),
                          "selector_fallbacks": sum(row["selector_fallbacks"] for row in runs)}
    report = {"manifest": manifest, "completed": len(rows), "planned": len(manifest["plan"]),
              "groups": groups, "runs": rows}
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    def number(value):
        return "unknown" if value is None else f"{value:,}"
    text = ["# Selective context and delegated retrieval pilot", "",
            f"Completed **{len(rows)}/{len(manifest['plan'])}** planned runs. One repeat per task/condition.", "",
            "## Design", "",
            "All conditions use the same pinned public Pydantic code, seeded regressions, initial real root map, "
            "visible tests and hidden acceptance tests. CodeMesh is accessed through its public CLI. "
            "Full and working-set use requested gpt-5.6-terra for all model decisions. Selected uses requested "
            "gpt-6-luna for bounded read/navigation choices and navigation retention; gpt-5.6-terra reasons and edits. "
            "All use low reasoning effort. This CLI experiment does **not** invoke the Decisions API or inherit its prices/latency.", "",
            "The selector chooses exact harness-generated tool arguments, estimates keep/archive/defer relevance, "
            "and may escalate. A confidence threshold is an uncalibrated policy heuristic. The harness protects "
            "task/source/error/edit/test state, validates selections, and archives every observation. "
            "Luna gets source descriptors; Terra gets original code. Source remains under the existing 16KiB "
            "budget; this pilot does not ask the selector to discard source blocks.", "",
            "Single-session protocol, shared 20-call limit (including selector calls), randomized run order. "
            "No sampling seed or cross-session reset. These outcomes must not be pooled with the previous "
            "three-phase Luna-only matrix. The selected condition changes both routing and context; it is not "
            "an isolated causal estimate of compaction.", "", "## Actual combined usage", "",
            "Cached input is a subset of input. Missing cost is not zero. Failed graded runs remain included.", "",
            "| Condition | First/final passes | Calls | Input | Output | Cached input | Model seconds |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for policy, group in groups.items():
        t = group["totals"]
        seconds = "unknown" if t["elapsed_ms"] is None else f"{t['elapsed_ms']/1000:.1f}"
        text.append(f"| {policy} | {group['first_patch_passes']}/{group['runs']}; {group['final_patch_passes']}/{group['runs']} "
                    f"| {t['calls']} | {number(t['input_tokens'])} | {number(t['output_tokens'])} "
                    f"| {number(t['cache_read_input_tokens'])} | {seconds} |")
    text += ["", "## Selected condition by role", "",
             "| Role | Calls | Input | Output | Cached input |", "|---|---:|---:|---:|---:|"]
    for role, t in groups["selected"]["by_role"].items():
        text.append(f"| {role} | {t['calls']} | {number(t['input_tokens'])} | {number(t['output_tokens'])} | {number(t['cache_read_input_tokens'])} |")
    selected = groups["selected"]
    text += ["", "## Did selective archival actually occur?", "",
             f"- Applied selection transactions: {selected['selection_applied']}.",
             f"- Rejected selection transactions: {selected['selection_rejected']}.",
             f"- Archive events reported by applied transactions: {selected['navigation_handles_archived']} (includes repeated decisions for the same handle; not a unique-handle count).",
             f"- Selector validation/uncertainty fallbacks: {selected['selector_fallbacks']}.",
             "- A run with no applied archival does not demonstrate learned compaction, even if its final prompt is smaller.",
             "", "## Per-task results", "",
             "| Task | Condition | Status | First/final pass | Input | Calls |", "|---|---|---|---|---:|---:|"]
    for row in rows:
        text.append(f"| {row['task']} | {row['policy']} | {row['status']} | {row['first_patch_pass']}/{row['final_patch_pass']} "
                    f"| {number(row['totals']['input_tokens'])} | {row['totals']['calls']} |")
    text += ["", "## Interpretation boundaries", "",
             "Three small seeded repairs and one repeat per condition provide pilot observations only. "
             "Hidden-test acceptance is narrower than general repair correctness. A passing final patch can "
             "still have call_limit status if the agent did not finish its workflow; the per-task status is "
             "reported separately from patch acceptance. Both selector and reasoner "
             "usage is included. The CLI does not report billed USD; no dollar saving is established. "
             "Model names are requested identities; the stream does not independently attest resolved model identity. "
             "Server cache behavior and model sampling can vary across runs.", "",
             "A separate synthetic branch-switch fixture verifies archival/recovery and source residency. "
             "Its full exact prompts are in the sibling branch-compaction-2026-10-19/offline directory. "
             "That small fixture did not show an overall byte advantage: metadata overhead can outweigh deleted map details.",
             "", "## Evidence", "",
             "Each condition/task directory contains calls.jsonl, actions.jsonl, result.json, exact inputs/, "
             "and selection audit/archive where applicable. patch.diff is against pristine upstream; a correct "
             "repair restoring pristine can yield an empty diff. Any repair.diff is explicitly reconstructed "
             "from the seeded source and successful edit actions. The separate Terra preflight is operational "
             "overhead excluded from the experimental cells.", ""]
    (root / "REPORT.md").write_text("\n".join(text))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    report = generate(args.root)
    print(f"Reported {report['completed']}/{report['planned']} runs")
