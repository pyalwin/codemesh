#!/usr/bin/env python3.11
"""Held-out Pydantic repair evaluation with investigate, handoff, edit and pytest.

Each run starts from the same pinned commit plus one seeded regression. Agents
see a symptom and a visible test. Hidden tests are run only by the harness.
All agent file access is through the structured, simulated tool protocol.
"""

from __future__ import annotations

import argparse
import ast
import difflib
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from typing import Any

from grep_router import ReadRouter, native_grep, native_read, route_grep

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval" / "capsules"))
from capsule_v2 import extract_evidence, format_v2  # noqa: E402
from experiment import first_doc_sentence  # noqa: E402

MODES = ("filesystem", "capsule_v2", "routed", "combined")
CAPSULE_MODES = ("capsule_v2", "combined")
ROUTED_MODES = ("routed", "combined")
SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["grep", "read", "read_raw", "edit", "test", "answer"]},
        "query": {"type": "string"}, "path": {"type": "string"},
        "start": {"type": "integer"}, "limit": {"type": "integer"},
        "old": {"type": "string"}, "new": {"type": "string"},
        "answer": {"type": "string"},
    },
    "required": ["action", "answer"], "additionalProperties": False,
}


def schema_for_mode(mode: str) -> str:
    schema = json.loads(json.dumps(SCHEMA))
    if mode not in CAPSULE_MODES:
        schema["properties"]["action"]["enum"].remove("read_raw")
    return json.dumps(schema, separators=(",", ":"))


def system_prompt(mode: str) -> str:
    tools = "grep(query), read(path,start,limit), edit(path,old,new), test, answer"
    if mode in CAPSULE_MODES:
        tools += ", read_raw(path,start,limit)"
    context = (
        "Read may provide a concise source-backed capsule. Its overview is navigation, "
        "and its line-cited evidence is syntactic. Use read_raw for exact source before editing. "
        if mode in CAPSULE_MODES else
        "Read returns numbered source lines. "
    )
    if mode in ROUTED_MODES:
        context += "Grep may annotate fresh indexed symbols; raw reads may include verified tails. "
    return (
        "You are repairing one regression in a pinned Python repository. "
        "Use only tool observations and your own verified handoff memory for code facts. "
        "Choose one JSON action per response. Available actions: " + tools + ". " + context +
        "All paths are relative to the Pydantic checkout; read requires a file path, "
        "not a directory or absolute path. The visible test is eval_visible_test.py. "
        "Grep searches literal source text, not regex, and does not search filenames; "
        "search one term per call. "
        "For tool actions leave answer empty. Do not edit during investigation phases. "
        "Use an exact old string for edits. The visible test can be run with test. "
        "The hidden acceptance tests are unavailable to you."
    )


def model_call(message: str, session_id: str, mode: str, *, resume: bool,
               structured: bool, out: Path, budget_usd: float) -> dict[str, Any]:
    cmd = [
        "claude", "--print", "--model", "haiku", "--tools", "",
        "--output-format", "json", "--max-budget-usd", str(budget_usd),
        "--setting-sources", "", "--effort", "low",
        "--system-prompt", system_prompt(mode),
    ]
    cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
    if structured:
        cmd += ["--json-schema", schema_for_mode(mode)]
    started = time.monotonic()
    try:
        process = subprocess.run(cmd, input=message, text=True, capture_output=True,
                                 cwd=out, timeout=180)
        payload = json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"error": str(exc), "elapsed_ms": int((time.monotonic() - started) * 1000)}
    usage = payload.get("usage") or {}
    result = {
        "answer": payload.get("result") or "",
        "decision": payload.get("structured_output") if structured else None,
        "error": payload.get("result") if payload.get("is_error") else None,
        "session_id": payload.get("session_id"),
        "input_tokens": sum(usage.get(key, 0) or 0 for key in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "uncached_input_tokens": usage.get("input_tokens", 0) or 0,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "cost_usd": payload.get("total_cost_usd", 0) or 0,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "model_ids": list((payload.get("modelUsage") or {}).keys()),
        "exit_code": process.returncode,
    }
    if result["session_id"] != session_id:
        result["error"] = result["error"] or "Session ID changed unexpectedly"
    if structured and not isinstance(result["decision"], dict):
        result["error"] = result["error"] or "Missing structured_output"
    return result


def safe_file(repo: Path, relative: str) -> Path:
    path = (repo / relative.removeprefix("./")).resolve()
    if not path.is_relative_to(repo.resolve()) or not path.is_file():
        raise ValueError("Path must be a repository-relative file, e.g. eval_visible_test.py or pydantic/module.py")
    return path


def _candidate_functions(tree: ast.AST) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    found: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]] = []

    def visit(node: ast.AST, parents: tuple[str, ...] = ()) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, parents + (child.name,))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((child, ".".join(parents + (child.name,))))
                visit(child, parents + (child.name,))
            else:
                visit(child, parents)

    visit(tree)
    return found


