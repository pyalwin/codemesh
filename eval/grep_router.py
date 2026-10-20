"""Source-verified grep and numbered read routing for the navigation evaluation.

Grep still runs a shadow native search, so this evaluates agent behavior rather
than search CPU savings. Native matches are never omitted.
"""

from __future__ import annotations

import ast
from hashlib import sha256
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Any


DECLARATION = re.compile(r"^(?:(?:async )?def|class) ([A-Za-z_][A-Za-z_0-9]*)(?:\()?$")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
RG_LINE = re.compile(r"^\./(.+?):(\d+):(.*)$")


def native_grep(query: str, repo: Path) -> str:
    process = subprocess.run(
        ["rg", "-n", "--fixed-strings", "--glob", "*.py", "--max-count", "8", query, "."],
        capture_output=True, text=True, cwd=repo, timeout=20,
    )
    if process.returncode not in (0, 1):
        raise ValueError(process.stderr.strip()[-1000:] or "grep failed")
    return "\n".join(process.stdout.splitlines()[:45])[:6000] or "No matches"


def native_read(path: str, start: int, limit: int, repo: Path) -> str:
    file = (repo / path).resolve()
    if not file.is_relative_to(repo.resolve()) or not file.is_file():
        raise ValueError("Path must name a file inside the target repository")
    lines = file.read_text(errors="replace").splitlines()
    return _numbered_read(path, start, limit, lines)


def _numbered_read(path: str, start: int, limit: int, lines: list[str]) -> str:
    selected = lines[start - 1:start - 1 + limit]
    return f"{path} lines {start}-{start + len(selected) - 1} of {len(lines)}:\n" + \
        "\n".join(f"{start + i}: {line}" for i, line in enumerate(selected))


def _db(repo: Path) -> sqlite3.Connection | None:
    path = repo / ".codemesh" / "codemesh.db"
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True) if path.is_file() else None


def _verified_file(repo: Path, path: str, indexed_hash: str) -> tuple[bytes, list[str]] | None:
    file = (repo / path).resolve()
    if not file.is_relative_to(repo.resolve()) or not file.is_file():
        return None
    content = file.read_bytes()
    if sha256(content).hexdigest() != indexed_hash:
        return None
    return content, content.decode("utf-8", errors="replace").splitlines()


def _symbol_rows(repo: Path, name: str) -> tuple[list[tuple[Any, ...]], str | None]:
    db = _db(repo)
    if db is None:
        return [], "index_missing"
    try:
        rows = db.execute(
            """SELECT n.id, n.name, json_extract(n.data, '$.filePath'),
                      json_extract(n.data, '$.lineStart'),
                      json_extract(n.data, '$.lineEnd'), f.hash
               FROM nodes n JOIN nodes f
                 ON f.id = 'file:' || json_extract(n.data, '$.filePath')
               WHERE n.type = 'symbol' AND f.type = 'file'
                 AND (n.name GLOB ? OR n.name GLOB ?)
               ORDER BY json_extract(n.data, '$.filePath'),
                        json_extract(n.data, '$.lineStart') LIMIT 101""",
            (f"{name}*", f"*.{name}*"),
        ).fetchall()
    finally:
        db.close()
    if len(rows) > 100:
        return [], "too_many_candidates"
    return rows, None if rows else "no_indexed_declarations"


def _return_evidence(content: str, line_start: int, cutoff: int) -> str | None:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    function = next((node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.lineno == line_start), None)
    if function is None:
        return None
    returns: list[ast.Return] = []

    def collect_returns(node: ast.AST) -> None:
        if node is not function and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            return
        if isinstance(node, ast.Return) and node.lineno > cutoff:
            returns.append(node)
        for child in ast.iter_child_nodes(node):
            collect_returns(child)

    collect_returns(function)
    if not returns:
        return None
    last = max(returns, key=lambda node: node.lineno)
    expression = last.value
    if expression is None:
        return f"return L{last.lineno}: None"
    calls = [node for node in ast.walk(expression) if isinstance(node, ast.Call)]
    delegate = next((call for call in calls if isinstance(call.func, ast.Attribute)
                     and call.func.attr != "decode"), calls[0] if calls else None)
    delegate_name = ast.unparse(delegate.func) if delegate else ast.unparse(expression)
    keywords = [f"{keyword.arg}={ast.unparse(keyword.value)[:48]}"
                for keyword in delegate.keywords if keyword.arg] if delegate else []
    suffix = ".decode()" if any(isinstance(call.func, ast.Attribute)
                               and call.func.attr == "decode" for call in calls) else ""
    details = f"; bindings: {', '.join(keywords)}" if keywords else ""
    rendered = f"{delegate_name}(…){suffix}" if delegate else delegate_name
    return f"return L{last.lineno}-L{last.end_lineno}: {rendered}{details}"


