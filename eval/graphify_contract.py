#!/usr/bin/env python3
"""No-model public CLI contract for the deterministic retrieval improvements."""
import argparse
import json
from pathlib import Path
import tempfile

from vnext_contract import cli


def run(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    calls = []
    with tempfile.TemporaryDirectory(prefix="codemesh-graphify-contract-") as directory:
        repo = Path(directory)
        files = {
            "pkg/__init__.py": "",
            "pkg/core.py": "def helper(data):\n    return dict(data)\n\ndef resolve_default_value(factory, data):\n    return factory(helper(data))\n",
            "pkg/fields.py": "from pkg.core import resolve_default_value\n\nclass FieldInfo:\n    def get_default(self, factory, validated_data):\n        return resolve_default_value(factory, validated_data)\n",
            "tests/test_helpers.py": "def test_get_default():\n    return 'helper only'\n",
        }
        for name, text in files.items():
            target = repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)

        def invoke(*args):
            result, elapsed = cli(repo, *args)
            calls.append({"arguments": args, "elapsed_ms": elapsed, "output": result})
            return result

        invoke("index", "--json")
        root = invoke("explore", "map", "--max-bytes", "512")
        branch = invoke("explore", "map", "--node", "dir:pkg", "--max-bytes", "1024")
        search = invoke("explore", "search", "get_default", "--path", "pkg", "--scope", "symbols", "--compact")
        expected = "symbol:pkg/fields.py:FieldInfo.get_default"
        assert search["results"][0]["id"] == expected
        assert search["results"][0]["why"] == "exact_leaf"
        assert all("tests/" not in item["id"] for item in search["results"])
        resolver = "symbol:pkg/core.py:resolve_default_value"
        inspected = invoke("explore", "inspect", resolver, "--direction", "both", "--max-candidates", "4",
                           "--max-symbols", "3", "--max-bytes", "12000")
        assert {s["id"] for s in inspected["sources"]} == {expected, resolver, "symbol:pkg/core.py:helper"}
        assert any(c["sourceId"] == expected and c.get("targetId") == resolver and c["status"] == "included"
                   for c in inspected["calls"])
        assert inspected["incoming"]["complete"] is False
        for source in inspected["sources"]:
            exact = "\n".join(files[source["filePath"]].split("\n")[source["lineStart"]-1:source["lineEnd"]])
            assert source["source"] == exact
        assert len(json.dumps(root, separators=(",", ":")).encode()) <= 512
        assert len(json.dumps(branch, separators=(",", ":")).encode()) <= 1024
        assert len(json.dumps(inspected, separators=(",", ":")).encode()) <= 12000
        (repo / "pkg/fields.py").write_text(files["pkg/fields.py"].replace(
            "return resolve_default_value(factory, validated_data)", "return resolve_default_value(factory, {})"))
        changed = invoke("explore", "inspect", resolver, "--direction", "incoming", "--max-bytes", "12000")
        assert all(source["id"] != expected for source in changed["sources"])
        report = {"kind": "public CLI correctness contract", "model_calls": 0,
                  "passed": True, "checks": ["root/child navigation byte caps", "scoped exact leaf search",
                    "real indexed caller and callee proof", "exact whole source", "incoming incompleteness explicit",
                    "stale caller excluded"], "calls": calls,
                  "limitation": "Synthetic code fixture; not a model quality, token, cost, or performance benchmark."}
        (out / "CLI_CONTRACT.json").write_text(json.dumps(report, indent=2) + "\n")
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("eval/results/graphify-deterministic-2026-10-19"))
    args = parser.parse_args()
    print(json.dumps({key: value for key, value in run(args.out).items() if key != "calls"}))
