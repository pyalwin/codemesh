#!/usr/bin/env python3
"""Validate genuine multi-file repair fixtures without indexing or model calls.

Copy the pinned Pydantic package into temporary directories, then run every task
pristine, with both defects, and with each defect independently. A one-file repair
must still fail hidden acceptance. The source checkout is never modified.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "eval/graph_workflow_tasks.json"


def mutation_source(source: str, mutation: dict[str, Any]) -> str:
    old, mutant = mutation["old"], mutation["mutant"]
    occurrence = mutation.get("occurrence", 1)
    if not old or type(occurrence) is not int or occurrence < 1 or source.count(old) < occurrence:
        raise ValueError(f"Invalid or missing mutation anchor: {mutation['path']}")
    parts = source.split(old)
    return old.join(parts[:occurrence]) + mutant + old.join(parts[occurrence:])


def run_tests(repo: Path, python: Path, test: Path) -> dict[str, Any]:
    env = {**os.environ, "PYTHONPATH": str(repo), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    process = subprocess.run(
        [str(python), "-m", "pytest", "-q", "-c", os.devnull, "-p", "no:cacheprovider",
         "--import-mode=importlib", str(test)],
        cwd=repo, env=env, text=True, capture_output=True, timeout=90,
    )
    return {"passed": process.returncode == 0, "exit_code": process.returncode,
            "output": process.stdout + process.stderr}


def validate(base: Path, python: Path, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    base, python, manifest_path = base.resolve(), python.absolute(), manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text())
    actual_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=base, text=True).strip()
    if actual_commit != manifest["commit"]:
        raise ValueError("Fixture checkout does not match the manifest's pinned commit")
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "pydantic"],
                   cwd=base, check=True, capture_output=True)
    source_before = {}
    for task in manifest["tasks"]:
        mutations = task["mutations"]
        if len(mutations) != 2 or len({item["path"] for item in mutations}) != 2:
            raise ValueError("Every graph workflow task must mutate exactly two distinct files")
        for mutation in mutations:
            relative = Path(mutation["path"])
            if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "pydantic":
                raise ValueError("Mutations must target relative Pydantic package paths")
            source_before[relative] = (base / relative).read_bytes()

    rows = []
    with tempfile.TemporaryDirectory(prefix="codemesh-graph-fixture-") as temporary:
        temp = Path(temporary)
        for task in manifest["tasks"]:
            mutations = task["mutations"]
            subsets = [list(subset) for length in range(3)
                       for subset in itertools.combinations(range(2), length)]
            for active in subsets:
                case = "pristine" if not active else "both" if len(active) == 2 else f"mutation_{active[0]}_only"
                repo = temp / task["id"] / case
                shutil.copytree(base / "pydantic", repo / "pydantic",
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                for index in active:
                    mutation = mutations[index]
                    target = repo / mutation["path"]
                    target.write_text(mutation_source(target.read_text(), mutation))
                visible_path = repo / "eval_visible_test.py"
                visible_path.write_text(task["visible_test"])
                visible = run_tests(repo, python, visible_path)
                hidden = run_tests(repo, python, ROOT / task["hidden_test"])
                expected = 0 if not active else 1
                passed = visible["exit_code"] == expected and hidden["exit_code"] == expected
                rows.append({"task": task["id"], "case": case, "active_mutations": active,
                             "expected_exit_code": expected, "passed": passed,
                             "visible": visible, "hidden": hidden})

    unchanged = all((base / relative).read_bytes() == source for relative, source in source_before.items())
    return {"kind": "multi-file public API fixture validation", "manifest": str(manifest_path),
            "repository": manifest["repository"], "commit": actual_commit, "model_calls": 0,
            "base_checkout_unchanged": unchanged, "passed": unchanged and all(row["passed"] for row in rows),
            "tasks": len(manifest["tasks"]), "cases": len(rows), "test_runs": len(rows) * 2,
            "limitation": "Seeded defect correctness only; no model quality, graph use, token, or cost claim.",
            "results": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=Path("/tmp/codemesh-pydantic-benchmark"))
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, help="Optional JSON audit output")
    args = parser.parse_args()
    report = validate(args.base, args.python, args.manifest)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    if not report["passed"]:
        for row in report["results"]:
            if not row["passed"]:
                print(json.dumps(row, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