def capsule_read(path: str, start: int, limit: int, repo: Path,
                 question: str) -> tuple[str, dict[str, Any]]:
    file = safe_file(repo, path)
    source = file.read_text(errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return native_read(path, start, limit, repo), {"backend": "native", "reason": "syntax_error"}
    end = start + limit - 1
    candidates = [(node, name) for node, name in _candidate_functions(tree)
                  if node.lineno <= end and node.end_lineno >= start]
    enclosing = [(node, name) for node, name in candidates if node.lineno <= start <= node.end_lineno]
    if enclosing:
        node, name = min(enclosing, key=lambda item: item[0].end_lineno - item[0].lineno)
    else:
        starts = [(node, name) for node, name in candidates if start <= node.lineno <= end]
        if len(starts) != 1:
            return native_read(path, start, limit, repo), {"backend": "native", "reason": "ambiguous_span"}
        node, name = starts[0]
    lines = source.splitlines(keepends=True)
    code = "".join(lines[node.lineno - 1:node.end_lineno])
    unit = {
        "id": f"{path}::{name}", "path": path, "name": name,
        "lines": f"{node.lineno}-{node.end_lineno}",
        "source_sha256": sha256(code.encode()).hexdigest(),
        "signature": f"{name}({ast.unparse(node.args)})" +
                     (f" -> {ast.unparse(node.returns)}" if node.returns else ""),
        "purpose": first_doc_sentence(node),
        "evidence": extract_evidence(node, source),
    }
    formatted = format_v2([unit], question).replace(
        "Overview (generated; verify against evidence):",
        "Overview (source docstring; verify against evidence):",
    )
    return formatted, {
        "backend": "capsule_v2", "symbol": unit["id"], "source_sha256": unit["source_sha256"]}


def run_pytest(repo: Path, python: Path, test_path: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    started = time.monotonic()
    try:
        process = subprocess.run([str(python), "-m", "pytest", "-q", "-o", "addopts=", str(test_path)],
                                 cwd=repo, env=env, text=True, capture_output=True, timeout=90)
        return {"passed": process.returncode == 0, "exit_code": process.returncode,
                "output": (process.stdout + process.stderr)[-3000:],
                "elapsed_ms": int((time.monotonic() - started) * 1000)}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"passed": False, "error": str(exc),
                "elapsed_ms": int((time.monotonic() - started) * 1000)}


def mutated_content(repo: Path, task: dict[str, Any]) -> bytes:
    original = safe_file(repo, task["path"]).read_text()
    occurrence = task.get("occurrence", 1)
    if not isinstance(occurrence, int) or occurrence < 1 or original.count(task["old"]) < occurrence:
        raise ValueError(f"Mutation anchor occurrence is missing: {task['id']}")
    parts = original.split(task["old"])
    return (task["old"].join(parts[:occurrence]) + task["mutant"] +
            task["old"].join(parts[occurrence:])).encode()


def prepare_index(base: Path, task: dict[str, Any], out: Path) -> Path:
    destination = out / "indexes" / f"{task['id']}.db"
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = base / ".codemesh" / "codemesh.db"
    if not source.is_file():
        raise ValueError(f"Missing Codemesh index: {source}")
    try:
        subprocess.run(["cp", "-c", str(source), str(destination)], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        shutil.copy2(source, destination)
    digest = sha256(mutated_content(base, task)).hexdigest()
    db = sqlite3.connect(destination)
    try:
        cursor = db.execute("UPDATE nodes SET hash=? WHERE id=? AND type='file'",
                            (digest, f"file:{task['path']}"))
        if cursor.rowcount != 1:
            raise ValueError(f"Target file absent from index: {task['path']}")
        db.commit()
    finally:
        db.close()
    return destination


def create_worktree(base: Path, task: dict[str, Any], path: Path,
                    index: Path | None) -> None:
    content = mutated_content(base, task)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(base), "worktree", "add", "--detach", str(path), "HEAD"],
                   check=True, capture_output=True, text=True)
    try:
        safe_file(path, task["path"]).write_bytes(content)
        (path / "eval_visible_test.py").write_text(task["visible_test"])
        if index is not None:
            directory = path / ".codemesh"
            directory.mkdir()
            (directory / "codemesh.db").symlink_to(index)
    except Exception:
        remove_worktree(base, path)
        raise


