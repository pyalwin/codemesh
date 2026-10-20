"""Source-backed evidence for query-aware Python code capsules.

The extractor records syntactic observations, not a complete execution trace.
Every record keeps the physical source line from which it was obtained.
"""

from __future__ import annotations

import ast
import re
from typing import Any


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "code", "does",
    "for", "from", "how", "in", "is", "it", "its", "of", "on", "or", "the",
    "their", "them", "this", "to", "what", "when", "where", "which", "who",
    "with", "work", "would",
}
LOW_VALUE_CALLS = {
    "isinstance", "cast", "len", "type", "str", "int", "dict", "list", "set",
    "get", "copy", "items", "keys", "values", "AttributeError", "PydanticUserError",
}


def words(value: str) -> set[str]:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    tokens = re.findall(r"[a-z_][a-z_0-9]*", split_camel.casefold())
    expanded = set(tokens)
    for token in tokens:
        expanded.update(token.split("_"))
    return expanded - STOPWORDS


def callee_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = callee_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


class CallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.assignments: list[dict[str, Any]] = []
        self.raises: list[dict[str, Any]] = []
        self.guards: list[str] = []
        self.guard_lines: list[int] = []

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        condition = ast.unparse(node.test)
        self.guards.append(condition)
        self.guard_lines.append(node.lineno)
        for child in node.body:
            self.visit(child)
        self.guards.pop()
        self.guard_lines.pop()
        if node.orelse:
            self.guards.append(f"not ({condition})")
            self.guard_lines.append(node.lineno)
            for child in node.orelse:
                self.visit(child)
            self.guards.pop()
            self.guard_lines.pop()

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        condition = ast.unparse(node.test)
        self.guards.append(condition)
        self.guard_lines.append(node.lineno)
        self.visit(node.body)
        self.guards.pop()
        self.guard_lines.pop()
        self.guards.append(f"not ({condition})")
        self.guard_lines.append(node.lineno)
        self.visit(node.orelse)
        self.guards.pop()
        self.guard_lines.pop()

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc:
            self.raises.append({
                "line": node.lineno,
                "guard_line": self.guard_lines[0] if self.guard_lines else None,
                "exception": ast.unparse(node.exc),
                "guards": list(self.guards),
            })
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = callee_name(node.func)
        if name:
            arguments = [ast.unparse(arg) for arg in node.args]
            arguments += [
                f"{keyword.arg}={ast.unparse(keyword.value)}" if keyword.arg else f"**{ast.unparse(keyword.value)}"
                for keyword in node.keywords
            ]
            self.calls.append({
                "line": node.lineno,
                "callee": name,
                "arguments": arguments,
                "guards": list(self.guards),
            })
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.assignments.append({
            "line": node.lineno,
            "targets": [ast.unparse(target) for target in node.targets],
            "value": ast.unparse(node.value),
            "guards": list(self.guards),
        })
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            self.assignments.append({
                "line": node.lineno,
                "targets": [ast.unparse(node.target)],
                "value": ast.unparse(node.value),
                "guards": list(self.guards),
            })
        self.generic_visit(node)


def extract_evidence(node: ast.AST, source: str) -> dict[str, list[dict[str, Any]]]:
    lines = source.splitlines()
    docs: list[dict[str, Any]] = []
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            doc_node = body[0]
            for line_number in range(doc_node.lineno, doc_node.end_lineno + 1):
                line = lines[line_number - 1].strip().strip('"\'').strip()
                if line and not line.startswith(("!!!", "```", "[`", "#")) and line not in {"Args:", "Returns:"}:
                    docs.append({"line": line_number, "text": line})
    collector = CallCollector()
    collector.visit(node)
    return {"docs": docs, "calls": collector.calls, "assignments": collector.assignments, "raises": collector.raises}


def select_docs(evidence: dict[str, Any], question: str, limit: int = 4) -> list[dict[str, Any]]:
    query = words(question)
    frequency: dict[str, int] = {}
    for item in evidence["docs"]:
        for word in words(item["text"]) & query:
            frequency[word] = frequency.get(word, 0) + 1
    ranked = [
        (sum(1 / frequency[word] for word in words(item["text"]) & query), index, item)
        for index, item in enumerate(evidence["docs"])
    ]
    selected = sorted(
        ((score, index, item) for score, index, item in ranked if score),
        key=lambda entry: (-entry[0], entry[1]),
    )[:limit]
    return [item for _, _, item in sorted(selected, key=lambda entry: entry[1])]


def select_calls(evidence: dict[str, Any], question: str, limit: int = 8) -> list[dict[str, Any]]:
    query = words(question)
    ranked = []
    for index, item in enumerate(evidence["calls"]):
        if item["callee"].split(".")[-1] in LOW_VALUE_CALLS:
            continue
        call_text = item["callee"] + " " + " ".join(item["arguments"])
        score = len(words(call_text) & query)
        ranked.append((score, index, item))
    selected = sorted(ranked, key=lambda entry: (-entry[0], entry[1]))[:limit]
    return [item for _, _, item in sorted(selected, key=lambda entry: entry[1])]


def select_assignments(evidence: dict[str, Any], question: str, limit: int = 5) -> list[dict[str, Any]]:
    query = words(question)
    ranked = []
    for index, item in enumerate(evidence["assignments"]):
        if not item["guards"] and not any("." in target or "[" in target for target in item["targets"]):
            continue
        if len(item["targets"]) == 1 and "." not in item["targets"][0] and item["value"] in {"{}", "[]", "None", "False", "True", "0"}:
            continue
        text = " ".join(item["targets"] + [item["value"]] + item["guards"])
        score = len(words(text) & query)
        if score:
            ranked.append((score, index, item))
    selected = sorted(ranked, key=lambda entry: (-entry[0], entry[1]))[:limit]
    return [item for _, _, item in sorted(selected, key=lambda entry: entry[1])]


def format_v2(selected: list[dict[str, Any]], question: str) -> str:
    blocks = []
    for unit in selected:
        evidence = unit["evidence"]
        lines = [
            f"### {unit['id']}",
            f"Version: sha256:{unit['source_sha256'][:12]}",
            f"Source: {unit['path']}:{unit['lines']}",
            f"Declaration: {unit['signature']}",
            f"Overview (generated; verify against evidence): {unit['purpose']}",
        ]
        docs = select_docs(evidence, question)
        if docs:
            lines.append("Source documentation:")
            lines.extend(f"- {unit['path']}:{item['line']} {item['text']}" for item in docs)
        calls = select_calls(evidence, question)
        if calls:
            lines.append("Call evidence (syntactic; guards show branch nesting):")
            for call in calls:
                guard = f" if {' and '.join(call['guards'])}" if call["guards"] else ""
                lines.append(
                    f"- {unit['path']}:{call['line']}{guard}: "
                    f"{call['callee']}({', '.join(call['arguments'])})"
                )
        assignments = select_assignments(evidence, question)
        if assignments:
            lines.append("Assignment evidence (syntactic; guards show branch nesting):")
            for assignment in assignments:
                guard = f" if {' and '.join(assignment['guards'])}" if assignment["guards"] else ""
                value = assignment["value"]
                same_line_call = next((call for call in calls if call["line"] == assignment["line"] and value.startswith(call["callee"] + "(")), None)
                if same_line_call:
                    value = f"result of {same_line_call['callee']}(...) above"
                lines.append(
                    f"- {unit['path']}:{assignment['line']}{guard}: "
                    f"{', '.join(assignment['targets'])} = {value}"
                )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
