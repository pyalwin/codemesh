"""Exact, bounded filesystem controls for source bundles; no model calls."""

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from grep_router import native_read

BUNDLE_BYTES = 24576
BUNDLE_SYMBOLS = 4
SOURCE_ACTIONS = frozenset(("source", "read", "inspect", "hydrate", "batch_read"))


def compact_source(value: dict[str, Any]) -> dict[str, Any]:
    """Accept both the public SourceOutput and compact CLI representation."""
    result = dict(value)
    for target, original in (("path", "filePath"), ("hash", "sourceHash")):
        if target not in result and original in result:
            result[target] = result[original]
    if "lines" not in result and "lineStart" in result:
        result["lines"] = [result["lineStart"], result["lineEnd"]]
    return result


def source_blocks(action: str, observation: str) -> list[dict[str, Any]]:
    try:
        result = json.loads(observation)
        if not isinstance(result, dict):
            return []
        values = [result] if action == "source" else result.get("sources", [])
        return [compact_source(item) for item in values if isinstance(item, dict)]
    except (ValueError, TypeError):
        return []


def _file(repo: Path, path: str) -> Path:
    if not isinstance(path, str):
        raise ValueError("A repository-relative path is required")
    relative = Path(path)
    target = (repo / relative).resolve()
    if relative.is_absolute() or ".." in relative.parts or not target.is_relative_to(repo.resolve()) or not target.is_file():
        raise ValueError("Path must be a file inside the repository")
    return target


def read_span(repo: Path, request: dict[str, Any]) -> dict[str, Any]:
    path = request.get("path")
    start, limit = request.get("start") or 1, request.get("limit") or 60
    if type(start) is not int or type(limit) is not int or start < 1 or not 1 <= limit <= 160:
        raise ValueError("read range must be positive and <=160 lines")
    live = _file(repo, path).read_bytes()
    lines = live.decode().splitlines()
    selected = lines[start - 1:start - 1 + limit]
    if not selected:
        raise ValueError("Requested range has no source lines")
    source = "\n".join(selected)
    path = Path(path).as_posix().removeprefix("./")
    return {"status": "fresh", "path": path, "lines": [start, start + len(selected) - 1],
            "mode": "full", "hash": sha256(source.encode()).hexdigest(),
            "fileHash": sha256(live).hexdigest(), "source": source}


def read_reference(repo: Path, decision: dict[str, Any], observation: str) -> dict[str, Any] | None:
    try:
        result = read_span(repo, decision)
        # The original numbered read must still match this same live revision.
        if native_read(decision["path"], decision.get("start") or 1,
                       decision.get("limit") or 60, repo) != observation:
            return None
        return span_reference(repo, result)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def span_reference(repo: Path, result: dict[str, Any]) -> dict[str, Any] | None:
    try:
        path, lines = result["path"], result["lines"]
        if (result.get("status") != "fresh" or result.get("mode") != "full"
                or not isinstance(lines, list) or len(lines) != 2
                or any(type(n) is not int for n in lines) or not 1 <= lines[0] <= lines[1]):
            return None
        live = read_span(repo, {"path": path, "start": lines[0], "limit": lines[1] - lines[0] + 1})
        if live["hash"] != result.get("hash") or live["source"] != result.get("source"):
            return None
        if result.get("fileHash") and result["fileHash"] != live["fileHash"]:
            return None
        return {"id": f"read:{path}:{lines[0]}:{lines[1]}", "path": path, "lines": lines,
                "hash": live["hash"], "mode": "full", "file_hash": live["fileHash"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def native_bundle(repo: Path, requests: list[dict[str, Any]], *, hydrate: bool = False,
                  max_bytes: int = BUNDLE_BYTES, max_symbols: int = BUNDLE_SYMBOLS) -> dict[str, Any]:
    """Read at most eight explicit spans, verifying saved hashes on hydration.

    Budget includes the whole JSON envelope; oversized spans are omitted whole.
    """
    if (not isinstance(requests, list) or not 1 <= len(requests) <= 8
            or any(not isinstance(item, dict) for item in requests)):
        raise ValueError("Provide 1..8 explicit read requests")
    if not 1024 <= max_bytes <= 131072 or not 1 <= max_symbols <= 8:
        raise ValueError("Invalid bundle limits")
    result: dict[str, Any] = {"status": "complete", "sources": [],
        "references": [{"index": index, "status": "omitted"} for index in range(len(requests))],
        "omitted": {"sources": 0}, "limits": {"maxBytes": max_bytes, "maxSymbols": max_symbols}}
    seen = set()
    accepted: list[tuple[int, tuple[str, tuple[int, ...]]]] = []
    duplicate_of: dict[int, tuple[str, tuple[int, ...]]] = {}
    for index, request in enumerate(requests):
        item = result["references"][index]
        try:
            if hydrate:
                lines = request.get("lines")
                if (not isinstance(lines, list) or len(lines) != 2
                        or any(type(n) is not int for n in lines) or not 1 <= lines[0] <= lines[1]):
                    raise ValueError("Invalid saved span")
                spec = {"path": request.get("path"), "start": lines[0], "limit": lines[1] - lines[0] + 1}
            else:
                spec = request
            source = read_span(repo, spec)
            if hydrate and (source["fileHash"] != request.get("file_hash") or source["hash"] != request.get("hash")):
                item["status"] = "changed"
                continue
            key = (source["path"], tuple(source["lines"]))
            if key in seen:
                item["status"] = "duplicate"
                duplicate_of[index] = key
                continue
            if len(result["sources"]) >= max_symbols:
                continue
            item["status"] = "fresh"
            result["sources"].append(source)
            # Reserve a partial label/count even while future items are pending.
            result["status"] = "partial"
            result["omitted"]["sources"] = len(requests)
            if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) > max_bytes:
                result["sources"].pop()
                item["status"] = "omitted"
            else:
                seen.add(key)
                accepted.append((index, key))
        except (OSError, ValueError, KeyError, TypeError):
            item["status"] = "missing" if hydrate else "error"
    # Later reads can race edits to an earlier file. Recheck each accepted
    # snapshot before exposing any saved or newly read span as fresh.
    for position in range(len(accepted) - 1, -1, -1):
        index, key = accepted[position]
        source = result["sources"][position]
        try:
            current = sha256(_file(repo, source["path"]).read_bytes()).hexdigest()
        except (OSError, ValueError):
            current = None
        if current != source["fileHash"]:
            result["references"][index]["status"] = "stale" if current else "missing"
            for duplicate, original in duplicate_of.items():
                if original == key:
                    result["references"][duplicate]["status"] = result["references"][index]["status"]
            result["sources"].pop(position)
            accepted.pop(position)
    result["omitted"]["sources"] = sum(item["status"] == "omitted" for item in result["references"])
    result["status"] = "complete" if all(item["status"] in ("fresh", "duplicate") for item in result["references"]) else "partial"
    # Later duplicate/status labels can grow the envelope; enforce the FINAL
    # serialization, removing only whole spans and their duplicate claims.
    while len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) > max_bytes and accepted:
        index, key = accepted.pop()
        result["sources"].pop()
        result["references"][index]["status"] = "omitted"
        for duplicate, original in duplicate_of.items():
            if original == key:
                result["references"][duplicate]["status"] = "omitted"
        result["status"] = "partial"
        result["omitted"]["sources"] = sum(item["status"] == "omitted" for item in result["references"])
    return result