def remove_worktree(base: Path, path: Path) -> None:
    if path.exists():
        subprocess.run(["git", "-C", str(base), "worktree", "remove", "--force", str(path)],
                       check=True, capture_output=True, text=True)


def validate_tasks(base: Path, tasks: list[dict[str, Any]], out: Path,
                   python: Path) -> list[dict[str, Any]]:
    checks = []
    for task in tasks:
        hidden = ROOT / task["hidden_test"]
        if not hidden.is_file() or not hidden.resolve().is_relative_to((ROOT / "eval" / "edit_tasks" / "hidden").resolve()):
            raise ValueError(f"Hidden test missing or outside fixture directory: {task['id']}")
        pristine = run_pytest(base, python, hidden)
        path = out / "worktrees" / f"validate-{task['id']}"
        create_worktree(base, task, path, None)
        try:
            visible = run_pytest(path, python, path / "eval_visible_test.py")
            mutant = run_pytest(path, python, hidden)
        finally:
            remove_worktree(base, path)
        row = {"task": task["id"], "pristine_pass": pristine["passed"],
               "mutant_visible_fails": visible.get("exit_code") == 1,
               "mutant_hidden_fails": mutant.get("exit_code") == 1,
               "pristine": pristine, "mutant_visible": visible, "mutant_hidden": mutant}
        checks.append(row)
        if not all((row["pristine_pass"], row["mutant_visible_fails"], row["mutant_hidden_fails"])):
            raise ValueError(f"Invalid task fixture: {task['id']}: {row}")
    return checks


def tool_call(decision: dict[str, Any], mode: str, repo: Path,
              task: dict[str, Any], question: str, read_router: ReadRouter,
              python: Path, edit_count: int, test_count: int) -> tuple[str, str, dict[str, Any]]:
    action = decision.get("action")
    if action == "grep":
        query = (decision.get("query") or "").strip()
        if not query or len(query) > 120:
            raise ValueError("grep requires a literal query of at most 120 characters")
        if mode in ROUTED_MODES:
            observation, route = route_grep(query, repo)
        else:
            observation, route = native_grep(query, repo), {"backend": "native"}
        if observation == "No matches" and "|" in query:
            observation += "\nGrep is literal: | is a character, not alternation. Search terms separately."
        return action, observation, route
    if action in ("read", "read_raw"):
        if action == "read_raw" and mode not in CAPSULE_MODES:
            raise ValueError("read_raw is unavailable in this mode")
        path = (decision.get("path") or "").removeprefix("./")
        start = decision.get("start") or 1
        limit = decision.get("limit") or 60
        if not isinstance(start, int) or not isinstance(limit, int) or start < 1 or not 1 <= limit <= 120:
            raise ValueError("read requires start >= 1 and 1 <= limit <= 120")
        safe_file(repo, path)
        if mode in CAPSULE_MODES and action == "read":
            observation, route = capsule_read(path, start, limit, repo, question)
        elif mode in ROUTED_MODES:
            observation, route = read_router.route_read(path, start, limit, repo)
        else:
            observation, route = native_read(path, start, limit, repo), {"backend": "native"}
        return action, observation, route
    if action == "edit":
        if edit_count >= 3:
            raise ValueError("Maximum three edit attempts")
        path = (decision.get("path") or "").removeprefix("./")
        old, new = decision.get("old"), decision.get("new")
        if not path.startswith("pydantic/") or not path.endswith(".py"):
            raise ValueError("Edits must target a Python source file under pydantic/")
        if not isinstance(old, str) or not isinstance(new, str) or not old or len(old) > 4000 or len(new) > 4000:
            raise ValueError("edit requires nonempty old and bounded new strings")
        file = safe_file(repo, path)
        source = file.read_text()
        if source.count(old) != 1:
            raise ValueError(f"old string must occur exactly once; found {source.count(old)}")
        file.write_text(source.replace(old, new, 1))
        hidden = run_pytest(repo, python, ROOT / task["hidden_test"])
        # The hidden result is recorded as private route metadata, never sent
        # to the model; only the ordinary edit confirmation is observed.
        return action, f"Edited {path}; exact old string replaced once.", {
            "backend": "filesystem", "hidden_pass": hidden["passed"],
            "hidden_exit_code": hidden.get("exit_code"), "hidden_elapsed_ms": hidden["elapsed_ms"]}
    if action == "test":
        if test_count >= 3:
            raise ValueError("Maximum three visible test calls")
        result = run_pytest(repo, python, repo / "eval_visible_test.py")
        return action, result["output"], {"backend": "pytest", "passed": result["passed"],
                                          "exit_code": result.get("exit_code"),
                                          "elapsed_ms": result["elapsed_ms"]}
    raise ValueError("Unknown action")


