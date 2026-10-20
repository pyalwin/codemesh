#!/usr/bin/env python3
"""Paired long-running repair eval: native tools versus deliberate vNext CLI.

The default command prepares and verifies fixtures without calling a model.
Pass --run to spend model tokens. All tool observations and model usage are logged.
"""

from __future__ import annotations

import argparse
import difflib
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any

from grep_router import native_grep, native_read
from vnext_contract import PIN, ROOT, cli, command
from working_state import WorkingState
from focused_state import FocusedWorkingState
from source_bundles import (BUNDLE_BYTES, BUNDLE_SYMBOLS, SOURCE_ACTIONS, compact_source,
                            native_bundle, read_reference, source_blocks, span_reference)

MODES = ("filesystem", "codemesh")
GRADED_STATUSES = ("complete", "turn_limit", "context_limit")
TASKS = ROOT / "eval" / "edit_workflow_tasks.json"
SCHEMA = {
    "type": "object", "properties": {
        "action": {"type": "string", "enum": ["grep", "read", "map", "search", "source",
                                            "recall", "remember", "edit", "test", "answer"]},
        "query": {"type": "string"}, "path": {"type": "string"}, "node": {"type": "string"},
        "relation": {"type": "string"}, "cursor": {"type": "string"},
        "mode": {"type": "string"}, "if_hash": {"type": "string"},
        "start": {"type": "integer"}, "limit": {"type": "integer"},
        "old": {"type": "string"}, "new": {"type": "string"},
        "summary": {"type": "string"}, "answer": {"type": "string"},
    }, "required": ["action", "answer"], "additionalProperties": False,
}


def schema(mode: str, context_policy: str = "full", bundle_reads: bool = False,
           tool_contract: str = "legacy") -> str:
    if tool_contract == "lean":
        from tool_contract import lean_schema
        return lean_schema(mode, context_policy, bundle_reads)
    if tool_contract != "legacy":
        raise ValueError("Unknown tool contract")
    data = json.loads(json.dumps(SCHEMA))
    if mode == "filesystem":
        data["properties"]["action"]["enum"] = ["grep", "read", "edit", "test", "answer"]
    if context_policy in ("working-set", "focused"):
        data["properties"]["action"]["enum"].append("restore")
        data["properties"]["handle"] = {"type": "string"}
    if mode == "codemesh":
        data["properties"]["max_bytes"] = {"type": "integer", "minimum": 512, "maximum": 65536}
    if bundle_reads:
        data["properties"]["action"]["enum"].append("batch_read")
        data["properties"]["reads"] = {"type": "array", "minItems": 1, "maxItems": 8, "items": {
            "type": "object", "properties": {"path": {"type": "string"},
                "start": {"type": "integer"}, "limit": {"type": "integer"}},
            "required": ["path", "start", "limit"], "additionalProperties": False}}
        if mode == "codemesh":
            data["properties"]["action"]["enum"].append("inspect")
            data["properties"]["depth"] = {"type": "integer", "enum": [0, 1]}
            data["properties"]["direction"] = {"type": "string", "enum": ["outgoing", "incoming", "both"]}
    return json.dumps(data, separators=(",", ":"))


def system_prompt(mode: str, source_mode: str = "auto", context_policy: str = "full",
                  bundle_reads: bool = False, tool_contract: str = "legacy") -> str:
    if tool_contract == "lean":
        from tool_contract import lean_system_prompt
        return lean_system_prompt(mode, source_mode, context_policy, bundle_reads)
    if tool_contract != "legacy":
        raise ValueError("Unknown tool contract")
    common = (
        "Repair one seeded Pydantic regression. Choose one JSON action per response. "
        "Emit JSON actions only; the harness executes them. Do not invoke code_mode or any internal tool router. "
        "For tool actions leave answer empty. Leave irrelevant optional fields null. Tool observations arrive in the next message. "
        "Use source evidence before editing. Paths are relative to the checkout. "
        "grep is literal and searches Python source. read takes path/start/limit. "
        "edit replaces an exact unique old string in a pydantic/*.py source file. "
        "test runs the visible test. Hidden tests are unavailable. "
        "Do not infer behavior from signatures or notes alone."
    )
    if context_policy in ("working-set", "focused"):
        common += (
            " The harness supplies a bounded working state, not the complete conversation history. "
            "Previous navigation is represented by archive handles and candidate pointers. "
            "restore(handle) retrieves an archived map/search/grep observation for navigation; "
            "restore(handle='archive:0') lists archived navigation and source pointers in pages. "
            "it is historical and must not be treated as current source evidence. "
            "Re-run source/read to recover evicted code. Preserve unresolved branches; "
            "model-authored conclusions remain hypotheses until supported by source or tests. "
            "Only use if_hash for source present in the supplied active evidence."
        )
    if bundle_reads:
        common += (
            f" batch_read(reads=[{{path,start,limit}},...]) fetches up to {BUNDLE_SYMBOLS} explicit spans "
            f"in one action, each <=160 lines, with a {BUNDLE_BYTES}-byte total JSON budget. "
            "Omitted spans are explicit; source blocks are exact. "
        )
        if mode == "codemesh":
            common += (
                f"inspect(node=exact symbol ID or unique name, depth=0|1, direction=outgoing|incoming|both) returns exact root source and "
                f"verified direct neighbors in one action, <= {BUNDLE_SYMBOLS} symbols and "
                f"{BUNDLE_BYTES} JSON bytes. Default depth is 1. Check unresolved/ambiguous/omitted entries; "
                "Incoming discovery uses indexed candidate edges and is incomplete; missing neighbors do not prove no call exists. "
            )
    if mode == "filesystem":
        return common + " Available actions: grep, read, edit, test, answer" + (", batch_read" if bundle_reads else "") + (
            ", restore." if context_policy in ("working-set", "focused") else ".")
    return common + (
        " Available actions: map, search, source, recall, remember, grep, read, edit, test, answer"
        + (", inspect, batch_read" if bundle_reads else "")
        + (", restore. " if context_policy in ("working-set", "focused") else ". ") +
        ("Emit map or search JSON actions to locate symbols, then source or inspect for exact code. " if bundle_reads else
         "Emit map or search JSON actions to locate symbols, then a source action for exact code. ") +
        "map(node='repo' or returned ID, optional relation/cursor/max_bytes) expands one level; "
        "search(query, optional path file/directory filter, limit 1..20, max_bytes) returns 8 indexed matches by default. "
        "Map/search have an 8192-byte default budget; follow nextCursor or increase max_bytes for omitted entries. "
        "Leave limit null for the first search; broaden only after inspecting insufficient results. "
        "source(node=exact symbol ID, mode=auto|fold|preview|full, optional if_hash) reads live-checked code "
        f"and returns a hash. The default source mode is {source_mode}; auto bounds large symbols and explicitly marks omissions. "
        "Leave mode null for initial navigation. Request full when exact code needed for an edit is omitted. "
        "Use map on large classes to select member IDs; use full on the relevant member before editing. "
        "remember(path,summary) records a source-bound explanation for another session; "
        "the harness attaches the saved handoff to a fresh session. "
        "When recorded source references are present, copy their exact id when requesting source. "
        "References do not contain the old source bytes. "
        "recall(path) finds other notes. "
        "Use grep/read when needed. "
        "Only send if_hash when those exact source bytes are still in this session's context."
    )


