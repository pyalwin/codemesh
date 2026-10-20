#!/usr/bin/env python3
"""No-model CLI contract for the opt-in exact-name path-depth tie-break.

Index a disposable ordinary code fixture through the public CLI, then assign
controlled PageRank values in its SQLite index so the two policies must disagree.
This tests adapter wiring and ordering; it does not estimate retrieval quality.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from vnext_contract import ROOT, cli, command


def run(out: Path) -> dict:
    calls = []
    with tempfile.TemporaryDirectory(prefix="codemesh-next-contract-") as directory:
        repo = Path(directory)
        shallow_path, deep_path = "pkg/entry.py", "pkg/compat/entry.py"
        shallow_id = f"symbol:{shallow_path}:validate"
        deep_id = f"symbol:{deep_path}:validate"
        for file_path in (shallow_path, deep_path):
            target = repo / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def validate(value):\n    return bool(value)\n")

        def invoke(*args: str) -> dict:
            result, elapsed = cli(repo, *args)
            calls.append({"arguments": args, "elapsed_ms": elapsed, "output": result})
            return result

        invoke("index", "--json")
        with sqlite3.connect(repo / ".codemesh/codemesh.db") as db:
            for symbol_id, score in ((shallow_id, 0.01), (deep_id, 0.9)):
                updated = db.execute(
                    "UPDATE nodes SET data=json_set(data, '$.pagerankScore', ?) "
                    "WHERE id=? AND type='symbol'", (score, symbol_id))
                assert updated.rowcount == 1, f"CLI index did not produce {symbol_id}"
            # No search occurred before this fixture-only adjustment; make the
            # invariant explicit in case indexing begins warming search later.
            db.execute("DELETE FROM retrieval_cache")

        prefix = ("explore", "search", "validate", "--scope", "symbols", "--compact")
        default = invoke(*prefix)
        explicit_default = invoke(*prefix, "--exact-tie-break", "pagerank")
        shallow_first = invoke(*prefix, "--exact-tie-break", "path-depth")
        default_again = invoke(*prefix)
        scoped = invoke(*prefix, "--exact-tie-break", "path-depth", "--path", "pkg/compat")
        explicit_id = invoke("explore", "search", deep_id, "--scope", "symbols", "--compact",
                             "--exact-tie-break", "path-depth")
        ids = lambda result: [item["id"] for item in result["results"]]
        assert ids(default) == [deep_id, shallow_id]
        assert explicit_default == default == default_again
        assert ids(shallow_first) == [shallow_id, deep_id]
        assert all(item["why"] == "exact" for item in shallow_first["results"])
        assert ids(scoped) == [deep_id]
        assert ids(explicit_id)[0] == deep_id

        invalid_args = (*prefix, "--exact-tie-break", "canonical")
        started = time.monotonic_ns()
        invalid = command(["node", str(ROOT / "dist/cli.js"), *invalid_args], repo)
        calls.append({"arguments": invalid_args,
                      "elapsed_ms": (time.monotonic_ns() - started) // 1_000_000,
                      "returncode": invalid.returncode, "stdout": invalid.stdout, "stderr": invalid.stderr})
        assert invalid.returncode != 0
        assert "--exact-tie-break must be pagerank or path-depth" in invalid.stderr

        report = {
            "kind": "public CLI correctness contract",
            "model_calls": 0,
            "passed": True,
            "checks": ["default and explicit pagerank prefer the popular deeper exact symbol",
                       "path-depth reverses the tied exact ordering",
                       "cached candidate reuse preserves each requested policy",
                       "explicit deeper path and symbol ID remain selectable",
                       "invalid tie-break value is rejected"],
            "fixture": {"shallow": {"id": shallow_id, "pagerank": 0.01},
                        "deep": {"id": deep_id, "pagerank": 0.9},
                        "ranking_setup": "PageRank values assigned directly after real CLI indexing"},
            "built_artifact_sha256": {
                name: sha256((ROOT / name).read_bytes()).hexdigest()
                for name in ("dist/cli.js", "dist/tools/query.js")},
            "calls": calls,
            "limitation": "Synthetic correctness fixture; not a model quality, token, cost, or performance benchmark.",
        }
        out.mkdir(parents=True, exist_ok=True)
        (out / "CLI_CONTRACT.json").write_text(json.dumps(report, indent=2) + "\n")
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "eval/results/controlled-next-2026-10-19")
    args = parser.parse_args()
    print(json.dumps({key: value for key, value in run(args.out).items() if key != "calls"}))