def route_grep(query: str, repo: Path) -> tuple[str, dict[str, Any]]:
    """Annotate only fresh declaration lines, preserving native grep results."""
    native = native_grep(query, repo)
    declaration = DECLARATION.fullmatch(query)
    name = declaration.group(1) if declaration else query if IDENTIFIER.fullmatch(query) else None
    if name is None:
        return native, {"backend": "native", "reason": "not_symbol_query", "indexed_matches": 0}
    rows, reason = _symbol_rows(repo, name)
    if reason:
        return native, {"backend": "native", "reason": reason, "indexed_matches": 0}
    indexed: dict[str, str] = {}
    cache: dict[str, tuple[bytes, list[str]]] = {}
    for node_id, _node_name, path, start, end, indexed_hash in rows:
        if not isinstance(path, str) or not isinstance(start, int) or start < 1:
            continue
        if path not in cache:
            verified = _verified_file(repo, path, indexed_hash)
            if verified is None:
                return native, {"backend": "native", "reason": "stale_index", "indexed_matches": 0}
            cache[path] = verified
        content, lines = cache[path]
        if start > len(lines) or query not in lines[start - 1]:
            continue
        span = f"span: {start}-{end}" if isinstance(end, int) and end >= start else f"start: {start}"
        hint = _return_evidence(content.decode("utf-8", errors="replace"), start, start)
        indexed[f"./{path}:{start}"] = f"  [codemesh-id: {node_id} | {span}" + \
            (f" | {hint}" if hint else "") + "]"
    native_lines = native.splitlines() if native != "No matches" else []
    keys: list[str] = []
    for line in native_lines:
        match = RG_LINE.fullmatch(line)
        if match is None:
            return native, {"backend": "native", "reason": "unparseable_native_result",
                            "indexed_matches": len(indexed)}
        keys.append(f"./{match.group(1)}:{match.group(2)}")
    if declaration and set(keys) != set(indexed):
        return native, {"backend": "native", "reason": "parity_mismatch",
                        "indexed_matches": len(indexed), "native_matches": len(keys)}
    count = sum(key in indexed for key in keys)
    if count == 0:
        return native, {"backend": "native", "reason": "no_visible_indexed_match",
                        "indexed_matches": len(indexed), "native_matches": len(keys)}
    annotated = [line + indexed.get(key, "") for line, key in zip(native_lines, keys)]
    return "\n".join(annotated), {
        "backend": "codemesh" if declaration else "hybrid",
        "reason": "fresh_exact_declaration_parity" if declaration else "fresh_symbol_annotation",
        "indexed_matches": count, "native_matches": len(keys),
    }


class ReadRouter:
    """Numbered reads with verified tail evidence and exact-repeat deduplication."""

    def __init__(self) -> None:
        self.seen: dict[tuple[str, int, int, str], int] = {}
        self.turn = 0

    def reset_session(self) -> None:
        self.seen.clear()
        self.turn = 0

    def route_read(self, path: str, start: int, limit: int,
                   repo: Path) -> tuple[str, dict[str, Any]]:
        file = (repo / path).resolve()
        if not file.is_relative_to(repo.resolve()) or not file.is_file():
            raise ValueError("Path must name a file inside the target repository")
        content = file.read_bytes()
        lines = content.decode("utf-8", errors="replace").splitlines()
        native = _numbered_read(path, start, limit, lines)
        if start > len(lines):
            return native, {"backend": "native", "reason": "empty_read"}
        end = min(start + limit - 1, len(lines))
        self.turn += 1
        db = _db(repo)
        if db is None:
            return native, {"backend": "native", "reason": "index_missing"}
        try:
            file_row = db.execute(
                "SELECT hash FROM nodes WHERE id = ? AND type = 'file'",
                (f"file:{path}",),
            ).fetchone()
            rows = db.execute(
                """SELECT n.id, n.name, json_extract(n.data, '$.lineStart'),
                          json_extract(n.data, '$.lineEnd')
                   FROM nodes n
                   WHERE n.type = 'symbol'
                     AND json_extract(n.data, '$.filePath') = ?
                     AND json_extract(n.data, '$.lineStart') BETWEEN ? AND ?
                   ORDER BY json_extract(n.data, '$.lineStart') LIMIT 5""",
                (path, max(1, start - 20), end),
            ).fetchall()
        finally:
            db.close()
        if file_row is None:
            return native, {"backend": "native", "reason": "file_not_indexed"}
        indexed_hash = file_row[0]
        if sha256(content).hexdigest() != indexed_hash:
            return native, {"backend": "native", "reason": "stale_index"}
        key = (path, start, end, indexed_hash)
        previous = self.seen.get(key)
        self.seen[key] = self.turn
        footers = []
        for _node_id, name, symbol_start, symbol_end in rows:
            if not isinstance(symbol_start, int) or not isinstance(symbol_end, int):
                continue
            if not (start - 20 <= symbol_start <= end and end < symbol_end):
                continue
            hint = _return_evidence(content.decode("utf-8", errors="replace"), symbol_start, end)
            if hint:
                footers.append(f"[Codemesh verified tail for {name}, {path}:{symbol_start}-{symbol_end}: {hint}]")
        footer = "\n".join(footers[:2])
        if previous is not None:
            stub = f"[Unchanged since read {previous}: {path}:{start}-{end}, sha256 {indexed_hash[:12]}]"
            return stub + ("\n" + footer if footer else ""), {
                "backend": "codemesh", "reason": "repeat_read", "saved_source_lines": end - start + 1}
        if footer:
            return native + "\n" + footer, {"backend": "codemesh", "reason": "verified_tail"}
        return native, {"backend": "native", "reason": "no_missing_tail"}
