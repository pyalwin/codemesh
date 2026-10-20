#!/usr/bin/env python3
"""No-model, pinned-fixture checks for source names and SQLite handoff references."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from vnext_agent import (build_fingerprint, handoff_payload, load_handoff,
                         remove_worktree, source_reference, worktree)
from vnext_contract import PIN, ROOT, cli, command


def references_in(text: str) -> list[dict[str, Any]]:
    return next(json.loads(line) for line in text.splitlines() if line.startswith("[{"))


def evaluate(repo: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, **evidence: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **evidence})

    ambiguous = command(["node", str(ROOT / "dist/cli.js"), "explore", "source",
                         "FieldInfo.get_default", "--mode", "full", "--compact"], repo)
    candidates = [json.loads(line) for line in ambiguous.stderr.splitlines() if line.startswith('{"id":')]
    implementation = "symbol:pydantic/fields.py:FieldInfo.get_default@L901"
    check("qualified_overload_lists_exact_ids", ambiguous.returncode != 0
          and len(candidates) == 3 and any(item["id"] == implementation and item["lineStart"] == 901
                                          and item["lineEnd"] == 921 for item in candidates),
          exit_code=ambiguous.returncode, candidates=candidates, stderr=ambiguous.stderr.strip())

    full, _ = cli(repo, "explore", "source", implementation, "--mode", "full", "--compact")
    expected = "\n".join((repo / full["path"]).read_text().split("\n")[full["lines"][0] - 1:full["lines"][1]])
    check("explicit_overload_body_is_exact", full["status"] == "fresh" and full["source"] == expected
          and full["hash"] == sha256(expected.encode()).hexdigest(),
          id=full["id"], lines=full["lines"], hash=full["hash"], source_bytes=len(expected.encode()))

    resolver, _ = cli(repo, "explore", "source", "resolve_default_value", "--mode", "full", "--compact")
    resolver_id = "symbol:pydantic/_internal/_fields.py:resolve_default_value"
    exact_resolver, _ = cli(repo, "explore", "source", resolver_id, "--mode", "full", "--compact")
    check("unique_name_returns_canonical_source", resolver == exact_resolver and resolver["status"] == "fresh"
          and resolver["id"] == resolver_id, id=resolver["id"], lines=resolver["lines"], hash=resolver["hash"])

    refs = [source_reference(repo, json.dumps(item)) for item in (full, resolver)]
    check("delivered_source_produces_references", all(ref is not None and "source" not in ref for ref in refs),
          references=refs)
    if any(ref is None for ref in refs):
        return checks
    summary = "The field wrapper forwards default resolution options to the internal resolver. Verify live source before patching."
    payload = handoff_payload(summary, refs)
    cli(repo, "remember", "--path", "pydantic/fields.py", "--summary", payload,
        "--session", "handoff-contract", "--status", "hypothesis")
    with sqlite3.connect(repo / ".codemesh/codemesh.db") as database:
        notes = [json.loads(row[0]) for row in database.execute("SELECT data FROM nodes WHERE type='concept'")]
    stored = next((note for note in notes if note.get("summary") == payload), None)
    check("structured_references_persist_in_sqlite", stored is not None
          and json.loads(stored["summary"])["references"] == refs,
          format=json.loads(payload)["format"], reference_count=len(refs), payload_bytes=len(payload.encode()))

    restored = load_handoff(repo, "pydantic/fields.py", payload)
    fresh_refs = references_in(restored)
    check("fresh_session_restores_pointers_without_source", len(fresh_refs) == 2 and all(
        item["id"] == ref["id"] and item["lines"] == ref["lines"] and item["hash"] == ref["hash"]
        and item["status"] == "file_unchanged" and "source" not in item
        for item, ref in zip(fresh_refs, refs))
          and full["source"] not in restored and resolver["source"] not in restored
          and "Saved hashes do not authorize if_hash" in restored,
          restored_references=fresh_refs, restored_bytes=len(restored.encode()))

    other_file = repo / resolver["path"]
    other_file.write_text("# External change after recording the handoff.\n" + other_file.read_text())
    stale_rendered = load_handoff(repo, "pydantic/fields.py", payload)
    stale_refs = references_in(stale_rendered)
    stale_ref = next(item for item in stale_refs if item["id"] == resolver_id)
    current_ref = next(item for item in stale_refs if item["id"] == implementation)
    check("changed_secondary_reference_loses_old_span_and_hash", stale_ref["status"] == "stale"
          and not any(key in stale_ref for key in ("lines", "hash", "mode", "source"))
          and resolver["hash"] not in stale_rendered and current_ref["status"] == "file_unchanged"
          and current_ref["hash"] == full["hash"], restored_references=stale_refs)

    stale_source, _ = cli(repo, "explore", "source", "resolve_default_value", "--mode", "full", "--compact")
    check("name_fallback_preserves_stale_source_status", stale_source["id"] == resolver_id
          and stale_source["status"] == "stale" and stale_source["source"] is None
          and stale_source["hash"] is None, status=stale_source["status"], id=stale_source["id"])

    other_file.unlink()
    unavailable_refs = references_in(load_handoff(repo, "pydantic/fields.py", payload))
    unavailable = next(item for item in unavailable_refs if item["id"] == resolver_id)
    check("deleted_secondary_reference_is_unavailable", unavailable["status"] == "unavailable"
          and not any(key in unavailable for key in ("lines", "hash", "mode", "source")), reference=unavailable)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("/tmp/codemesh-pydantic-benchmark"))
    parser.add_argument("--index", type=Path, default=ROOT / "eval/results/vnext-luna-factory-pilot/indexes/factory_validated_data.db")
    parser.add_argument("--output", type=Path, default=ROOT / "eval/results/vnext-handoff-contract.json")
    args = parser.parse_args()
    task = next(item for item in json.loads((ROOT / "eval/edit_workflow_tasks.json").read_text())["tasks"]
                if item["id"] == "factory_validated_data")
    report: dict[str, Any] = {"commit": PIN, "task": task["id"], "model_calls": 0,
                              "build_fingerprint": build_fingerprint(), "source_index": str(args.index.resolve())}
    with tempfile.TemporaryDirectory(prefix="codemesh-handoff-contract-") as temporary:
        checkout = Path(temporary) / "checkout"
        try:
            worktree(args.repo.resolve(), checkout, task, args.index.resolve())
            report["checks"] = evaluate(checkout)
        except Exception as exc:
            report["error"] = str(exc)
        finally:
            remove_worktree(args.repo.resolve(), checkout)
            report["worktree_removed"] = not checkout.exists()
    report["passed"] = bool(report.get("checks")) and all(item["passed"] for item in report["checks"]) and "error" not in report
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "checks": len(report.get("checks", [])),
                      "error": report.get("error"), "report": str(args.output)}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