def model_call(message: str, session: str, mode: str, *, resume: bool,
               structured: bool, cwd: Path, model: str, budget: float | None,
               provider: str = "claude", effort: str = "low",
               history: list[dict[str, str]] | None = None,
               source_mode: str = "auto", working_state: WorkingState | None = None,
               artifact_dir: Path | None = None, bundle_reads: bool = False,
               tool_contract: str = "legacy") -> dict[str, Any]:
    if provider == "codex":
        from codex_runner import model_call as codex_call
        if history is None:
            raise ValueError("Codex requires an explicit session history")
        history.append({"role": "user", "content": message})
        context_policy = getattr(working_state, "policy", "working-set") if working_state else "full"
        prompt = working_state.render() if working_state else history
        if working_state and not structured:
            prompt = [*prompt, {"role": "user", "content": message}]
        state_stats = working_state.context_stats() if working_state else None
        row = codex_call(prompt, system=system_prompt(mode, source_mode, context_policy, bundle_reads, tool_contract),
                         schema=json.loads(schema(mode, context_policy, bundle_reads, tool_contract)) if structured else None,
                         model=model, effort=effort, artifact_dir=artifact_dir)
        prefix = "Continue this explicit conversation transcript:\n"
        row["prompt_context"] = {
            "policy": context_policy,
            "full_history_bytes": len((prefix + json.dumps(history, ensure_ascii=False)).encode()),
            "sent_transcript_bytes": len((prefix + json.dumps(prompt, ensure_ascii=False)).encode()),
        }
        if state_stats is not None:
            row["prompt_context"]["working_state"] = state_stats
        row["context_session_id"] = session
        if not row.get("error"):
            history.append({"role": "assistant", "content": row["text"]})
            if working_state:
                try:
                    working_state.assistant(row["decision"] if structured else row["text"])
                except (OSError, ValueError, RuntimeError, TypeError) as exc:
                    # The provider already ran: keep its usage and archived input.
                    row["error"] = f"Working-state update failed after provider response: {exc}"
                    row["call_valid"] = False
        return row
    if working_state is not None:
        raise ValueError("Working-state delivery requires the explicit-transcript Codex adapter")
    argv = ["claude", "--print", "--model", model, "--tools", "",
            "--output-format", "json", "--max-budget-usd", str(budget),
            "--setting-sources", "", "--effort", effort,
            "--system-prompt", system_prompt(mode, source_mode, bundle_reads=bundle_reads, tool_contract=tool_contract)]
    argv += ["--resume", session] if resume else ["--session-id", session]
    if structured:
        argv += ["--json-schema", schema(mode, bundle_reads=bundle_reads, tool_contract=tool_contract)]
    started = time.monotonic()
    try:
        result = subprocess.run(argv, input=message, text=True, capture_output=True,
                                cwd=cwd, timeout=180)
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"error": str(exc), "elapsed_ms": int((time.monotonic() - started) * 1000)}
    usage = payload.get("usage") or {}
    plain_text = payload.get("result") or ""
    if not structured and not plain_text:
        # Claude may preserve a resumed session's JSON schema even when this
        # call does not pass --json-schema. Recover its answer field.
        shaped = payload.get("structured_output")
        if isinstance(shaped, dict) and isinstance(shaped.get("answer"), str):
            plain_text = shaped["answer"]
    if not structured and plain_text.startswith("{"):
        try:
            shaped = json.loads(plain_text)
            if isinstance(shaped, dict) and isinstance(shaped.get("answer"), str):
                plain_text = shaped["answer"]
        except json.JSONDecodeError:
            pass
    row = {"decision": payload.get("structured_output") if structured else None,
           "text": plain_text, "session_id": payload.get("session_id"),
           "input_tokens": sum(usage.get(k, 0) or 0 for k in (
               "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
           "uncached_input_tokens": usage.get("input_tokens", 0) or 0,
           "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
           "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
           "output_tokens": usage.get("output_tokens", 0) or 0,
           "cost_usd": payload.get("total_cost_usd"), "cost_basis": "cli_reported",
           "raw_usage": usage, "raw_model_usage": payload.get("modelUsage"),
           "provider_num_turns": payload.get("num_turns"),
           "elapsed_ms": int((time.monotonic() - started) * 1000),
           "exit_code": result.returncode, "model_ids": list((payload.get("modelUsage") or {}).keys())}
    if payload.get("is_error") or result.returncode or payload.get("session_id") != session \
            or (structured and not isinstance(row["decision"], dict)) or (not structured and not plain_text.strip()):
        row["error"] = (row["text"] or result.stderr or "Invalid model response")[-1200:]
    return row


def safe_file(repo: Path, path: str) -> Path:
    target = (repo / path.removeprefix("./")).resolve()
    if not target.is_relative_to(repo.resolve()) or not target.is_file():
        raise ValueError("Path must be a file in the repository")
    return target


def seeded_sources(base: Path, task: dict[str, Any]) -> dict[str, bytes]:
    mutations = task.get("mutations", [task])
    if not isinstance(mutations, list) or not mutations:
        raise ValueError("Task mutations must be a nonempty list")
    sources: dict[str, str] = {}
    for mutation in mutations:
        if not isinstance(mutation, dict):
            raise ValueError("Invalid source mutation")
        path, old, mutant = (mutation.get(key) for key in ("path", "old", "mutant"))
        occurrence = mutation.get("occurrence", 1)
        if (not isinstance(path, str) or not path.startswith("pydantic/") or not path.endswith(".py")
                or any(part in ("", ".", "..") for part in path.split("/")) or "\\" in path
                or not isinstance(old, str) or not old or not isinstance(mutant, str) or old == mutant
                or type(occurrence) is not int or occurrence < 1):
            raise ValueError("Invalid source mutation")
        source = sources.get(path)
        if source is None:
            source = safe_file(base, path).read_text()
        if source.count(old) < occurrence:
            raise ValueError(f"Missing mutation anchor for {task['id']}: {path}")
        parts = source.split(old)
        sources[path] = old.join(parts[:occurrence]) + mutant + old.join(parts[occurrence:])
    if task["path"] not in sources:
        raise ValueError("Primary task path must be one of its mutation paths")
    return {path: source.encode() for path, source in sources.items()}


def seeded_source(base: Path, task: dict[str, Any]) -> bytes:
    """Compatibility helper for callers interested only in the primary file."""
    return seeded_sources(base, task)[task["path"]]


def seed_fingerprint(base: Path, task: dict[str, Any]) -> str:
    digest = sha256()
    for path, source in sorted(seeded_sources(base, task).items()):
        digest.update(path.encode() + b"\0")
        digest.update(sha256(source).digest())
    return digest.hexdigest()


def worktree(base: Path, path: Path, task: dict[str, Any], index: Path | None = None) -> None:
    sources = seeded_sources(base, task)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not (path / ".git").exists():
        shutil.rmtree(path)
    result = command(["git", "worktree", "add", "--detach", str(path), PIN], base)
    if result.returncode:
        raise RuntimeError(result.stderr)
    for file, source in sources.items():
        safe_file(path, file).write_bytes(source)
    (path / "eval_visible_test.py").write_text(task["visible_test"])
    if index:
        directory = path / ".codemesh"
        directory.mkdir()
        clone_db(index, directory / "codemesh.db")


def clone_db(source: Path, destination: Path) -> None:
    """Use APFS cloning when available; fall back to an ordinary independent copy."""
    result = subprocess.run(["cp", "-c", str(source), str(destination)], capture_output=True)
    if result.returncode:
        shutil.copy2(source, destination)


def build_fingerprint() -> str:
    digest = sha256()
    for path in sorted((ROOT / "dist").rglob("*.js")):
        if path.name.endswith(".test.js"):
            continue
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def protocol_fingerprint(tasks: Path) -> str:
    """Do not resume trajectories produced by a different harness or task protocol."""
    digest = sha256()
    for path in [Path(__file__), Path(__file__).with_name("codex_runner.py"),
                 Path(__file__).with_name("working_state.py"),
                 Path(__file__).with_name("focused_state.py"),
                 Path(__file__).with_name("tool_contract.py"),
                 Path(__file__).with_name("source_bundles.py"),
                 Path(__file__).with_name("codex_luna_catalog.json"),
                 Path(__file__).with_name("codex_terra_catalog.json"),
                 Path(__file__).with_name("grep_router.py"),
                 Path(__file__).with_name("vnext_contract.py"), tasks]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes() if path.exists() else b"<missing>")
    for task in json.loads(tasks.read_text())["tasks"]:
        digest.update((ROOT / task["hidden_test"]).read_bytes())
    return digest.hexdigest()


def total_cost(items: list[dict[str, Any]]) -> float | None:
    """Unavailable cost must never become a misleading zero-dollar run."""
    return total_known(items, "cost_usd")


def total_known(items: list[dict[str, Any]], key: str) -> float | None:
    values = [item.get(key) for item in items]
    return None if any(value is None for value in values) else sum(values)


def repeated_reads(phases: list[dict[str, Any]]) -> dict[str, int]:
    """Count repeated calls separately from blocks, preserving context provenance.

    One bundled action may repeat several source blocks or mix repeat categories.
    The total call count counts that action once; category call counts can overlap.
    """
    seen: dict[tuple[str, str, str], int] = {}
    kinds = ("same_context", "cross_context", "evicted_context")
    counts = {"repeat_read_calls": 0,
              **{f"{kind}_{metric}": 0 for kind in kinds for metric in (
                  "repeat_read_calls", "repeat_source_blocks", "repeated_bytes")}}
    for phase in phases:
        epoch = phase.get("context_epoch", int(phase["phase"] == 2))
        for action in phase["actions"]:
            if action["action"] not in SOURCE_ACTIONS or action.get("error"):
                continue
            if (action.get("route") or {}).get("status") in ("stale", "deleted", "unchanged"):
                continue
            units = []
            blocks = source_blocks(action["action"], action["observation"])
            for index, block in enumerate(blocks):
                if block.get("status") != "fresh" or not isinstance(block.get("source"), str):
                    continue
                node = block.get("id")
                target = node or f"{block.get('path')}:{block.get('lines')}"
                key = ("source" if node else "read", target, sha256(block["source"].encode()).hexdigest())
                resident = action.get("source_blocks_resident_before", [])
                units.append((key, len(block["source"].encode()), resident[index] if index < len(resident)
                              else action.get("source_resident_before")))
            if not blocks and action["action"] in ("source", "read"):
                target = action["input"].get("node") or (
                    f"{action['input'].get('path')}:{action['input'].get('start') or 1}:{action['input'].get('limit') or 60}")
                body = action["observation"]
                # Normalize numbered filesystem reads to the exact raw span so
                # automatic hydration and batch_read repeats remain observable.
                match = re.match(r"^(.+) lines (\d+)-(\d+) of \d+:\n", body)
                if action["action"] == "read" and match:
                    target = f"{match[1]}:{[int(match[2]), int(match[3])]}"
                    body = "\n".join(re.sub(r"^\d+: ", "", line) for line in body[match.end():].splitlines())
                units.append(((action["action"], target, sha256(body.encode()).hexdigest()),
                              len(body.encode()) if match else action["observation_bytes"],
                              action.get("source_resident_before")))
            repeated_categories = set()
            for key, delivered_bytes, resident in units:
                if key in seen:
                    prefix = ("cross_context" if seen[key] != epoch else
                              "evicted_context" if resident is False else "same_context")
                    repeated_categories.add(prefix)
                    counts[f"{prefix}_repeat_source_blocks"] += 1
                    counts[f"{prefix}_repeated_bytes"] += delivered_bytes
                seen[key] = epoch
            for prefix in repeated_categories:
                counts[f"{prefix}_repeat_read_calls"] += 1
            counts["repeat_read_calls"] += bool(repeated_categories)
    counts["repeat_source_blocks"] = sum(counts[f"{kind}_repeat_source_blocks"] for kind in kinds)
    counts["repeated_observation_bytes"] = sum(counts[f"{kind}_repeated_bytes"]
                                             for kind in kinds)
    return counts


def remove_worktree(base: Path, path: Path) -> None:
    if path.exists():
        if (path / ".git").exists():
            result = command(["git", "worktree", "remove", "--force", str(path)], base)
            if result.returncode:
                raise RuntimeError(result.stderr)
        # Git can leave generated empty directories behind (for example from
        # filesystem synchronization). These paths are owned by this runner.
        if path.exists():
            shutil.rmtree(path)


def pytest(repo: Path, python: Path, test: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    started = time.monotonic()
    try:
        process = subprocess.run([str(python), "-m", "pytest", "-q", "-o", "addopts=", str(test)],
                                 cwd=repo, env=env, text=True, capture_output=True, timeout=90)
        return {"passed": process.returncode == 0, "exit_code": process.returncode,
                "output": (process.stdout + process.stderr)[-2500:],
                "elapsed_ms": int((time.monotonic() - started) * 1000)}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"passed": False, "error": str(exc),
                "elapsed_ms": int((time.monotonic() - started) * 1000)}


def prepare(base: Path, tasks: list[dict[str, Any]], out: Path, python: Path) -> dict[str, Path]:
    indexes: dict[str, Path] = {}
    fingerprint = build_fingerprint()
    pristine_path = out / "worktrees" / "prepare-pristine"
    pristine_path.parent.mkdir(parents=True, exist_ok=True)
    if pristine_path.exists() and not (pristine_path / ".git").exists():
        shutil.rmtree(pristine_path)
    added = command(["git", "worktree", "add", "--detach", str(pristine_path), PIN], base)
    if added.returncode:
        raise RuntimeError(added.stderr)
    try:
        for task in tasks:
            index = out / "indexes" / f"{task['id']}.db"
            checks = out / "indexes" / f"{task['id']}.json"
            if index.exists() and checks.exists():
                existing = json.loads(checks.read_text())
                if (existing.get("commit") == PIN and existing.get("build_fingerprint") == fingerprint
                        and existing.get("seed_hash") == seed_fingerprint(base, task)):
                    indexes[task["id"]] = index
                    continue
            pristine = pytest(pristine_path, python, ROOT / task["hidden_test"])
            path = out / "worktrees" / f"prepare-{task['id']}"
            worktree(base, path, task)
            try:
                visible = pytest(path, python, path / "eval_visible_test.py")
                hidden = pytest(path, python, ROOT / task["hidden_test"])
                if not pristine["passed"] or visible.get("exit_code") != 1 or hidden.get("exit_code") != 1:
                    raise RuntimeError(f"Invalid fixture {task['id']}: {pristine}, {visible}, {hidden}")
                index.parent.mkdir(parents=True, exist_ok=True)
                cli(path, "index", "--json")
                clone_db(path / ".codemesh" / "codemesh.db", index)
                checks.write_text(json.dumps({"commit": PIN, "task": task["id"],
                                              "build_fingerprint": fingerprint,
                                              "seed_hash": seed_fingerprint(base, task),
                                              "mutation_paths": sorted(seeded_sources(base, task)),
                                              "pristine": pristine, "mutant_visible": visible,
                                              "mutant_hidden": hidden}, indent=2) + "\n")
                indexes[task["id"]] = index
            finally:
                remove_worktree(base, path)
    finally:
        remove_worktree(base, pristine_path)
    return indexes


def tool(decision: dict[str, Any], mode: str, repo: Path, task: dict[str, Any],
         python: Path, session: str, epoch: int, known_hashes: set[tuple[str, str, str]],
         test_count: int, source_mode: str = "auto", bundle_reads: bool = False,
         search_tie_break: str = "pagerank") -> tuple[str, str, dict[str, Any]]:
    action = decision.get("action")
    if action == "grep":
        query = decision.get("query") or ""
        if not query or len(query) > 100:
            raise ValueError("grep requires a bounded literal query")
        return action, native_grep(query, repo), {"backend": "filesystem"}
    if action == "read":
        path = decision.get("path") or ""
        start, limit = decision.get("start") or 1, decision.get("limit") or 60
        if not isinstance(start, int) or not isinstance(limit, int) or start < 1 or limit < 1 or limit > 160:
            raise ValueError("read range must be positive and <=160 lines")
        return action, native_read(path, start, limit, repo), {"backend": "filesystem"}
    if action == "batch_read" and bundle_reads:
        result = native_bundle(repo, decision.get("reads"))
        return action, json.dumps(result, ensure_ascii=False, separators=(",", ":")), {"backend": "filesystem", "status": result["status"]}
    if action == "inspect" and bundle_reads and mode == "codemesh":
        depth = decision.get("depth") if decision.get("depth") is not None else 1
        if type(depth) is not int or depth not in (0, 1):
            raise ValueError("inspect depth must be 0 or 1")
        direction = decision.get("direction") or "outgoing"
        if direction not in ("outgoing", "incoming", "both"):
            raise ValueError("inspect direction must be outgoing, incoming, or both")
        result, ms = cli(repo, "explore", "inspect", decision.get("node") or "", "--depth", str(depth), "--direction", direction,
                         "--max-symbols", str(BUNDLE_SYMBOLS), "--max-bytes", str(BUNDLE_BYTES), "--compact")
        observation = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        for block in source_blocks(action, observation):
            if block.get("status") == "fresh" and isinstance(block.get("source"), str):
                known_hashes.add((block["id"], block["mode"], block["hash"]))
        return action, observation, {"backend": "codemesh", "elapsed_ms": ms, "status": result.get("status")}
    if mode == "codemesh" and action in ("map", "search", "source", "recall", "remember"):
        args: list[str]
        if action == "map":
            args = ["explore", "map", "--node", decision.get("node") or "repo", "--limit", "30", "--compact"]
            for key, flag in (("relation", "--relation"), ("cursor", "--cursor")):
                if decision.get(key): args += [flag, decision[key]]
        elif action == "search":
            args = ["explore", "search", decision.get("query") or "", "--compact"]
            if search_tie_break not in ("pagerank", "path-depth"):
                raise ValueError("Unknown search tie-break")
            if search_tie_break != "pagerank":
                args += ["--exact-tie-break", search_tie_break]
            if decision.get("limit") is not None:
                args += ["--limit", str(decision["limit"])]
            if decision.get("path"):
                args += ["--path", decision["path"]]
        elif action == "source":
            node, view = decision.get("node") or "", decision.get("mode") or source_mode
            args = ["explore", "source", node, "--mode", view, "--compact",
                    "--session", session, "--epoch", str(epoch)]
            if decision.get("if_hash"):
                if (node, view, decision["if_hash"]) not in known_hashes:
                    raise ValueError("if_hash bytes are absent from this session; request source without it")
                args += ["--if-hash", decision["if_hash"]]
        elif action == "recall":
            args = ["explore", "recall", "--path", decision.get("path") or "", "--compact"]
        else:
            args = ["remember", "--path", decision.get("path") or "", "--summary", decision.get("summary") or "",
                    "--session", session]
        if action in ("map", "search") and decision.get("max_bytes") is not None:
            budget_bytes = decision["max_bytes"]
            if type(budget_bytes) is not int or not 512 <= budget_bytes <= 65536:
                raise ValueError("max_bytes must be an integer from 512 to 65536")
            args += ["--max-bytes", str(budget_bytes)]
        result, ms = cli(repo, *args)
        if action == "source" and result.get("status") == "fresh":
            source_hash = result.get("hash") or result["sourceHash"]
            known_hashes.add((node, view, source_hash))
            known_hashes.add((result.get("id", node), view, source_hash))
        return action, json.dumps(result, separators=(",", ":")), {"backend": "codemesh", "elapsed_ms": ms,
                                                                   "status": result.get("status")}
    if action == "edit":
        path, old, new = decision.get("path") or "", decision.get("old"), decision.get("new")
        if not path.startswith("pydantic/") or not path.endswith(".py"):
            raise ValueError("edit must target pydantic/*.py")
        if not isinstance(old, str) or not isinstance(new, str) or not old or len(old) > 4000 or len(new) > 4000:
            raise ValueError("edit requires bounded nonempty old/new strings")
        target = safe_file(repo, path)
        content = target.read_text()
        if content.count(old) != 1:
            raise ValueError(f"old string must occur exactly once; found {content.count(old)}")
        target.write_text(content.replace(old, new, 1))
        return action, f"Edited {path}; exact old string replaced once.", {"backend": "filesystem"}
    if action == "test":
        if test_count >= 3: raise ValueError("Maximum three visible tests")
        result = pytest(repo, python, repo / "eval_visible_test.py")
        return action, result["output"], {"backend": "pytest", "passed": result["passed"]}
    raise ValueError("Unsupported tool action")


def phase_prompt(task: dict[str, Any], phase: int, memory: str, mode: str) -> str:
    if phase == 0:
        return f"Repair task: {task['symptom']}\nInvestigate the likely cause and read source. Do not edit yet."
    if phase == 1:
        return f"Second investigation: {task['impact_question']}\nCheck source as needed. Do not edit yet."
    handoff = f"Model-written handoff from earlier session:\n{memory}\n" if mode == "filesystem" else memory
    return handoff + f"Apply a minimal fix: {task['symptom']}\nRun the visible test if useful, then summarize the patch."


HANDOFF_FORMAT = "codemesh-handoff-v1"
MAX_HANDOFF_REFERENCES = 8


def source_reference(repo: Path, observation: str) -> dict[str, Any] | None:
    """Capture canonical references only from successfully delivered live source."""
    try:
        result = compact_source(json.loads(observation))
        if (result.get("status") != "fresh" or result.get("mode") == "fold"
                or not isinstance(result.get("source"), str)):
            return None
        node, path, lines, source_hash = (result.get(key) for key in ("id", "path", "lines", "hash"))
        if (not isinstance(node, str) or not node.startswith("symbol:")
                or not isinstance(path, str) or not isinstance(lines, list) or len(lines) != 2
                or any(type(line) is not int for line in lines) or not 1 <= lines[0] <= lines[1]
                or not isinstance(source_hash, str)
                or sha256(result["source"].encode()).hexdigest() != source_hash):
            return None
        live = safe_file(repo, path).read_bytes()
        live_span = "\n".join(live.decode().split("\n")[lines[0] - 1:lines[1]])
        if not result.get("omittedRanges"):
            if sha256(live_span.encode()).hexdigest() != source_hash:
                return None  # An external edit raced the completed source read.
        elif sha256(live_span.encode()).hexdigest() != result.get("bodyHash"):
            # A preview hash alone cannot prove which omitted body was seen.
            # Older compact previews lack this proof and remain navigation only.
            return None
        if result.get("fileHash") and result["fileHash"] != sha256(live).hexdigest():
            return None
        return {"id": node, "path": path, "lines": lines, "hash": source_hash,
                "mode": result.get("mode"), "file_hash": sha256(live).hexdigest()}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def handoff_payload(summary: str, references: list[dict[str, Any]]) -> str:
    """Persist references in the existing SQLite note, not in model-written prose."""
    return json.dumps({"format": HANDOFF_FORMAT, "summary": summary,
                       "references": references[-MAX_HANDOFF_REFERENCES:]}, separators=(",", ":"))


def restored_references(repo: Path, references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    restored = []
    for ref in references[:MAX_HANDOFF_REFERENCES]:
        if not isinstance(ref, dict) or not isinstance(ref.get("id"), str) or not isinstance(ref.get("path"), str):
            continue
        item = {"id": ref["id"], "path": ref["path"]}
        try:
            live_hash = sha256(safe_file(repo, ref["path"]).read_bytes()).hexdigest()
            if live_hash == ref.get("file_hash"):
                item.update({key: ref[key] for key in ("lines", "hash", "mode") if key in ref})
                item["status"] = "file_unchanged"
            else:
                item["status"] = "stale"
        except (OSError, ValueError):
            item["status"] = "unavailable"
        restored.append(item)
    return restored


def load_handoff(repo: Path, path: str, expected_summary: str,
                 references_out: list[dict[str, Any]] | None = None) -> str:
    """Inject the persisted note at the new-session boundary, with freshness visible."""
    result, _ = cli(repo, "explore", "recall", "--path", path, "--compact")
    note = next((item for item in result.get("notes", [])
                 if item.get("summary") == expected_summary), None)
    if note is None:
        raise RuntimeError("Persisted handoff was not found by recall")
    status = note.get("status")
    if status == "stale":
        return "Saved Codemesh handoff is stale; re-investigate live source before editing.\n"
    summary = note["summary"]
    references = []
    try:
        payload = json.loads(summary)
        if isinstance(payload, dict) and payload.get("format") == HANDOFF_FORMAT:
            summary = payload["summary"]
            references = restored_references(repo, payload["references"])
            if references_out is not None:
                references_out.extend(ref for ref in payload["references"][:MAX_HANDOFF_REFERENCES]
                                      if isinstance(ref, dict))
    except (ValueError, KeyError, TypeError):
        pass  # Legacy narrative-only notes remain readable.
    rendered = f"Saved Codemesh handoff ({status}; verify against live source):\n{summary}\n"
    if references:
        rendered += ("Recorded source references (navigation pointers, not source evidence; copy exact id). "
                     "Re-read source before editing. Saved hashes do not authorize if_hash in this fresh context. "
                     "Stale/unavailable references require re-indexing or fresh discovery:\n"
                     + json.dumps(references, separators=(",", ":")) + "\n")
    return rendered


def hydrate_handoff(repo: Path, mode: str, references: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Automatic live read at reset; returned bytes enter active evidence, never task prose."""
    if mode == "codemesh":
        saved = [{"id": ref["id"], "hash": ref["hash"], "mode": ref["mode"],
                  "fileHash": ref["file_hash"]} for ref in references]
        result, ms = cli(repo, "explore", "hydrate", "--references", json.dumps(saved, separators=(",", ":")),
                         "--max-symbols", str(BUNDLE_SYMBOLS), "--max-bytes", str(BUNDLE_BYTES), "--compact")
        route = {"backend": "codemesh", "elapsed_ms": ms, "status": result.get("status"), "automatic": True}
    else:
        result = native_bundle(repo, references, hydrate=True)
        route = {"backend": "filesystem", "status": result.get("status"), "automatic": True}
    return json.dumps(result, ensure_ascii=False, separators=(",", ":")), route


def trajectory(base: Path, task: dict[str, Any], mode: str, repeat: int, out: Path,
               index: Path, python: Path, model: str, budget: float | None, max_turns: int,
               provider: str = "claude", effort: str = "low", source_mode: str = "auto",
               handoff_references: bool = True, context_policy: str = "full",
               evidence_bytes: int = 16384, ledger_entries: int = 8,
               hydrate_on_resume: bool = False, bundle_reads: bool = False,
               tool_contract: str = "legacy", search_tie_break: str = "pagerank") -> dict[str, Any]:
    if tool_contract not in ("legacy", "lean") or search_tie_break not in ("pagerank", "path-depth"):
        raise ValueError("Unknown tool contract or search tie-break")
    if context_policy not in ("full", "working-set", "focused"):
        raise ValueError("Unknown context policy")
    if context_policy in ("working-set", "focused") and provider != "codex":
        raise ValueError("Working-state delivery requires the explicit-transcript Codex adapter")
    if hydrate_on_resume and not handoff_references:
        raise ValueError("Resume hydration requires saved handoff references")
    path = out / "worktrees" / f"{task['id']}-{mode}-{repeat}"
    session = str(uuid.uuid4())
    resume = False
    history: list[dict[str, str]] = []
    known_hashes: set[tuple[str, str, str]] = set()
    calls: list[dict[str, Any]] = []
    attempt_id = str(uuid.uuid4())
    state_class = FocusedWorkingState if context_policy == "focused" else WorkingState
    state = (state_class(out / "observations" / attempt_id, session,
                          evidence_bytes=evidence_bytes, ledger_entries=ledger_entries)
             if context_policy in ("working-set", "focused") else None)

    def invoke(message: str, *, structured: bool) -> dict[str, Any]:
        try:
            return model_call(message, session, mode, resume=resume, structured=structured,
                              cwd=out, model=model, budget=budget, provider=provider,
                              effort=effort, history=history, source_mode=source_mode,
                              working_state=state,
                              bundle_reads=bundle_reads,
                              tool_contract=tool_contract,
                              artifact_dir=out / "inputs" / attempt_id / f"call-{len(calls) + 1:03}")
        except (OSError, ValueError, RuntimeError) as exc:
            return {"error": f"Prompt preparation failed: {exc}", "call_valid": False,
                    "usage_available": False, "cost_usd": None, "context_session_id": session}

    def retain_control(decision: dict[str, Any], message: str) -> str | None:
        if state:
            try:
                state.observe("control", decision, message, error=message, route={"backend": "harness"})
            except (OSError, ValueError, RuntimeError, TypeError) as exc:
                return str(exc)
        return None
    def record_call(call: dict[str, Any]) -> None:
        calls.append(call)
        with (out / "calls.jsonl").open("a") as handle:
            handle.write(json.dumps({"attempt_id": attempt_id, "task": task["id"],
                                     "mode": mode, "repeat": repeat, "call_index": len(calls),
                                     **call}) + "\n")
    phases: list[dict[str, Any]] = []
    memory = ""
    restored_handoff = ""
    references: dict[str, dict[str, Any]] = {}
    saved_reference_count = 0
    status = "complete"
    edits = tests = tool_ms = 0
    harness_memory_writes = 0
    hydration_references: list[dict[str, Any]] = []
    first_patch_pass: bool | None = None
    first_correct_edit: dict[str, int] | None = None
    observation_bytes = action_count = 0
    worktree(base, path, task, index if mode == "codemesh" else None)
    mutation_paths = {m["path"] for m in task.get("mutations", [task])}
    initial_sources = {file: safe_file(path, file).read_text() for file in mutation_paths}
    try:
        for phase in range(3):
            if phase == 2:
                instruction = "Write a factual handoff for a fresh coding agent in at most 120 words. Include observed cause, source location, behavior to preserve, and uncertainties. Do not patch yet."
                response = invoke(instruction, structured=False)
                record_call({"kind": "handoff", "phase": phase, "message": instruction, **response})
                if response.get("error") == "Invalid model response" and not response.get("text", "").strip():
                    retry_instruction = (
                        "Your previous response contained no handoff text. "
                        "Write the factual handoff now in plain text, within 120 words."
                    )
                    response = invoke(retry_instruction, structured=False)
                    record_call({"kind": "handoff_retry", "phase": phase,
                                 "message": retry_instruction, **response})
                if response.get("error"):
                    status = "context_limit" if "prompt is too long" in response["error"].lower() else "handoff_error"
                    break
                memory = response["text"][:2000]
                if mode == "codemesh":
                    saved = list(references.values())[-MAX_HANDOFF_REFERENCES:] if handoff_references else []
                    saved_reference_count = len(saved)
                    summary = handoff_payload(memory, saved) if handoff_references else memory
                    try:
                        cli(path, "remember", "--path", task["path"], "--summary", summary,
                            "--session", session, "--status", "hypothesis")
                        harness_memory_writes += 1
                        restored_handoff = load_handoff(path, task["path"], summary,
                                                        hydration_references if hydrate_on_resume else None)
                    except RuntimeError as exc:
                        status = "memory_handoff_error"
                        calls.append({"kind": "memory_error", "error": str(exc), "cost_usd": 0})
                        break
                elif hydrate_on_resume:
                    saved = list(references.values())[-MAX_HANDOFF_REFERENCES:]
                    saved_reference_count = len(saved)
                    checkpoint = out / "handoffs" / f"{attempt_id}.json"
                    checkpoint.parent.mkdir(parents=True, exist_ok=True)
                    checkpoint.write_text(handoff_payload(memory, saved))
                    hydration_references = json.loads(checkpoint.read_text())["references"]
                session = str(uuid.uuid4())
                resume = False
                known_hashes.clear()
                history.clear()
                if state:
                    state.reset(session)
            record: dict[str, Any] = {"phase": phase, "prompt": phase_prompt(task, phase, restored_handoff if mode == "codemesh" else memory, mode),
                                      "context_epoch": int(phase == 2), "actions": [], "answer": None}
            message = record["prompt"]
            if state:
                state.task(message)
            phase_reads = 0
            if phase == 2 and hydrate_on_resume and hydration_references:
                started = time.monotonic()
                try:
                    observation, route = hydrate_handoff(path, mode, hydration_references)
                    hydration_input = {"references": hydration_references}
                    handle = state.observe("hydrate", hydration_input, observation, route=route) if state else None
                    blocks = source_blocks("hydrate", observation)
                    for block in blocks:
                        if block.get("status") == "fresh" and str(block.get("id", "")).startswith("symbol:"):
                            known_hashes.add((block["id"], block["mode"], block["hash"]))
                    phase_reads += len(blocks)
                    record["actions"].append({"action": "hydrate", "input": hydration_input, "observation": observation,
                        "observation_bytes": len(observation.encode()), "route": route, "error": None,
                        **({"archive_handle": handle} if handle else {})})
                    observation_bytes += len(observation.encode())
                    action_count += 1
                    # Full history gets this once; working state consumes the separate observation.
                    message += ("\nAutomatic resume hydration (live-verified source is evidence; "
                                "failed or omitted references still require a live read):\n" + observation)
                except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                    status = "memory_handoff_error"
                    record["hydration_error"] = str(exc)
                    calls.append({"kind": "memory_error", "error": str(exc), "cost_usd": 0})
                tool_ms += int((time.monotonic() - started) * 1000)
                if status != "complete":
                    phases.append(record)
                    break
            for turn in range(1, max_turns + 1):
                response = invoke(message, structured=True)
                record_call({"kind": "decision", "phase": phase, "turn": turn, "message": message, **response})
                resume = True
                if response.get("error"):
                    status = "context_limit" if "prompt is too long" in response["error"].lower() else "model_error"
                    break
                decision = response["decision"]
                action = decision.get("action")
                if action == "answer":
                    if phase == 0 and phase_reads == 0:
                        message = "Read at least one source span before answering."
                        record["actions"].append({"action": "premature_answer"})
                        failure = retain_control(decision, message)
                        if failure is not None:
                            status, record["context_error"] = "context_state_error", failure
                            break
                        continue
                    if phase == 2 and edits == 0:
                        message = "Apply a source edit before finishing."
                        record["actions"].append({"action": "premature_answer"})
                        failure = retain_control(decision, message)
                        if failure is not None:
                            status, record["context_error"] = "context_state_error", failure
                            break
                        continue
                    record["answer"] = decision.get("answer") or ""
                    record["actions"].append({"action": "answer"})
                    break
                if phase < 2 and action in ("edit", "test"):
                    message = "Edit and test are available in the patch phase."
                    record["actions"].append({"action": "early_action", "input": decision})
                    failure = retain_control(decision, message)
                    if failure is not None:
                        status, record["context_error"] = "context_state_error", failure
                        break
                    continue
                started = time.monotonic()
                if state:
                    # Eligibility means retained in the next prompt, not ever delivered.
                    known_hashes.clear()
                    known_hashes.update(state.resident_hashes())
                try:
                    if action == "edit":
                        edit_path = (decision.get("path") or "").removeprefix("./")
                        if edit_path.startswith("pydantic/") and edit_path not in initial_sources:
                            initial_sources[edit_path] = safe_file(path, edit_path).read_text()
                    if action == "restore" and state:
                        actual = "restore"
                        observation = state.restore(decision.get("handle") or "")
                        route = {"backend": "observation_archive", "historical": True}
                    else:
                        actual, observation, route = tool(decision, mode, path, task, python, session,
                                                          int(phase == 2), known_hashes, tests, source_mode, bundle_reads,
                                                          search_tie_break)
                    error = None
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    actual, observation, route, error = str(action), f"Tool error: {exc}", {}, str(exc)
                tool_ms += int((time.monotonic() - started) * 1000)
                if not error and phase < 2:
                    captured = []
                    if mode == "codemesh" and action in ("source", "inspect"):
                        captured = [source_reference(path, json.dumps(block)) for block in source_blocks(action, observation)]
                    elif mode == "filesystem" and hydrate_on_resume:
                        if action == "read":
                            captured = [read_reference(path, decision, observation)]
                        elif action == "batch_read":
                            captured = [span_reference(path, block) for block in source_blocks(action, observation)]
                    for reference in captured:
                        if reference:
                            references.pop(reference["id"], None)
                            references[reference["id"]] = reference
                if not error and (action == "read" or (action == "source" and route.get("status") == "fresh"
                                  and (decision.get("mode") or source_mode) in ("auto", "preview", "full"))):
                    phase_reads += 1
                if not error and action in ("inspect", "batch_read"):
                    phase_reads += sum(block.get("status") == "fresh" for block in source_blocks(action, observation))
                if not error and action == "edit":
                    edits += 1
                    patch_pass = pytest(path, python, ROOT / task["hidden_test"])["passed"]
                    if first_patch_pass is None:
                        first_patch_pass = patch_pass
                    if patch_pass and first_correct_edit is None:
                        first_correct_edit = {"edit_count": edits, "tool_calls_before_edit": action_count,
                                              "observation_bytes_before_edit": observation_bytes}
                if not error and action == "test": tests += 1
                action_count += 1
                if not error:
                    observation_bytes += len(observation.encode())
                handle = None
                resident_before = (state.observation_resident(actual, decision, observation)
                                   if state and actual in ("read", "source") and not error else None)
                block_residency = [state.observation_resident(
                    "source" if str(block.get("id", "")).startswith("symbol:") else "read_span", {}, json.dumps(block))
                    for block in source_blocks(actual, observation)] if state and not error and actual in ("inspect", "batch_read") else []
                state_error = None
                if state and (actual != "restore" or error):
                    try:
                        handle = state.observe(actual, decision, observation, error=error, route=route)
                    except (OSError, ValueError, RuntimeError, TypeError) as exc:
                        state_error = str(exc)
                record["actions"].append({"action": actual, "input": decision, "observation": observation,
                                          "observation_bytes": len(observation.encode()), "route": route,
                                          "error": error, **({"archive_handle": handle} if handle else {}),
                                          **({"source_resident_before": resident_before}
                                             if resident_before is not None else {}),
                                          **({"source_blocks_resident_before": block_residency} if block_residency else {})})
                if state_error is not None:
                    status = "context_state_error"
                    record["context_error"] = state_error
                    break
                message = f"Tool result for {actual}:\n{observation}\nChoose another action or answer from observed evidence."
            else:
                status = "turn_limit"
            phases.append(record)
            if status != "complete" or record["answer"] is None:
                break
        hidden = pytest(path, python, ROOT / task["hidden_test"])
        visible = pytest(path, python, path / "eval_visible_test.py")
        diff = "".join("".join(difflib.unified_diff(
            before.splitlines(keepends=True), safe_file(path, file).read_text().splitlines(keepends=True),
            fromfile=f"a/{file}", tofile=f"b/{file}"))
            for file, before in sorted(initial_sources.items()))
        patch = out / "patches" / f"{task['id']}-{mode}-{repeat}.diff"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(diff)
    finally:
        remove_worktree(base, path)
    actions = [a for phase in phases for a in phase["actions"]]
    totals = {key: sum(call.get(key) or 0 for call in calls) for key in (
        "input_tokens", "uncached_input_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens", "output_tokens", "elapsed_ms")}
    totals["cost_usd"] = total_cost(calls)
    model_calls = [call for call in calls if call.get("kind") != "memory_error"]
    for key in ("cache_creation_input_tokens", "uncached_input_tokens"):
        totals[key] = total_known(model_calls, key)
    router_error_count = sum(call.get("router_error_count", 0) for call in model_calls)
    return {"attempt_id": attempt_id, "task": task["id"], "mode": mode, "repeat": repeat, "status": status,
            "provider": provider, "model": model, "context_policy": context_policy,
            "hydrate_on_resume": hydrate_on_resume, "bundle_reads": bundle_reads,
            "tool_contract": tool_contract, "search_tie_break": search_tie_break,
            "mutation_paths": sorted(mutation_paths),
            "first_correct_edit": first_correct_edit,
            "router_error_count": router_error_count,
            "router_affected_calls": sum(call.get("router_error_count", 0) > 0 for call in model_calls),
            "call_protocol_valid": all(call.get("call_valid", not call.get("error")) for call in model_calls),
            "phases": phases, "calls": calls, "handoff": memory, "totals": totals,
            "tool_elapsed_ms": tool_ms, "first_patch_pass": first_patch_pass is True,
            "final_patch_pass": hidden["passed"], "visible_final_pass": visible["passed"],
            "hidden_final": hidden, "visible_final": visible, "patch_path": str(patch),
            "tool_calls": sum(a["action"] not in ("answer", "premature_answer") for a in actions),
            "harness_memory_writes": harness_memory_writes,
            "handoff_reference_count": saved_reference_count,
            "restore_calls": sum(a["action"] == "restore" for a in actions),
            "harness_hydration_calls": sum(a["action"] == "hydrate" for a in actions),
            "inspect_calls": sum(a["action"] == "inspect" for a in actions),
            "batch_read_calls": sum(a["action"] == "batch_read" for a in actions),
            "sent_transcript_bytes": sum(c.get("prompt_context", {}).get("sent_transcript_bytes", 0) for c in calls),
            "full_history_transcript_bytes": sum(c.get("prompt_context", {}).get("full_history_bytes", 0) for c in calls),
            "total_observation_bytes": sum(a.get("observation_bytes", 0) for a in actions if not a.get("error")),
            "native_grep_calls": sum(a["action"] == "grep" for a in actions),
            "native_read_calls": sum(a["action"] == "read" for a in actions),
            "codemesh_calls": sum(a["action"] in ("map", "search", "source", "recall", "remember", "inspect")
                                   or (a["action"] == "hydrate" and mode == "codemesh") for a in actions),
            **{f"{name}_calls": sum(a["action"] == name for a in actions)
               for name in ("map", "search", "source", "recall", "remember")},
            "search_limit_overrides": sum(a["action"] == "search" and a.get("input", {}).get("limit") is not None for a in actions),
            "source_full_requests": sum(a["action"] == "source" and a.get("input", {}).get("mode") == "full" for a in actions),
            "source_default_requests": sum(a["action"] == "source" and not a.get("input", {}).get("mode") for a in actions),
            "source_observation_bytes": sum(a.get("observation_bytes", 0) for a in actions
                                            if a["action"] in SOURCE_ACTIONS and not a.get("error")),
            "source_error_observation_bytes": sum(a.get("observation_bytes", 0) for a in actions
                                                  if a["action"] in SOURCE_ACTIONS and a.get("error")),
            **repeated_reads(phases),
            "conditional_hits": sum((a.get("route") or {}).get("status") == "unchanged" for a in actions)}


def summarize(rows: list[dict[str, Any]], modes: list[str]) -> dict[str, Any]:
    return {mode: {"attempts": len(attempts := [r for r in rows if r["mode"] == mode]),
                   "graded_trajectories": len(selected := [r for r in attempts if r["status"] in GRADED_STATUSES]),
                   "runner_errors": len(attempts) - len(selected),
                   "ungraded_model_cost_usd": total_cost([r["totals"] for r in attempts
                                                          if r["status"] not in GRADED_STATUSES]),
                   "all_attempt_cost_usd": total_cost([r["totals"] for r in attempts]),
                   "all_attempt_input_tokens": total_known([r["totals"] for r in attempts], "input_tokens"),
                   "all_attempt_output_tokens": total_known([r["totals"] for r in attempts], "output_tokens"),
                   "all_attempt_router_error_count": sum(r.get("router_error_count", 0) for r in attempts),
                   "all_attempt_router_affected_calls": sum(r.get("router_affected_calls", 0) for r in attempts),
                   "cost_usd": total_cost([r["totals"] for r in selected]),
                   **{key: sum(r.get(key, 0) for r in selected) for key in (
                       "first_patch_pass", "final_patch_pass", "visible_final_pass", "tool_calls",
                       "native_grep_calls", "native_read_calls", "codemesh_calls",
                       "map_calls", "search_calls", "source_calls", "recall_calls", "remember_calls",
                       "search_limit_overrides", "source_full_requests", "source_default_requests",
                       "harness_memory_writes", "handoff_reference_count", "total_observation_bytes",
                       "restore_calls", "harness_hydration_calls", "inspect_calls", "batch_read_calls",
                       "sent_transcript_bytes", "full_history_transcript_bytes",
                       "source_observation_bytes", "source_error_observation_bytes", "repeat_read_calls", "repeat_source_blocks", "repeated_observation_bytes",
                       "same_context_repeat_read_calls", "same_context_repeat_source_blocks", "same_context_repeated_bytes",
                       "cross_context_repeat_read_calls", "cross_context_repeat_source_blocks", "cross_context_repeated_bytes",
                       "evicted_context_repeat_read_calls", "evicted_context_repeat_source_blocks", "evicted_context_repeated_bytes",
                       "conditional_hits", "tool_elapsed_ms")},
                   **{key: total_known([r["totals"] for r in selected], key) for key in (
                       "input_tokens", "uncached_input_tokens", "cache_creation_input_tokens",
                       "cache_read_input_tokens", "output_tokens", "elapsed_ms")}}
            for mode in modes}


def paired(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {(row["task"], row["repeat"], row["mode"]): row for row in rows
              if row["status"] in GRADED_STATUSES}
    pairs = []
    for task, repeat, mode in sorted(by_key):
        if mode != "filesystem": continue
        native = by_key[(task, repeat, "filesystem")]
        mesh = by_key.get((task, repeat, "codemesh"))
        if mesh is None: continue
        pairs.append({"task": task, "repeat": repeat,
                      "filesystem": {"first_patch_pass": native["first_patch_pass"],
                                     "final_patch_pass": native["final_patch_pass"],
                                     "cost_usd": native["totals"]["cost_usd"],
                                     "input_tokens": native["totals"]["input_tokens"]},
                      "codemesh": {"first_patch_pass": mesh["first_patch_pass"],
                                   "final_patch_pass": mesh["final_patch_pass"],
                                   "cost_usd": mesh["totals"]["cost_usd"],
                                   "input_tokens": mesh["totals"]["input_tokens"]}})
    return pairs


def paired_summary(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pairs:
        return None
    count = len(pairs)
    native_pass = sum(pair["filesystem"]["final_patch_pass"] for pair in pairs)
    mesh_pass = sum(pair["codemesh"]["final_patch_pass"] for pair in pairs)
    native_cost = total_cost([pair["filesystem"] for pair in pairs])
    mesh_cost = total_cost([pair["codemesh"] for pair in pairs])
    native_tokens = sum(pair["filesystem"]["input_tokens"] for pair in pairs)
    mesh_tokens = sum(pair["codemesh"]["input_tokens"] for pair in pairs)
    return {"pairs": count, "filesystem_final_pass_rate": native_pass / count,
            "codemesh_final_pass_rate": mesh_pass / count,
            "quality_change_percentage_points": 100 * (mesh_pass - native_pass) / count,
            "filesystem_cost_usd": native_cost, "codemesh_cost_usd": mesh_cost,
            "cost_change_percent": 100 * (mesh_cost / native_cost - 1) if native_cost and mesh_cost is not None else None,
            "filesystem_input_tokens": native_tokens, "codemesh_input_tokens": mesh_tokens,
            "input_token_change_percent": 100 * (mesh_tokens / native_tokens - 1) if native_tokens else None}


def normalize_checkpoint_row(row: dict[str, Any]) -> None:
    """Keep older checkpoint attempts interpretable after runner fixes."""
    if row["status"] == "model_error" and any(
        "prompt is too long" in str(call.get("error") or "").lower() for call in row.get("calls", [])):
        row["status"] = "context_limit"
    actions = [action for phase in row.get("phases", []) for action in phase.get("actions", [])]
    row.update(repeated_reads(row.get("phases", [])))
    row.setdefault("total_observation_bytes", sum(
        action.get("observation_bytes", 0) for action in actions if not action.get("error")))
    row.setdefault("harness_memory_writes", int(
        row["mode"] == "codemesh"
        and any(call.get("kind") in ("handoff", "handoff_retry") and call.get("text", "").strip()
                for call in row.get("calls", []))
        and not any(call.get("kind") == "memory_error" for call in row.get("calls", []))))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "results" / "vnext-agent-working-state")
    parser.add_argument("--tasks", type=Path, default=TASKS)
    parser.add_argument("--python", type=Path, default=Path("/tmp/codemesh-edit-eval-venv/bin/python"))
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--model", help="Defaults to gpt-5.6-terra for Codex or haiku for Claude")
    parser.add_argument("--effort", default="low", choices=("low", "medium", "high"))
    parser.add_argument("--source-mode", choices=("auto", "full"), default="auto")
    parser.add_argument("--context-policy", choices=("full", "working-set", "focused"),
                        help="Codex defaults to working-set; focused is the optional stable-prefix navigation policy; Claude supports full only")
    parser.add_argument("--evidence-bytes", type=int, default=16384,
                        help="Retained exact source/read budget for working-set context (default 16384 bytes)")
    parser.add_argument("--ledger-entries", type=int, default=8,
                        help="Recent compact navigation records retained in working-set context")
    parser.add_argument("--no-handoff-references", dest="handoff_references", action="store_false",
                        help="Ablation: restore narrative handoff without recorded source references")
    parser.add_argument("--hydrate-handoff", dest="hydrate_on_resume", action="store_true",
                        help="Verify saved references and automatically read bounded live source after reset, in both arms")
    parser.add_argument("--bundle-reads", action="store_true",
                        help="Expose inspect to Codemesh and equivalent batched explicit filesystem reads to both arms")
    parser.add_argument("--tool-contract", choices=("legacy", "lean"), default="legacy",
                        help="Instruction/schema treatment; legacy is the unchanged control")
    parser.add_argument("--search-tie-break", choices=("pagerank", "path-depth"), default="pagerank",
                        help="Fixed CodeMesh exact-match tie-break, not a model-selected action option")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--budget-usd", type=float, help="Claude-only per-call limit, default $1; Codex uses turn/time bounds")
    parser.add_argument("--task", action="append")
    parser.add_argument("--run", action="store_true", help="execute paid model trajectories")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.model = args.model or ("gpt-5.6-terra" if args.provider == "codex" else "haiku")
    args.context_policy = args.context_policy or ("working-set" if args.provider == "codex" else "full")
    if args.context_policy in ("working-set", "focused") and args.provider != "codex":
        parser.error("Working state requires the explicit-transcript Codex adapter; use --context-policy full for Claude")
    if args.evidence_bytes < 1 or not 1 <= args.ledger_entries <= 32:
        parser.error("evidence bytes must be positive and ledger entries must be in 1..32")
    if args.hydrate_on_resume and not args.handoff_references:
        parser.error("--hydrate-handoff requires saved references; remove --no-handoff-references")
    if args.provider == "codex" and args.budget_usd is not None:
        parser.error("Codex CLI does not expose a USD budget; use --max-turns")
    if args.provider == "claude" and args.budget_usd is None:
        args.budget_usd = 1.0
    base, out = args.repo.resolve(), args.out.resolve()
    config = json.loads(args.tasks.read_text())
    commit = command(["git", "rev-parse", "HEAD"], base)
    if commit.returncode or commit.stdout.strip() != PIN or config["commit"] != PIN:
        parser.error(f"repository and task manifest must pin {PIN}")
    tasks = [task for task in config["tasks"] if not args.task or task["id"] in args.task]
    modes = args.modes.split(",")
    if (not tasks or not modes or any(mode not in MODES for mode in modes)
            or len(set(modes)) != len(modes) or args.repeats < 1 or args.max_turns < 1
            or (args.budget_usd is not None and args.budget_usd <= 0) or not args.python.is_file()):
        parser.error("invalid task, mode, repeat, budget, or Python environment")
    out.mkdir(parents=True, exist_ok=True)
    if args.run and not args.resume and (out / "runs.jsonl").exists():
        parser.error("output already has trajectories; choose a new --out or use --resume")
    if not args.run:
        indexes = prepare(base, tasks, out, args.python.absolute())
        print(f"Prepared {len(indexes)} pristine-pass/mutant-fail indexed fixtures", flush=True)
        print("Pass --run to execute paired model trajectories.")
        return 0
    cli_version = command([args.provider, "--version"], ROOT).stdout.strip()
    catalog_hash = None
    if args.provider == "codex":
        from codex_runner import catalog_fingerprint
        try:
            catalog_hash = catalog_fingerprint(args.model)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    run_config = {"commit": PIN, "build_fingerprint": build_fingerprint(),
                  "protocol_fingerprint": protocol_fingerprint(args.tasks),
                  "provider": args.provider, "cli_version": cli_version, "effort": args.effort,
                  "model_catalog_sha256": catalog_hash,
                  "context_transport": "explicit_transcript" if args.provider == "codex" else "cli_resume",
                  "context_policy": args.context_policy,
                  "evidence_bytes": args.evidence_bytes,
                  "ledger_entries": args.ledger_entries,
                  "source_mode": args.source_mode,
                  "handoff_references": args.handoff_references,
                  "hydrate_on_resume": args.hydrate_on_resume, "bundle_reads": args.bundle_reads,
                  "tool_contract": args.tool_contract, "search_tie_break": args.search_tie_break,
                  "bundle_limits": {"maxBytes": BUNDLE_BYTES, "maxSymbols": BUNDLE_SYMBOLS},
                  "task_ids": [task["id"] for task in tasks], "modes": modes,
                  "model": args.model, "repeats": args.repeats, "seed": args.seed,
                  "max_turns": args.max_turns, "budget_usd": args.budget_usd}
    manifest = out / "run_config.json"
    if args.resume and manifest.exists() and json.loads(manifest.read_text()) != run_config:
        parser.error("--resume settings differ from the existing run_config.json")
    if args.resume and not manifest.exists() and (out / "runs.jsonl").exists():
        parser.error("cannot resume runs.jsonl without run_config.json")
    indexes = prepare(base, tasks, out, args.python.absolute())
    print(f"Prepared {len(indexes)} pristine-pass/mutant-fail indexed fixtures", flush=True)
    manifest.write_text(json.dumps(run_config, indent=2) + "\n")
    plan = [(task, mode, repeat) for task in tasks for mode in modes
            for repeat in range(1, args.repeats + 1)]
    random.Random(args.seed).shuffle(plan)
    checkpoint = out / "runs.jsonl"
    rows = [json.loads(line) for line in checkpoint.read_text().splitlines()] if args.resume and checkpoint.exists() else []
    for row in rows:
        normalize_checkpoint_row(row)
    if not args.resume: checkpoint.write_text("")
    completed = {(r["task"], r["mode"], r["repeat"]) for r in rows
                 if r["status"] in GRADED_STATUSES}
    halted = False
    for index, (task, mode, repeat) in enumerate(plan, 1):
        if (task["id"], mode, repeat) in completed: continue
        print(f"[{index}/{len(plan)}] {task['id']} {mode} repeat {repeat}", flush=True)
        row = trajectory(base, task, mode, repeat, out, indexes[task["id"]],
                         args.python.absolute(), args.model, args.budget_usd, args.max_turns,
                         args.provider, args.effort, args.source_mode, args.handoff_references,
                         args.context_policy, args.evidence_bytes, args.ledger_entries,
                         args.hydrate_on_resume, args.bundle_reads, args.tool_contract, args.search_tie_break)
        rows.append(row)
        with checkpoint.open("a") as handle: handle.write(json.dumps(row) + "\n")
        cost = row["totals"]["cost_usd"]
        cost_label = f"${cost:.6f}" if cost is not None else "cost unavailable from provider"
        print(f"  {row['status']}; first={row['first_patch_pass']} final={row['final_patch_pass']}; "
              f"{cost_label}", flush=True)
        errors = [str(call.get("error") or "") for call in row["calls"]]
        if row["status"] in ("model_error", "handoff_error", "memory_handoff_error", "context_state_error"):
            print("  Model or harness error; stopping before the next trajectory. See calls.jsonl.", flush=True)
            halted = True
            break
    report = {"repository": config["repository"], "commit": PIN, "model": args.model,
              "provider": args.provider,
              "modes": modes, "repeats": args.repeats, "seed": args.seed,
              "run_config": run_config,
              "tasks": [task["id"] for task in tasks], "results": rows,
              "by_mode": summarize(rows, modes),
              "paired_results": paired(rows),
              "paired_summary": paired_summary(paired(rows)),
              "method": "Paired seeded repairs with two investigation phases, model-written handoff, fresh editing session, and hidden acceptance tests. Codemesh restores its persisted revision-bound note; filesystem restores handoff text. Optional resume hydration verifies saved live source in both arms. Optional bundled reads expose inspect plus explicit batch_read, with equal source-count/byte budgets. Both retain grep/read fallback. Index and pytest CPU are excluded from model USD cost and reported separately. Source bytes are observations, not tokens."}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["by_mode"], indent=2))
    return 2 if halted else 0


if __name__ == "__main__":
    raise SystemExit(main())
