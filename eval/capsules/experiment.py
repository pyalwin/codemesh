#!/usr/bin/env python3
"""Compare raw Python source, versioned capsules, and a compact notation.

This benchmark intentionally fixes the relevant code units for each question.
It measures representation quality and size without mixing in retrieval quality.
Python 3.11+ is required to parse the pinned Pydantic revision.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import time
from typing import Any

from capsule_v2 import extract_evidence, format_v2
from capsule_ids import expand_citations, format_v2_ids


HERE = Path(__file__).resolve().parent
DEFAULT_TASKS = HERE / "pydantic_tasks.json"
DEFAULT_OUT = HERE.parent / "results" / "capsules-pydantic"
MODES = ("raw", "capsule", "compact", "v2", "v2_ids")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def repo_commit(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def parse_unit_id(unit_id: str) -> tuple[str, str]:
    path, sep, qualname = unit_id.partition("::")
    if not sep or not path or not qualname or Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError(f"Invalid unit ID: {unit_id}")
    return path, qualname


def assigned_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


def select_node(tree: ast.AST, qualname: str) -> ast.AST:
    parts = qualname.split(".")
    body = getattr(tree, "body", [])
    node = None
    for part in parts:
        matches = [
            n for n in body
            if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part)
            or assigned_name(n) == part
        ]
        if not matches:
            raise ValueError(f"Symbol not found: {qualname}")
        # An overload group ends with the implementation; use that body.
        node = matches[-1]
        body = node.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else []
    assert node is not None
    return node


def first_doc_sentence(node: ast.AST) -> str:
    if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return "No source documentation."
    doc = ast.get_docstring(node) or ""
    for line in doc.splitlines():
        line = line.strip()
        if not line or line.startswith(("!!!", "[", "```", "#")):
            continue
        if line.endswith(":") and len(line.split()) < 5:
            continue
        return re.split(r"(?<=[.!?])\s+", line, maxsplit=1)[0]
    return "No source documentation."


def normalize_summary(summary: str) -> str:
    summary = re.sub(r"^\s*#{1,3}\s*Summary\s*", "", summary, flags=re.IGNORECASE)
    return " ".join(summary.split())


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def unit_facts(node: ast.AST) -> tuple[list[str], list[str]]:
    calls: Counter[str] = Counter()
    raises: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = call_name(child.func)
            if name:
                calls[name] += 1
        elif isinstance(child, ast.Raise) and child.exc:
            exc = child.exc.func if isinstance(child.exc, ast.Call) else child.exc
            name = call_name(exc)
            if name:
                raises.add(name)
    return [name for name, _ in calls.most_common(8)], sorted(raises)


def extract_unit(repo: Path, unit_id: str) -> dict[str, Any]:
    relative_path, qualname = parse_unit_id(unit_id)
    path = (repo / relative_path).resolve()
    if not path.is_relative_to(repo.resolve()):
        raise ValueError(f"Path escapes repository: {relative_path}")
    source = path.read_text(encoding="utf-8")
    node = select_node(ast.parse(source, filename=relative_path), qualname)
    lines = source.splitlines(keepends=True)
    start = min((d.lineno for d in getattr(node, "decorator_list", [])), default=node.lineno)
    code = "".join(lines[start - 1:node.end_lineno])
    if isinstance(node, ast.ClassDef):
        signature = f"class {qualname}"
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        signature = f"{qualname}({ast.unparse(node.args)}){ret}"
    else:
        signature = ast.unparse(node)
    calls, raises = unit_facts(node)
    return {
        "id": unit_id,
        "path": relative_path,
        "name": qualname,
        "lines": f"{start}-{node.end_lineno}",
        "source_sha256": sha256(code.encode("utf-8")).hexdigest(),
        "ast_sha256": sha256(ast.dump(node, include_attributes=False).encode("utf-8")).hexdigest(),
        "signature": signature,
        "purpose": first_doc_sentence(node),
        "calls": calls,
        "raises": raises,
        "evidence": extract_evidence(node, source),
        "source": code,
    }


def apply_summaries(units: dict[str, dict[str, Any]], summary_file: Path | None) -> None:
    if not summary_file or not summary_file.exists():
        return
    cached = load_json(summary_file)
    for unit_id, item in cached.items():
        unit = units.get(unit_id)
        if unit and item.get("source_sha256") == unit["source_sha256"]:
            unit["purpose"] = normalize_summary(item["summary"])
            unit["summary_source"] = "cached_model"


def generate_summaries(
    units: dict[str, dict[str, Any]], summary_file: Path, model: str, budget: float, cwd: Path
) -> list[dict[str, Any]]:
    cached = load_json(summary_file) if summary_file.exists() else {}
    runs: list[dict[str, Any]] = []
    for unit_id, unit in units.items():
        existing = cached.get(unit_id)
        if existing and existing.get("source_sha256") == unit["source_sha256"]:
            runs.append({"unit": unit_id, "cached": True, **existing.get("generation", {})})
            continue
        prompt = (
            "Summarize the observable behavior of this Python code unit in at most two "
            "sentences. State its purpose and any important delegation or side effect. "
            "Use only the supplied source. Do not repeat its signature or invent behavior.\n\n"
            f"{unit_id}\n```python\n{unit['source'].rstrip()}\n```"
        )
        result = run_claude(prompt, model, budget, cwd)
        generation = {k: v for k, v in result.items() if k != "answer"}
        runs.append({"unit": unit_id, "cached": False, **generation})
        if result.get("error") or not result.get("answer"):
            raise RuntimeError(f"Summary generation failed for {unit_id}: {result.get('error') or 'empty response'}")
        cached[unit_id] = {
            "source_sha256": unit["source_sha256"],
            "summary": normalize_summary(result["answer"]),
            "model": model,
            "generation": generation,
        }
        summary_file.write_text(json.dumps(cached, indent=2) + "\n", encoding="utf-8")
    apply_summaries(units, summary_file)
    return runs


def format_units(mode: str, selected: list[dict[str, Any]], question: str = "") -> str:
    if mode == "raw":
        return "\n\n".join(
            f"### {u['id']} ({u['path']}:{u['lines']}, sha256:{u['source_sha256'][:12]})\n"
            f"```python\n{u['source'].rstrip()}\n```" for u in selected
        )
    if mode == "capsule":
        return "\n\n".join(
            f"### {u['id']}\n"
            f"Version: sha256:{u['source_sha256'][:12]}\n"
            f"Source: {u['path']}:{u['lines']}\n"
            f"Declaration: {u['signature']}\n"
            f"Purpose: {u['purpose']}\n"
            f"Calls seen in body: {', '.join(u['calls']) or 'none'}\n"
            f"Raises seen in body: {', '.join(u['raises']) or 'none'}"
            for u in selected
        )
    if mode == "compact":
        header = (
            "Notation: U=unit; V=source SHA-256 prefix; S=declaration; "
            "P=purpose; C=calls seen in body; X=raises seen in body. "
            "C and X are syntactic observations, not complete runtime behavior."
        )
        rows = [
            f"U{i} {u['path']}:{u['lines']} V:{u['source_sha256'][:12]} "
            f"S:{u['signature']} P:{u['purpose']} "
            f"C:{','.join(u['calls']) or '-'} X:{','.join(u['raises']) or '-'}"
            for i, u in enumerate(selected, 1)
        ]
        return header + "\n" + "\n".join(rows)
    if mode == "v2":
        return format_v2(selected, question)
    if mode == "v2_ids":
        return format_v2_ids(selected, question)[0]
    raise ValueError(f"Unknown mode: {mode}")


def make_prompt(task: dict[str, Any], mode: str, units: dict[str, dict[str, Any]]) -> str:
    selected = [units[unit_id] for unit_id in task["units"]]
    context = format_units(mode, selected, task["question"])
    source_instruction = (
        "If a requested detail is not supported by source documentation, call evidence, or assignment evidence, "
        "respond only with NEED_SOURCE: followed by the relevant supplied unit ID(s). "
        "Do not infer argument bindings or branch behavior from the overview. "
        "Address every part of the question. "
        if mode in ("v2", "v2_ids") else
        "If the context cannot establish a detail, say so rather than guessing. "
    )
    citation_instruction = (
        "Cite supplied E IDs for every key claim; the server resolves them to file and line. "
        "If source is needed, request its U ID. "
        if mode == "v2_ids" else
        "Cite the supplied file and line range for every key claim. "
    )
    return (
        "Answer the question using only the supplied Pydantic code context. "
        + citation_instruction
        + source_instruction +
        "Answer in at most 120 words.\n\n"
        f"Question: {task['question']}\n\nContext:\n{context}\n"
    )


def proxy_tokens(prompt: str) -> int | None:
    try:
        import tiktoken  # type: ignore[import-not-found]
    except ImportError:
        return None
    return len(tiktoken.get_encoding("o200k_base").encode(prompt))


def run_claude(prompt: str, model: str, budget: float, cwd: Path) -> dict[str, Any]:
    cmd = [
        "claude", "--print", "--model", model, "--tools", "",
        "--no-session-persistence", "--output-format", "json",
        "--max-budget-usd", str(budget),
        "--setting-sources", "",
        "--system-prompt", "Answer only from the supplied context. Do not use tools.",
    ]
    start = time.monotonic()
    try:
        completed = subprocess.run(
            cmd, input=prompt, text=True, capture_output=True, cwd=cwd, timeout=180
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc), "elapsed_ms": int((time.monotonic() - start) * 1000)}
    elapsed = int((time.monotonic() - start) * 1000)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"error": completed.stderr.strip() or completed.stdout[:500], "elapsed_ms": elapsed}
    usage = payload.get("usage") or {}
    input_tokens = sum(
        usage.get(key, 0) or 0
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    return {
        "answer": payload.get("result", ""),
        "error": payload.get("result", "") if payload.get("is_error") else None,
        "elapsed_ms": elapsed,
        "input_tokens": input_tokens,
        "uncached_input_tokens": usage.get("input_tokens", 0) or 0,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens"),
        "thinking_tokens": (usage.get("output_tokens_details") or {}).get("thinking_tokens"),
        "cost_usd": payload.get("total_cost_usd", payload.get("cost_usd")),
        "model_ids": list((payload.get("modelUsage") or {}).keys()),
        "exit_code": completed.returncode,
    }


def lexical_checks(answer: str, task: dict[str, Any]) -> list[dict[str, Any]]:
    """Flag required strings for review; this is not a correctness score."""
    normalized = answer.casefold()
    return [
        {
            "label": check["label"],
            "hit": all(fragment.casefold() in normalized for fragment in check["all_of"]),
        }
        for check in task.get("checks", [])
    ]


def requested_source_units(answer: str, task: dict[str, Any], alias_map: dict[str, Any] | None = None) -> list[str]:
    if not re.search(r"NEED_SOURCE\s*:", answer, flags=re.IGNORECASE) and not re.search(
        r"cannot establish|cannot determine|not enough context|context does not|not shown in (?:the )?(?:supplied )?evidence",
        answer, flags=re.IGNORECASE,
    ):
        return []
    if alias_map:
        requested = [alias_map["units"][alias]["unit_id"] for alias in re.findall(r"\bU\d+\b", answer)
                     if alias in alias_map["units"]]
        if requested:
            return list(dict.fromkeys(requested))
    chosen = [unit_id for unit_id in task["units"] if unit_id in answer]
    if not chosen:
        chosen = [unit_id for unit_id in task["units"] if parse_unit_id(unit_id)[0] in answer]
    return chosen or list(task["units"])


def numbered_source_context(selected: list[dict[str, Any]]) -> str:
    blocks = []
    for unit in selected:
        start = int(unit["lines"].split("-", 1)[0])
        numbered = "\n".join(
            f"{start + index:>5}: {line}"
            for index, line in enumerate(unit["source"].rstrip().splitlines())
        )
        blocks.append(f"### {unit['id']} ({unit['path']}:{unit['lines']})\n```python\n{numbered}\n```")
    return "\n\n".join(blocks)


def run_with_fallback(
    prompt: str, task: dict[str, Any], units: dict[str, dict[str, Any]],
    model: str, budget: float, cwd: Path, alias_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    first = run_claude(prompt, model, budget, cwd)
    if first.get("error"):
        return {**first, "fallback_used": False, "stages": [first]}
    requested = requested_source_units(first.get("answer") or "", task, alias_map)
    if not requested:
        if alias_map:
            first["answer_with_ids"] = first.get("answer") or ""
            first["answer"] = expand_citations(first["answer_with_ids"], alias_map)
        return {**first, "fallback_used": False, "stages": [first]}
    fallback_context = numbered_source_context([units[unit_id] for unit_id in requested])
    fallback_prompt = (
        "Answer the original question using this requested source. Cite precise source lines. "
        "If the source does not establish a detail, say so. Answer in at most 120 words.\n\n"
        f"Question: {task['question']}\n\nContext:\n{fallback_context}\n"
    )
    second = run_claude(fallback_prompt, model, budget, cwd)
    return {
        **second,
        "fallback_used": True,
        "fallback_units": requested,
        "fallback_prompt_bytes": len(fallback_prompt.encode("utf-8")),
        "fallback_prompt_proxy_tokens_o200k": proxy_tokens(fallback_prompt),
        "input_tokens": (first.get("input_tokens") or 0) + (second.get("input_tokens") or 0),
        "uncached_input_tokens": (first.get("uncached_input_tokens") or 0) + (second.get("uncached_input_tokens") or 0),
        "cache_creation_input_tokens": (first.get("cache_creation_input_tokens") or 0) + (second.get("cache_creation_input_tokens") or 0),
        "cache_read_input_tokens": (first.get("cache_read_input_tokens") or 0) + (second.get("cache_read_input_tokens") or 0),
        "output_tokens": (first.get("output_tokens") or 0) + (second.get("output_tokens") or 0),
        "cost_usd": (first.get("cost_usd") or 0) + (second.get("cost_usd") or 0),
        "elapsed_ms": (first.get("elapsed_ms") or 0) + (second.get("elapsed_ms") or 0),
        "stages": [first, second],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Pinned Pydantic checkout")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--summary-file", type=Path, help="Optional cached summaries keyed by unit ID and source hash")
    parser.add_argument("--summarize", action="store_true", help="Generate and cache one model summary per changed code unit")
    parser.add_argument("--run", action="store_true", help="Run stateless Claude comparisons")
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--budget-usd", type=float, default=0.20, help="Maximum per model call")
    parser.add_argument("--task", action="append", help="Only prepare/run this task ID (repeatable)")
    parser.add_argument("--repeats", type=int, default=1, help="Independent stateless calls per task and mode")
    parser.add_argument("--seed", type=int, default=42, help="Seed used to shuffle call order")
    parser.add_argument("--modes", default="raw,capsule,compact", help="Comma-separated modes: raw,capsule,compact,v2,v2_ids")
    args = parser.parse_args()

    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ is required for the pinned Pydantic source")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    modes = tuple(args.modes.split(","))
    if not modes or len(set(modes)) != len(modes) or any(mode not in MODES for mode in modes):
        parser.error(f"--modes must list distinct values from {','.join(MODES)}")
    config = load_json(args.tasks)
    repo = args.repo.resolve()
    commit = repo_commit(repo)
    if commit != config["commit"]:
        parser.error(f"Expected commit {config['commit']}, found {commit}")
    tasks = [t for t in config["tasks"] if not args.task or t["id"] in args.task]
    if not tasks:
        parser.error("No matching tasks")

    unit_ids = list(dict.fromkeys(unit for task in tasks for unit in task["units"]))
    units = {unit_id: extract_unit(repo, unit_id) for unit_id in unit_ids}
    args.out.mkdir(parents=True, exist_ok=True)
    summary_file = args.summary_file or args.out / "summaries.json"
    summary_runs: list[dict[str, Any]] = []
    if args.summarize:
        try:
            summary_runs = generate_summaries(units, summary_file, args.model, args.budget_usd, args.out)
        except RuntimeError as exc:
            parser.error(str(exc))
    else:
        apply_summaries(units, summary_file)
    (args.out / "capsules.json").write_text(
        json.dumps([{k: v for k, v in u.items() if k != "source"} for u in units.values()], indent=2) + "\n",
        encoding="utf-8",
    )

    prompts: dict[tuple[str, str], str] = {}
    alias_maps: dict[str, dict[str, Any]] = {}
    rows = []
    for task in tasks:
        for mode in modes:
            prompt = make_prompt(task, mode, units)
            if mode == "v2_ids":
                selected = [units[unit_id] for unit_id in task["units"]]
                _, alias_map = format_v2_ids(selected, task["question"])
                alias_maps[task["id"]] = alias_map
                (args.out / f"{task['id']}.v2_ids.map.json").write_text(
                    json.dumps(alias_map, indent=2) + "\n", encoding="utf-8"
                )
            prompts[task["id"], mode] = prompt
            (args.out / f"{task['id']}.{mode}.txt").write_text(prompt, encoding="utf-8")
            for repeat in range(1, args.repeats + 1):
                rows.append({
                    "task": task["id"], "mode": mode, "repeat": repeat,
                    "category": task.get("category"), "path_group": task.get("path_group"),
                    "depth": task.get("depth"), "frame": task.get("frame"),
                    "prompt_bytes": len(prompt.encode("utf-8")),
                    "prompt_proxy_tokens_o200k": proxy_tokens(prompt),
                })
    if args.run:
        task_by_id = {task["id"]: task for task in tasks}
        call_order = list(range(len(rows)))
        random.Random(args.seed).shuffle(call_order)
        checkpoint = args.out / "runs.jsonl"
        checkpoint.write_text("", encoding="utf-8")
        for ordinal, index in enumerate(call_order, 1):
            row = rows[index]
            print(f"[{ordinal}/{len(rows)}] {row['task']} {row['mode']} repeat {row['repeat']}", flush=True)
            result = (
                run_with_fallback(
                    prompts[row["task"], row["mode"]], task_by_id[row["task"]], units,
                    args.model, args.budget_usd, args.out,
                    alias_maps.get(row["task"]) if row["mode"] == "v2_ids" else None,
                )
                if row["mode"] in ("v2", "v2_ids") else
                run_claude(prompts[row["task"], row["mode"]], args.model, args.budget_usd, args.out)
            )
            row["model_result"] = result
            row["lexical_checks"] = lexical_checks(result.get("answer") or "", task_by_id[row["task"]])
            with checkpoint.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
    totals = {
        mode: {
            "prompt_bytes": sum(r["prompt_bytes"] for r in rows if r["mode"] == mode),
            "prompt_proxy_tokens_o200k": sum(
                r["prompt_proxy_tokens_o200k"] or 0 for r in rows if r["mode"] == mode
            ) if proxy_tokens("test") is not None else None,
            "actual_input_tokens": sum(r["model_result"].get("input_tokens") or 0 for r in rows if r["mode"] == mode) if args.run else None,
            "uncached_input_tokens": sum(r["model_result"].get("uncached_input_tokens") or 0 for r in rows if r["mode"] == mode) if args.run else None,
            "cache_creation_input_tokens": sum(r["model_result"].get("cache_creation_input_tokens") or 0 for r in rows if r["mode"] == mode) if args.run else None,
            "cache_read_input_tokens": sum(r["model_result"].get("cache_read_input_tokens") or 0 for r in rows if r["mode"] == mode) if args.run else None,
            "actual_output_tokens": sum(r["model_result"].get("output_tokens") or 0 for r in rows if r["mode"] == mode) if args.run else None,
            "model_cost_usd": sum(r["model_result"].get("cost_usd") or 0 for r in rows if r["mode"] == mode) if args.run else None,
            "model_elapsed_ms": sum(r["model_result"].get("elapsed_ms") or 0 for r in rows if r["mode"] == mode) if args.run else None,
            "source_fallbacks": sum(bool(r["model_result"].get("fallback_used")) for r in rows if r["mode"] == mode) if args.run else None,
        }
        for mode in modes
    }
    report = {
        "repository": config["repository"], "commit": commit,
        "model": args.model if args.run else None,
        "repeats": args.repeats, "seed": args.seed,
        "modes": modes,
        "quality_metric": "Lexical checks are review flags, not correctness scores",
        "token_metric": "o200k_base proxy, not the target model tokenizer" if proxy_tokens("test") is not None else "unavailable: install tiktoken",
        "summary_source": "version-matched model cache where present; source docstring otherwise",
        "summary_generation": summary_runs,
        "summary_generation_cost_usd": sum(r.get("cost_usd") or 0 for r in summary_runs if not r.get("cached")) if summary_runs else None,
        "summary_cache_total_generation_cost_usd": sum(r.get("cost_usd") or 0 for r in summary_runs) if summary_runs else None,
        "totals": totals,
        "tasks": [{k: v for k, v in t.items()} for t in tasks],
        "results": rows,
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.run:
        review = ["# Manual quality review", "", "Score factual correctness against each rubric item. Mark unsupported claims and missing details.", ""]
        for task in tasks:
            review.extend([f"## {task['id']}", "", task["question"], "", "Rubric:", ""])
            review.extend(f"- {fact}" for fact in task["rubric"])
            for row in [r for r in rows if r["task"] == task["id"]]:
                review.extend([
                    "", f"### {row['mode']} repeat {row['repeat']}", "",
                    f"Lexical flags: {sum(c['hit'] for c in row['lexical_checks'])}/{len(row['lexical_checks'])} (manual review required)", "",
                    "Score: /10", "",
                    row["model_result"].get("answer") or row["model_result"].get("error") or "(empty)",
                ])
            review.append("")
        (args.out / "review.md").write_text("\n".join(review), encoding="utf-8")
    for task in tasks:
        task_rows = [r for r in rows if r["task"] == task["id"] and r["repeat"] == 1]
        print(task["id"])
        for row in task_rows:
            proxy = row["prompt_proxy_tokens_o200k"]
            print(f"  {row['mode']:<8} {row['prompt_bytes']:>6} bytes  {str(proxy):>5} proxy tokens")
    if args.run:
        errors = [r for r in rows if r["model_result"].get("error")]
        print(f"Calls: {len(rows)}; errors: {len(errors)}")
        for row in errors:
            print(f"  ERROR {row['task']} {row['mode']} repeat {row['repeat']}: {row['model_result']['error']}")
    print(f"Report: {args.out / 'report.json'}")
    return 1 if args.run and any(r["model_result"].get("error") for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