def phase_prompt(task: dict[str, Any], phase: int, memory: str) -> str:
    if phase == 0:
        return (
            f"Repair task: {task['symptom']}\n"
            "A minimal visible regression test is in the repo-relative file eval_visible_test.py. "
            "Investigate the source and identify the likely cause. Read at least one "
            "source span before answering. Do not edit yet."
        )
    if phase == 1:
        return (
            f"Second investigation phase: {task['impact_question']}\n"
            "Check source as needed. Do not edit yet."
        )
    return (
        f"Compaction memory from the previous session:\n{memory}\n\n"
        f"Apply a minimal fix for this regression: {task['symptom']}\n"
        "Use read_raw for exact source if a capsule was insufficient. You may run "
        "the visible test. Make at least one edit, then answer with a concise patch summary."
    )


def run_trajectory(base: Path, task: dict[str, Any], mode: str, repeat: int,
                   out: Path, python: Path, index: Path, max_turns: int,
                   budget_usd: float) -> dict[str, Any]:
    worktree = out / "worktrees" / f"{task['id']}-{mode}-{repeat}"
    create_worktree(base, task, worktree, index)
    session_id = str(uuid.uuid4())
    resume = False
    read_router = ReadRouter()
    calls: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    memory = ""
    status = "complete"
    edits = 0
    tests = 0
    first_patch_pass: bool | None = None
    read_keys: dict[tuple[str, int, int, str], int] = {}
    repeated_source_lines = 0
    repeated_read_bytes = 0
    tool_elapsed_ms = 0
    initial_sources: dict[str, str] = {task["path"]: safe_file(worktree, task["path"]).read_text()}
    try:
        for phase in range(3):
            if phase == 2:
                instruction = (
                    "Write a handoff memory for a fresh coding agent in at most 120 words. "
                    "Keep the observed cause, exact relevant source locations, surrounding "
                    "behavior to preserve, and unresolved facts. Do not patch yet."
                )
                result = model_call(instruction, session_id, mode, resume=True,
                                    structured=False, out=out, budget_usd=budget_usd)
                calls.append({"kind": "compaction", "phase": phase, **result})
                if result.get("error"):
                    status = "compaction_error"
                    break
                memory = result["answer"]
                session_id = str(uuid.uuid4())
                resume = False
                read_router.reset_session()
            question = phase_prompt(task, phase, memory)
            record: dict[str, Any] = {"phase": phase, "question": question,
                                      "actions": [], "answer": None}
            message = question
            phase_reads = 0
            for turn in range(1, max_turns + 1):
                result = model_call(message, session_id, mode, resume=resume,
                                    structured=True, out=out, budget_usd=budget_usd)
                calls.append({"kind": "decision", "phase": phase, "turn": turn,
                              "message": message, **result})
                resume = True
                if result.get("error"):
                    status = "model_error"
                    break
                decision = result["decision"]
                action = decision.get("action")
                if action == "answer":
                    if phase == 0 and phase_reads == 0:
                        message = "Tool protocol: read at least one source span before answering."
                        record["actions"].append({"action": "premature_answer"})
                        continue
                    if phase == 2 and edits == 0:
                        message = "Tool protocol: apply a source edit before finishing."
                        record["actions"].append({"action": "premature_answer"})
                        continue
                    record["answer"] = decision.get("answer") or ""
                    record["actions"].append({"action": "answer"})
                    break
                if phase < 2 and action in ("edit", "test"):
                    message = "Tool protocol: edit and test are available in the patch phase."
                    record["actions"].append({"action": "early_action", "input": decision})
                    continue
                started = time.monotonic()
                try:
                    if action == "edit":
                        edit_path = (decision.get("path") or "").removeprefix("./")
                        if edit_path.startswith("pydantic/") and edit_path not in initial_sources:
                            initial_sources[edit_path] = safe_file(worktree, edit_path).read_text()
                    actual, observation, route = tool_call(
                        decision, mode, worktree, task, question, read_router, python, edits, tests)
                    error = None
                except (OSError, ValueError, subprocess.TimeoutExpired, sqlite3.Error) as exc:
                    actual, observation, route, error = str(action), f"Tool error: {exc}", {}, str(exc)
                tool_elapsed_ms += int((time.monotonic() - started) * 1000)
                if not error and action in ("read", "read_raw"):
                    phase_reads += 1
                    path = (decision.get("path") or "").removeprefix("./")
                    start = decision.get("start") or 1
                    limit = decision.get("limit") or 60
                    content = safe_file(worktree, path).read_bytes()
                    end = min(start + limit - 1, len(content.splitlines()))
                    key = (path, start, end, sha256(content).hexdigest())
                    if key in read_keys:
                        repeated_source_lines += max(0, end - start + 1)
                        repeated_read_bytes += read_keys[key]
                    else:
                        read_keys[key] = len(observation.encode())
                if not error and action == "edit":
                    edits += 1
                    if first_patch_pass is None:
                        first_patch_pass = bool(route["hidden_pass"])
                if not error and action == "test":
                    tests += 1
                record["actions"].append({
                    "action": actual, "input": decision, "observation": observation,
                    "observation_bytes": len(observation.encode()), "route": route, "error": error,
                })
                # Hidden test metadata stays only in the report; never include
                # it in the model's next observation.
                message = (
                    f"Tool result for {actual}:\n{observation}\n\n"
                    "Choose another action or answer from observed evidence."
                )
            else:
                status = "turn_limit"
            phases.append(record)
            if status != "complete" or record["answer"] is None:
                break
        hidden_final = run_pytest(worktree, python, ROOT / task["hidden_test"])
        visible_final = run_pytest(worktree, python, worktree / "eval_visible_test.py")
        diff = "".join(
            "".join(difflib.unified_diff(
                before.splitlines(keepends=True), safe_file(worktree, path).read_text().splitlines(keepends=True),
                fromfile=f"a/{path} (seeded bug)", tofile=f"b/{path} (agent result)",
            )) for path, before in sorted(initial_sources.items())
        )
        patch_path = out / "patches" / f"{task['id']}-{mode}-{repeat}.diff"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(diff)
    finally:
        remove_worktree(base, worktree)
    totals = {key: sum(call.get(key) or 0 for call in calls) for key in (
        "input_tokens", "uncached_input_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens", "output_tokens", "cost_usd", "elapsed_ms")}
    actions = [action for phase in phases for action in phase["actions"]]
    return {
        "task": task["id"], "mode": mode, "repeat": repeat, "status": status,
        "phases": phases, "compaction_memory": memory, "calls": calls,
        "totals": totals, "tool_elapsed_ms": tool_elapsed_ms,
        "tool_calls": sum(action["action"] not in ("answer", "premature_answer") for action in actions),
        "source_reads": sum(action["action"] in ("read", "read_raw") and not action.get("error") for action in actions),
        "read_observation_bytes": sum(action.get("observation_bytes", 0) for action in actions
                                      if action["action"] in ("read", "read_raw")),
        "repeated_source_lines": repeated_source_lines,
        "repeated_read_bytes": repeated_read_bytes,
        "grep_calls": sum(action["action"] == "grep" for action in actions),
        "capsule_reads": sum((action.get("route") or {}).get("backend") == "capsule_v2"
                             for action in actions if action["action"] == "read"),
        "routed_greps": sum((action.get("route") or {}).get("backend") in ("codemesh", "hybrid")
                            for action in actions if action["action"] == "grep"),
        "routed_reads": sum((action.get("route") or {}).get("backend") == "codemesh"
                            for action in actions if action["action"] in ("read", "read_raw")),
        "edits": edits, "visible_test_calls": tests,
        "first_patch_pass": first_patch_pass is True,
        "final_patch_pass": hidden_final["passed"],
        "visible_final_pass": visible_final["passed"],
        "hidden_final": hidden_final, "visible_final": visible_final,
        "patch_path": str(patch_path),
    }


