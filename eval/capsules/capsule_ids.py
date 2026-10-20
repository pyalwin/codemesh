"""Prompt-local aliases for source-backed v2 capsules.

The complete source identity stays in a sidecar map; only short aliases are
sent to the model. Evidence is unchanged apart from its address labels.
"""

from __future__ import annotations

import re
from typing import Any

from capsule_v2 import format_v2


def format_v2_ids(selected: list[dict[str, Any]], question: str) -> tuple[str, dict[str, Any]]:
    context = format_v2(selected, question)
    mapping: dict[str, Any] = {"units": {}, "evidence": {}}
    for index, unit in enumerate(selected, 1):
        alias = f"U{index}"
        mapping["units"][alias] = {
            "unit_id": unit["id"],
            "path": unit["path"],
            "lines": unit["lines"],
            "source_sha256": unit["source_sha256"],
        }
        context = context.replace(f"### {unit['id']}\n", f"### {alias} {unit['name']}\n", 1)
    context = re.sub(r"^Version: sha256:[a-f0-9]+\n", "", context, flags=re.MULTILINE)
    context = re.sub(r"^Source: [^\n]+\n", "", context, flags=re.MULTILINE)

    # Match only the selected source files. This avoids changing paths that
    # occur inside generated prose or arbitrary code expressions.
    paths = sorted({unit["path"] for unit in selected}, key=len, reverse=True)
    source_ref = re.compile(r"(?<![\w/])(?:" + "|".join(map(re.escape, paths)) + r"):\d+(?:-\d+)?")
    aliases_by_ref: dict[str, str] = {}

    def replace_ref(match: re.Match[str]) -> str:
        ref = match.group()
        if ref not in aliases_by_ref:
            alias = f"E{len(aliases_by_ref) + 1}"
            aliases_by_ref[ref] = alias
            path, lines = ref.rsplit(":", 1)
            line = int(lines.split("-", 1)[0])
            unit = next(
                unit for unit in selected
                if unit["path"] == path
                and int(unit["lines"].split("-", 1)[0]) <= line <= int(unit["lines"].split("-", 1)[-1])
            )
            mapping["evidence"][alias] = {
                "path": path, "lines": lines,
                "unit_id": unit["id"], "source_sha256": unit["source_sha256"],
            }
        return aliases_by_ref[ref]

    context = source_ref.sub(replace_ref, context)
    return context, mapping


def expand_citations(answer: str, mapping: dict[str, Any]) -> str:
    """Render model citations as source locations for comparison and review."""
    def replace_range(match: re.Match[str]) -> str:
        first = mapping["evidence"].get(match.group(1))
        last = mapping["evidence"].get(match.group(2))
        if not first or not last or first["path"] != last["path"]:
            return match.group()
        return f"{first['path']}:{first['lines']}-{last['lines']}"

    answer = re.sub(r"\b(E\d+)-(E\d+)\b", replace_range, answer)

    def replace(match: re.Match[str]) -> str:
        alias = match.group()
        if alias in mapping["evidence"]:
            item = mapping["evidence"][alias]
            return f"{item['path']}:{item['lines']}"
        if alias in mapping["units"]:
            item = mapping["units"][alias]
            return f"{item['path']}:{item['lines']}"
        return alias

    return re.sub(r"\b(?:E|U)\d+\b", replace, answer)
