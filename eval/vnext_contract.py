#!/usr/bin/env python3
"""Black-box CLI contract eval against a pinned, ordinary open-source checkout.

This creates a disposable Git worktree. It never writes into the supplied repo.
Assertions describe observable behavior, rather than calling Codemesh internals.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PIN = "5adfeaaaea947f81f0f01868604f219084ec151a"
SYMBOL = "symbol:pydantic/main.py:BaseModel.model_dump"
FILE = "pydantic/main.py"
LARGE_SYMBOL = "symbol:pydantic/fields.py:FieldInfo"
LARGE_FILE = "pydantic/fields.py"


def command(argv: list[str], cwd: Path, *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=timeout)


def cli(repo: Path, *args: str) -> tuple[dict[str, Any], int]:
    started = time.monotonic_ns()
    process = command(["node", str(ROOT / "dist" / "cli.js"), *args], repo)
    elapsed = (time.monotonic_ns() - started) // 1_000_000
    if process.returncode:
        raise RuntimeError(f"codemesh {' '.join(args)} failed: {process.stderr[-1500:]}")
    return json.loads(process.stdout), elapsed


def pages(repo: Path, node: str, limit: int = 10) -> tuple[list[dict[str, Any]], int]:
    entries: list[dict[str, Any]] = []
    cursor = None
    elapsed = 0
    while True:
        args = ["explore", "map", "--node", node, "--limit", str(limit)]
        if cursor is not None:
            args += ["--cursor", cursor]
        result, ms = cli(repo, *args)
        entries.extend(result["entries"])
        elapsed += ms
        cursor = result.get("nextCursor")
        if cursor is None:
            break
    return entries, elapsed


def check(name: str, passed: bool, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), **evidence}


def search_cache_state(repo: Path) -> tuple[int, int | None]:
    db = sqlite3.connect(repo / ".codemesh" / "codemesh.db")
    try:
        version = db.execute("SELECT value FROM cache_meta WHERE key='graph_version'").fetchone()[0]
        row = db.execute("SELECT hit_count FROM retrieval_cache WHERE key=?",
                         (f"all:{SYMBOL.lower()}",)).fetchone()
        return version, row[0] if row else None
    finally:
        db.close()


def evaluate(repo: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    index, index_ms = cli(repo, "index", "--json")
    root, _ = pages(repo, "repo")
    package, _ = pages(repo, "dir:pydantic")
    file_symbols, _ = pages(repo, "file:pydantic/main.py")
    class_symbols, _ = pages(repo, "symbol:pydantic/main.py:BaseModel")
    checks.append(check("multi_level_navigation", all((
        any(x["id"] == "dir:pydantic" for x in root),
        any(x["id"] == "file:pydantic/main.py" for x in package),
        any(x["id"] == "symbol:pydantic/main.py:BaseModel" for x in file_symbols),
        any(x["id"] == SYMBOL for x in class_symbols),
        len({x["id"] for x in class_symbols}) == len(class_symbols),
    )), {"levels": 4, "class_children": len(class_symbols)}))

    again, _ = pages(repo, "symbol:pydantic/main.py:BaseModel")
    checks.append(check("stable_pagination", class_symbols == again,
                        {"entries": len(again)}))
    search, search_ms = cli(repo, "explore", "search", SYMBOL)
    checks.append(check("exact_search", any(x["id"] == SYMBOL for x in search["results"]),
                        {"result_count": len(search["results"]), "latency_ms": search_ms}))
    repeated_search, _ = cli(repo, "explore", "search", SYMBOL)
    cache_version, cache_hits = search_cache_state(repo)
    checks.append(check("persistent_search_cache", repeated_search["results"] == search["results"]
                        and cache_hits is not None and cache_hits >= 1,
                        {"cache_hits": cache_hits}))

    full, full_ms = cli(repo, "explore", "source", SYMBOL, "--mode", "full", "--session", "eval-a", "--epoch", "1")
    preview, _ = cli(repo, "explore", "source", SYMBOL, "--mode", "preview")
    fold, _ = cli(repo, "explore", "source", SYMBOL, "--mode", "fold")
    source_lines = (repo / FILE).read_text().split("\n")
    expected = "\n".join(source_lines[full["lineStart"] - 1:full["lineEnd"]])
    checks.append(check("source_exactness", full["status"] == "fresh" and full["source"] == expected
                        and full["sourceHash"] == sha256(expected.encode()).hexdigest(),
                        {"source_bytes": len(expected.encode()), "latency_ms": full_ms}))
    checks.append(check("progressive_views", fold["status"] == "fresh" and preview["status"] == "fresh"
                        and len(fold["source"]) < len(full["source"])
                        and len(preview["source"]) < len(full["source"])
                        and bool(preview.get("omittedRanges")),
                        {"fold_bytes": len(fold["source"].encode()),
                         "preview_bytes": len(preview["source"].encode()),
                         "full_bytes": len(full["source"].encode())}))

    # Auto is an explicit navigation view. A class that dominated the prior
    # agent run must be bounded without changing exact full/default retrieval.
    large_full, _ = cli(repo, "explore", "source", LARGE_SYMBOL, "--mode", "full", "--compact")
    large_auto, _ = cli(repo, "explore", "source", LARGE_SYMBOL, "--mode", "auto", "--compact")
    large_default, _ = cli(repo, "explore", "source", LARGE_SYMBOL, "--compact")
    large_lines = (repo / LARGE_FILE).read_text().split("\n")
    start, end = large_full["lines"]
    large_expected = "\n".join(large_lines[start - 1:end])
    full_bytes = len(large_expected.encode())
    auto_bytes = len(large_auto["source"].encode())
    full_observation_bytes = len(json.dumps(large_full, separators=(",", ":")).encode())
    auto_observation_bytes = len(json.dumps(large_auto, separators=(",", ":")).encode())
    omissions = large_auto.get("omittedRanges") or []
    checks.append(check("bounded_large_symbol_auto", large_auto["status"] == "fresh"
                        and large_auto["mode"] == "auto" and (end - start + 1 > 120 or full_bytes > 12 * 1024)
                        and 0 < auto_bytes <= 12 * 1024 and auto_bytes < full_bytes
                        and auto_observation_bytes < full_observation_bytes and bool(omissions)
                        and all(start <= span["start"] <= span["end"] <= end for span in omissions)
                        and large_auto["hash"] == sha256(large_auto["source"].encode()).hexdigest()
                        and "mode=full" in large_auto.get("hint", ""),
                        {"symbol": LARGE_SYMBOL, "lines": end - start + 1,
                         "full_source_bytes": full_bytes, "auto_source_bytes": auto_bytes,
                         "source_reduction_percent": round(100 * (1 - auto_bytes / full_bytes), 2),
                         "full_compact_observation_bytes": full_observation_bytes,
                         "auto_compact_observation_bytes": auto_observation_bytes,
                         "observation_reduction_percent": round(
                             100 * (1 - auto_observation_bytes / full_observation_bytes), 2),
                         "omitted_ranges": omissions}))
    checks.append(check("large_symbol_full_and_default_exact", all(
        view["status"] == "fresh" and view["mode"] == "full" and view["source"] == large_expected
        and view["hash"] == sha256(large_expected.encode()).hexdigest()
        and not view.get("omittedRanges") for view in (large_full, large_default)),
        {"symbol": LARGE_SYMBOL, "source_bytes": full_bytes}))
    short_auto, _ = cli(repo, "explore", "source", SYMBOL, "--mode", "auto")
    checks.append(check("small_symbol_auto_exact", full["lineEnd"] - full["lineStart"] + 1 <= 120
                        and len(expected.encode()) <= 12 * 1024 and short_auto["status"] == "fresh"
                        and short_auto["source"] == expected and not short_auto.get("omittedRanges")
                        and short_auto["sourceHash"] == full["sourceHash"],
                        {"symbol": SYMBOL, "source_bytes": len(expected.encode())}))

    unchanged, _ = cli(repo, "explore", "source", SYMBOL, "--mode", "full", "--if-hash", full["sourceHash"])
    rehydrated, _ = cli(repo, "explore", "source", SYMBOL, "--mode", "full", "--if-hash", full["sourceHash"],
                        "--force", "--session", "eval-b", "--epoch", "2")
    new_session, _ = cli(repo, "explore", "source", SYMBOL, "--mode", "full", "--session", "eval-c", "--epoch", "1")
    checks.append(check("conditional_and_rehydrate", unchanged["status"] == "unchanged"
                        and unchanged["source"] is None and rehydrated["source"] == expected
                        and new_session["source"] == expected,
                        {"avoided_source_bytes": len(expected.encode())}))

    note_text = "BaseModel.model_dump delegates dictionary export to to_python."
    saved, _ = cli(repo, "remember", "--path", FILE, "--symbol", "BaseModel.model_dump",
                   "--summary", note_text, "--session", "eval-a", "--status", "verified")
    recalled, _ = cli(repo, "explore", "recall", "--path", FILE, "--symbol", "BaseModel.model_dump")
    checks.append(check("cross_process_recall", saved["success"] and any(
        n["summary"] == note_text and n["status"] == "verified" for n in recalled["notes"]),
        {"note_id": saved["nodeId"]}))

    live = repo / FILE
    before = live.read_text()
    anchor = "        return self.__pydantic_serializer__.to_python("
    if before.count(anchor) != 1:
        raise RuntimeError("Pydantic mutation anchor is ambiguous")
    live.write_text(before.replace(anchor, "        # vNext eval revision marker\n" + anchor, 1))
    try:
        stale_source, _ = cli(repo, "explore", "source", SYMBOL, "--mode", "full")
        stale_recall, _ = cli(repo, "explore", "recall", "--path", FILE, "--symbol", "BaseModel.model_dump")
        checks.append(check("live_stale_guard", stale_source["status"] == "stale" and stale_source["source"] is None
                            and any(n["id"] == saved["nodeId"] and n["status"] == "stale"
                                    for n in stale_recall["notes"]), {}))
        rebuilt, rebuild_ms = cli(repo, "rebuild", "--json")
        cli(repo, "explore", "search", SYMBOL)
        next_version, next_hits = search_cache_state(repo)
        after, _ = cli(repo, "explore", "source", SYMBOL, "--mode", "full")
        recalled_after, _ = cli(repo, "explore", "recall", "--path", FILE, "--symbol", "BaseModel.model_dump")
        checks.append(check("rebuild_preserves_and_stales_knowledge", after["status"] == "fresh"
                            and after["sourceHash"] != full["sourceHash"]
                            and any(n["id"] == saved["nodeId"] and n["status"] == "stale"
                                    for n in recalled_after["notes"]),
                            {"rebuild_ms": rebuild_ms, "files_indexed": rebuilt.get("filesIndexed")}))
        checks.append(check("search_cache_invalidation", next_version > cache_version and next_hits == 0,
                            {"before_version": cache_version, "after_version": next_version,
                             "post_rebuild_hits": next_hits}))
    finally:
        live.write_text(before)

    db = sqlite3.connect(repo / ".codemesh" / "codemesh.db")
    try:
        metrics = {key: value for key, value in db.execute("SELECT key, value FROM context_metrics")}
        deliveries = db.execute("SELECT COUNT(*) FROM session_deliveries").fetchone()[0]
    finally:
        db.close()
    return {"checks": checks, "passed": sum(x["passed"] for x in checks),
            "total": len(checks), "index": index, "index_ms": index_ms,
            "metrics": metrics, "delivery_records": deliveries}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="clean checkout of pinned Pydantic commit")
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "results" / "vnext-contract.json")
    args = parser.parse_args()
    repo = args.repo.resolve()
    commit = command(["git", "rev-parse", "HEAD"], repo)
    if commit.returncode or commit.stdout.strip() != PIN:
        parser.error(f"expected Pydantic commit {PIN}")
    result: dict[str, Any]
    with tempfile.TemporaryDirectory(prefix="codemesh-vnext-contract-") as directory:
        worktree = Path(directory) / "pydantic"
        added = command(["git", "worktree", "add", "--detach", str(worktree), PIN], repo)
        if added.returncode:
            raise RuntimeError(added.stderr)
        try:
            result = evaluate(worktree)
        finally:
            removed = command(["git", "worktree", "remove", "--force", str(worktree)], repo)
            if removed.returncode:
                raise RuntimeError(removed.stderr)
    report = {"repository": "https://github.com/pydantic/pydantic.git", "commit": PIN,
              "kind": "black_box_cli_contract", **result}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "total": result["total"],
                      "checks": result["checks"], "report": str(args.out)}, indent=2))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