def summarize(rows: list[dict[str, Any]], modes: list[str]) -> dict[str, dict[str, Any]]:
    by_mode = {}
    for mode in modes:
        selected = [row for row in rows if row["mode"] == mode]
        by_mode[mode] = {
            "trajectories": len(selected),
            "complete": sum(row["status"] == "complete" for row in selected),
            "first_patch_pass": sum(row["first_patch_pass"] for row in selected),
            "final_patch_pass": sum(row["final_patch_pass"] for row in selected),
            "visible_final_pass": sum(row["visible_final_pass"] for row in selected),
            **{key: sum(row[key] for row in selected) for key in (
                "tool_calls", "source_reads", "read_observation_bytes",
                "repeated_source_lines", "repeated_read_bytes", "grep_calls",
                "capsule_reads", "routed_greps", "routed_reads", "edits",
                "visible_test_calls", "tool_elapsed_ms")},
            **{key: sum(row["totals"][key] for row in selected) for key in (
                "input_tokens", "uncached_input_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "output_tokens", "cost_usd", "elapsed_ms")},
        }
    return by_mode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=ROOT / "eval" / "edit_workflow_tasks.json")
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "results" / "edit-workflow")
    parser.add_argument("--python", type=Path, default=Path("/tmp/codemesh-edit-eval-venv/bin/python"))
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--budget-usd", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--task", action="append")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.repo = args.repo.resolve()
    args.out = args.out.resolve()
    args.tasks = args.tasks.resolve()
    # Keep the venv symlink path: resolving it loses the virtualenv context.
    args.python = args.python.absolute()
    config = json.loads(args.tasks.read_text())
    commit = subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True).strip()
    if commit != config["commit"]:
        parser.error(f"Target commit mismatch: {commit} != {config['commit']}")
    tasks = [task for task in config["tasks"] if not args.task or task["id"] in args.task]
    modes = args.modes.split(",")
    if (not tasks or not modes or len(set(modes)) != len(modes)
            or any(mode not in MODES for mode in modes) or args.repeats < 1
            or args.max_turns < 1 or args.budget_usd <= 0 or not args.python.is_file()):
        parser.error("Invalid task, mode, repeat, budget, or Python environment")
    args.out.mkdir(parents=True, exist_ok=True)
    checks_path = args.out / "fixture_checks.json"
    if not args.resume or not checks_path.exists():
        checks = validate_tasks(args.repo, tasks, args.out, args.python)
        checks_path.write_text(json.dumps(checks, indent=2) + "\n")
    else:
        checks = json.loads(checks_path.read_text())
    print(f"Validated {len(checks)} pristine-pass/mutant-fail fixtures", flush=True)
    if args.validate_only:
        return 0
    indexes = {task["id"]: prepare_index(args.repo, task, args.out) for task in tasks}
    plan = [(task, mode, repeat) for task in tasks for mode in modes
            for repeat in range(1, args.repeats + 1)]
    random.Random(args.seed).shuffle(plan)
    checkpoint = args.out / "runs.jsonl"
    if args.resume and checkpoint.exists():
        rows = [json.loads(line) for line in checkpoint.read_text().splitlines()]
    else:
        rows = []
        checkpoint.write_text("")
    completed = {(row["task"], row["mode"], row["repeat"]) for row in rows}
    for index, (task, mode, repeat) in enumerate(plan, 1):
        if (task["id"], mode, repeat) in completed:
            continue
        print(f"[{index}/{len(plan)}] {task['id']} {mode} repeat {repeat}", flush=True)
        row = run_trajectory(args.repo, task, mode, repeat, args.out, args.python,
                             indexes[task["id"]], args.max_turns, args.budget_usd)
        rows.append(row)
        with checkpoint.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        print(f"  {row['status']}; first={row['first_patch_pass']} final={row['final_patch_pass']}; "
              f"{row['source_reads']} reads; ${row['totals']['cost_usd']:.6f}", flush=True)
    report = {
        "repository": config["repository"], "commit": commit,
        "model": "haiku", "modes": modes, "repeats": args.repeats,
        "tasks": [task["id"] for task in tasks], "seed": args.seed,
        "per_call_budget_usd": args.budget_usd, "max_turns_per_phase": args.max_turns,
        "fixture_checks": checks, "results": rows,
        "by_mode": summarize(rows, modes),
        "method": (
            "Three symptom-based, seeded regressions on pinned Pydantic. Each run uses a fresh "
            "worktree, two investigation phases, a model-written memory, a fresh post-handoff "
            "session, exact-string edits, optional visible pytest, and hidden pytest after the "
            "first edit and final answer. Hidden results are never shown to the model. "
            "Capsule v2-style reads use live AST/docstring evidence rather than cached model "
            "overviews; all arms use the same Haiku runner. Index preparation and pytest CPU "
            "cost are excluded from model USD cost; tool elapsed time is reported separately."
        ),
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["by_mode"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
