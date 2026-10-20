#!/usr/bin/env python3
"""No-model CLI checks for bounded inspect/hydrate on pinned Pydantic."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from source_bundles import compact_source
from vnext_agent import build_fingerprint, handoff_payload, load_handoff, source_reference
from vnext_contract import PIN, ROOT, check, cli, command

SYMBOL = "symbol:pydantic/fields.py:FieldInfo.get_default@L901"
CALLEE = "symbol:pydantic/_internal/_fields.py:resolve_default_value"


def evaluate(repo):
    print("Indexing the pinned Pydantic fixture (no model calls)...", flush=True)
    _, index_ms = cli(repo, "index", "--json")
    print(f"Indexed in {index_ms} ms; checking CLI bundles...", flush=True)
    checks = []
    full, _ = cli(repo, "explore", "source", SYMBOL, "--mode", "full", "--compact")
    inspected, inspect_ms = cli(repo, "explore", "inspect", SYMBOL, "--compact")
    sources = [compact_source(item) for item in inspected["sources"]]
    checks.append(check("root_and_imported_callee", {SYMBOL, CALLEE}.issubset({item["id"] for item in sources}),
                        {"source_ids": [item["id"] for item in sources], "calls": inspected["calls"],
                         "latency_ms": inspect_ms}))
    checks.append(check("exact_live_bundle_source", bool(sources) and all(
        item["status"] == "fresh" and item["hash"] == sha256(item["source"].encode()).hexdigest()
        and item["source"] == "\n".join((repo / item["path"]).read_text().split("\n")[item["lines"][0]-1:item["lines"][1]])
        for item in sources), {"sources": len(sources)}))
    zero, _ = cli(repo, "explore", "inspect", SYMBOL, "--depth", "0", "--compact")
    checks.append(check("depth_zero_is_root_only", [s["id"] for s in zero["sources"]] == [SYMBOL], {}))
    bounded, _ = cli(repo, "explore", "inspect", SYMBOL, "--max-bytes", "1024", "--compact")
    size = len(json.dumps(bounded, ensure_ascii=False, separators=(",", ":")).encode())
    checks.append(check("inspect_budget_omits_complete_blocks", size <= 1024 and not bounded["sources"]
                        and bounded["omitted"]["sources"] > 0, {"json_bytes": size}))
    reference = source_reference(repo, json.dumps(full))
    assert reference
    saved = handoff_payload("Check the default factory wrapper.", [reference])
    cli(repo, "remember", "--path", reference["path"], "--summary", saved,
        "--session", "bundle-contract", "--status", "hypothesis")
    restored = []
    load_handoff(repo, reference["path"], saved, restored)
    refs = [{"id": ref["id"], "hash": ref["hash"], "mode": ref["mode"], "fileHash": ref["file_hash"]}
            for ref in restored]
    args = ("explore", "hydrate", "--references", json.dumps(refs), "--compact")
    hydrated, _ = cli(repo, *args)
    checks.append(check("persist_recall_hydrate_exact", hydrated["status"] == "complete"
                        and compact_source(hydrated["sources"][0])["source"] == full["source"], {}))
    small, _ = cli(repo, *args, "--max-bytes", "1024")
    checks.append(check("hydrate_budget_explicit", not small["sources"] and small["references"][0]["status"] == "omitted"
                        and len(json.dumps(small, ensure_ascii=False, separators=(",", ":")).encode()) <= 1024, {}))
    path = repo / reference["path"]
    path.write_text(path.read_text() + "\n# bundle contract revision\n")
    stale, _ = cli(repo, *args)
    stale_inspect, _ = cli(repo, "explore", "inspect", SYMBOL, "--compact")
    checks.append(check("unindexed_changes_withhold_all_root_source", not stale["sources"]
                        and stale["references"][0]["status"] == "stale"
                        and all(s.get("source") is None for s in stale_inspect["sources"]), {}))
    cli(repo, "index", "--json")
    changed, _ = cli(repo, *args)
    checks.append(check("reindex_does_not_validate_old_checkpoint", not changed["sources"]
                        and changed["references"][0]["status"] == "changed", {}))
    return {"checks": checks, "passed": sum(c["passed"] for c in checks), "total": len(checks), "index_ms": index_ms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "eval/results/vnext-bundle-contract.json")
    args = parser.parse_args()
    repo = args.repo.resolve()
    if command(["git", "rev-parse", "HEAD"], repo).stdout.strip() != PIN:
        parser.error(f"expected Pydantic commit {PIN}")
    with tempfile.TemporaryDirectory(prefix="codemesh-bundle-contract-") as temporary:
        worktree = Path(temporary) / "pydantic"
        added = command(["git", "worktree", "add", "--detach", str(worktree), PIN], repo)
        if added.returncode:
            raise RuntimeError(added.stderr)
        try:
            result = evaluate(worktree)
        finally:
            removed = command(["git", "worktree", "remove", "--force", str(worktree)], repo)
            if removed.returncode:
                raise RuntimeError(removed.stderr)
    report = {"kind": "black_box_cli_contract_no_models", "commit": PIN,
              "build_fingerprint": build_fingerprint(), **result}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return int(result["passed"] != result["total"])


if __name__ == "__main__":
    raise SystemExit(main())
