#!/usr/bin/env python3
"""Audit a matrix-driven controlled eval without invoking a model.

The default checks frozen files, exact captured CLI contracts, complete journals,
usage, isolation and source hashes. --source-replay also compares every delivered
source and successful edit to pinned git objects and saved patches. Incomplete
snapshots never certify final matrix coverage.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import difflib
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

GRADED = {"complete", "turn_limit", "context_limit"}
JOURNAL_METADATA = {"attempt_id", "task", "mode", "repeat", "call_index"}
STARTUP_WARNING = ("Code Mode is unavailable because code-mode host is disabled. Code mode will fail "
                   "closed; enable `features.code_mode_host` and install `codex-code-mode-host`.")
TRANSPORT_MARKERS = ("Connection refused", "failed to lookup address information", "timed out",
                     "nodename nor servname", "stream disconnected", "Connection failed: error sending request")


def is_transport_diagnostic(event: dict) -> bool:
    item = event.get("item", {})
    message = item.get("message", "")
    return (event.get("type") == "item.completed" and item.get("type") == "error"
            and isinstance(message, str)
            and message.startswith("Falling back from WebSockets to HTTPS transport. stream disconnected before completion: ")
            and any(marker in message for marker in TRANSPORT_MARKERS))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def known_sum(calls: list[dict], key: str) -> int | float | None:
    values = [c.get(key) for c in calls]
    return sum(values) if all(isinstance(v, (int, float)) for v in values) else None


def load_contract(frozen: Path):
    """Import the frozen, hash-checked implementation; never call its main()."""
    folder = str(frozen / "eval")
    sys.path.insert(0, folder)
    # Avoid accidentally resolving a previously imported working-tree module.
    for name in ("vnext_agent", "codex_runner", "working_state", "focused_state", "tool_contract",
                 "source_bundles", "grep_router", "vnext_contract"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("vnext_agent", frozen / "eval/vnext_agent.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["vnext_agent"] = module
    spec.loader.exec_module(module)
    import codex_runner
    return module, codex_runner


def blocks(action: str, observation: str) -> list[dict]:
    try:
        value = json.loads(observation)
    except (ValueError, TypeError):
        return []
    if not isinstance(value, dict):
        return []
    values = [value] if action == "source" else value.get("sources", [])
    normalized = []
    for item in values:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        for key, alias in (("path", "filePath"), ("hash", "sourceHash")):
            if key not in item:
                item[key] = item.get(alias)
        if "lines" not in item and "lineStart" in item:
            item["lines"] = [item["lineStart"], item["lineEnd"]]
        normalized.append(item)
    return normalized


def prompt_observations(prompt: str) -> tuple[list[dict], list[dict]]:
    """Read only explicit retained evidence and the latest nonhistorical tool result.

    Handoff strings and ledger pointers do not grant source authority.
    """
    transcript = json.loads(prompt.split("\n", 1)[1])
    observations, states = [], []
    decoder = json.JSONDecoder()
    for entry in transcript:
        if entry.get("role") != "user":
            continue
        content = entry["content"]
        if content.startswith("Deterministic harness working state."):
            state = json.loads(content.split("\n", 1)[1])
            states.append(state)
            observations.extend(state.get("retained_evidence", []))
        elif content.startswith("Latest tool observation "):
            rest = content[len("Latest tool observation "):]
            provenance, end = decoder.raw_decode(rest)
            if not provenance.get("replayed") and not provenance.get("error"):
                observation = rest[end + 2:].rsplit("\nChoose the next harness action", 1)[0]
                observations.append({**provenance, "observation": observation})
    return observations, states


class Replay:
    """Read pinned objects and mutate an in-memory file view only."""
    def __init__(self, repo: Path, commit: str, task: dict, cache: dict):
        self.repo, self.commit, self.task, self.cache = repo, commit, task, cache
        self.files: dict[str, str] = {}
        for mutation in task.get("mutations", [task]):
            path, old = mutation["path"], mutation["old"]
            content = self.get(path)
            n = mutation.get("occurrence", 1)
            if content.count(old) < n:
                raise ValueError(f"Mutation anchor missing: {path}")
            pieces = content.split(old, n)
            self.files[path] = old.join(pieces[:-1]) + mutation["mutant"] + pieces[-1]
        self.initial = dict(self.files)

    def get(self, path: str) -> str:
        path = path.removeprefix("./")
        if path == "eval_visible_test.py":
            return self.task["visible_test"]
        if path not in self.files:
            if Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError(f"Invalid source path: {path}")
            key = (self.commit, path)
            if key not in self.cache:
                result = subprocess.run(["git", "show", f"{self.commit}:{path}"], cwd=self.repo,
                                        capture_output=True, check=True)
                self.cache[key] = result.stdout.decode()
            self.files[path] = self.cache[key]
        return self.files[path]

    def source_matches(self, block: dict) -> bool | None:
        if block.get("mode") == "fold":
            return None  # Fold is index signature/summary, not an exact source body.
        content = self.get(block["path"])
        start, end = block["lines"]
        lines = content.split("\n")[start - 1:end]
        omitted = block.get("omittedRanges") or []
        rendered, next_line = [], start
        for span in sorted(omitted, key=lambda value: value["start"]):
            a, b = span["start"], span["end"]
            if not next_line <= a <= b <= end:
                return False
            rendered.extend(lines[next_line - start:a - start])
            rendered.append(f"… [lines {a}-{b} omitted] …")
            next_line = b + 1
        rendered.extend(lines[next_line - start:])
        if block.get("fileHash") and block["fileHash"] != sha256(content.encode()).hexdigest():
            return False
        if block.get("bodyHash") and block["bodyHash"] != sha256("\n".join(lines).encode()).hexdigest():
            return False
        return "\n".join(rendered) == block["source"]

    def read_matches(self, observation: str) -> bool:
        match = re.match(r"(.+) lines (\d+)-(\d+) of (\d+):\n", observation)
        if not match:
            return False
        path, start, end, total = match.groups()
        start, end, total = int(start), int(end), int(total)
        lines = self.get(path).splitlines()
        expected = f"{path} lines {start}-{end} of {len(lines)}:\n" + "\n".join(
            f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
        return total == len(lines) and observation == expected

    def edit(self, decision: dict) -> bool:
        path = decision["path"].removeprefix("./")
        content, old, new = self.get(path), decision["old"], decision["new"]
        self.initial.setdefault(path, content)
        if content.count(old) != 1:
            return False
        self.files[path] = content.replace(old, new, 1)
        return True

    def patch(self) -> str:
        return "".join("".join(difflib.unified_diff(before.splitlines(keepends=True),
            self.files[path].splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}"))
            for path, before in sorted(self.initial.items()))


def audit(root: Path, stage: str = "all", allow_incomplete: bool = False,
          source_replay: bool = False) -> dict:
    root = root.resolve()
    issues, summaries, patch_replays = [], {}, []

    def check(condition, kind, **context):
        if not condition:
            issues.append({"kind": kind, **context})

    def journal(path):
        rows = []
        if not path.exists():
            return rows
        for line, text in enumerate(path.read_text().splitlines(), 1):
            if text.strip():
                try:
                    rows.append((line, json.loads(text)))
                except ValueError:
                    check(False, "malformed_journal_line", path=str(path), line=line)
        return rows

    matrix = read_json(root / "matrix.json")
    check(matrix.get("schema_version") == 1, "matrix_schema_version")
    frozen = root / "frozen_source"
    manifest = read_json(frozen / "manifest.json")
    for relative, metadata in manifest.items():
        path = frozen / relative
        body = path.read_bytes() if path.is_file() else b""
        check(path.is_file() and len(body) == metadata["bytes"] and sha256(body).hexdigest() == metadata["sha256"],
              "frozen_file_mismatch", file=relative)
    runner, adapter = load_contract(frozen)
    build_hash = runner.build_fingerprint()
    runs = [run for run in matrix["runs"] if stage == "all" or run["stage"] == stage]
    check(bool(runs), "empty_stage", stage=stage)
    check(len({run["id"] for run in runs}) == len(runs), "duplicate_matrix_run_id")
    all_artifact_paths, source_cache = set(), {}
    for run in runs:
        folder = root / run["path"]
        config_path = folder / "run_config.json"
        context = {"run": run["id"]}
        expected = run["expected_config"]
        task_manifest = frozen / "eval" / ("graph_workflow_tasks.json" if run["suite"] == "new" else "controlled_tasks.json")
        task_data = read_json(task_manifest)
        tasks = {task["id"]: task for task in task_data["tasks"]}
        expected_keys = {(task, mode, repeat) for task in run["task_ids"] for mode in matrix["arms"]
                         for repeat in range(1, run["repeats"] + 1)}
        check(expected["commit"] == matrix["commit"] == task_data["commit"], "fixture_commit", **context)
        check(expected["task_ids"] == run["task_ids"] and expected["modes"] == matrix["arms"]
              and expected["repeats"] == run["repeats"] and expected["seed"] == run["seed"],
              "matrix_coverage_config", **context)
        check(expected["build_fingerprint"] == build_hash, "build_fingerprint", **context)
        check(expected["protocol_fingerprint"] == runner.protocol_fingerprint(task_manifest), "protocol_fingerprint", **context)
        check(all(expected.get(k) == v for k, v in matrix["variants"][run["variant"]].items()), "variant_config", **context)
        check(all(expected.get(k) == v for k, v in matrix.get("common_config", {}).items()), "common_config", **context)
        if not config_path.exists():
            if not allow_incomplete:
                check(False, "missing_run_config", **context)
            summaries[run["id"]] = {"graded_trajectories": 0, "expected_trajectories": len(expected_keys),
                                   "missing_trajectory_keys": sorted(expected_keys), "journal_calls_without_attempt_row": 0}
            continue
        config = read_json(config_path)
        check(config == expected, "run_config_difference", **context)
        rows, calls = journal(folder / "runs.jsonl"), journal(folder / "calls.jsonl")
        row_values = [r for _, r in rows]
        graded = [r for r in row_values if r["status"] in GRADED]
        observed = Counter((r["task"], r["mode"], r["repeat"]) for r in graded)
        missing = sorted(expected_keys - observed.keys())
        check(not observed.keys() - expected_keys, "unexpected_graded_key", **context)
        check(all(count == 1 for count in observed.values()), "duplicate_graded_key", **context)
        if not allow_incomplete:
            check(not missing, "missing_graded_trajectories", missing=missing, **context)
        check(len({r["attempt_id"] for r in row_values}) == len(rows), "duplicate_attempt_id", **context)
        call_map, row_call_keys, input_bodies = {}, set(), {}
        ungraded_ids = {r["attempt_id"] for r in row_values if r["status"] not in GRADED}
        graded_ids = {r["attempt_id"] for r in graded}
        hidden_identities = []
        for task in tasks.values():
            hidden = frozen / task["hidden_test"]
            hidden_identities.extend([task["hidden_test"], hidden.name])
            hidden_identities.extend(re.findall(r"^def (test_\w+)", hidden.read_text(), re.M))
        artifact_count = 0
        transport_diagnostics, provider_failures, metadata_warnings, resolved_model_ids = [], [], [], set()
        for line, call in calls:
            ctx = {**context, "line": line, "attempt_id": call["attempt_id"]}
            key = (call["attempt_id"], call["call_index"])
            check(key not in call_map, "duplicate_journal_call", **ctx)
            call_map[key] = call
            check(call.get("requested_model") == config["model"], "requested_model", **ctx)
            check(call.get("model_catalog_sha256") == config["model_catalog_sha256"], "call_catalog_hash", **ctx)
            check(call.get("router_error_count") == 0 and call.get("router_activity_valid"), "router_activity", **ctx)
            check(call.get("cost_usd") is None, "unavailable_billing_claim", **ctx)
            resolved_model_ids.update(call.get("model_ids") or [])
            if "Unknown model gpt-5.6-luna is used. This will use fallback model metadata." in call.get("stderr", ""):
                metadata_warnings.append({"attempt_id": call["attempt_id"], "call_index": call["call_index"], "line": line})
            if not (call.get("call_valid") and not call.get("error") and call.get("usage_available")):
                text = str(call.get("error", "")) + call.get("stderr", "")
                transport = any(marker in text for marker in TRANSPORT_MARKERS)
                accounted = call["attempt_id"] in ungraded_ids or (allow_incomplete and call["attempt_id"] not in graded_ids)
                check(accounted and transport, "unclassified_invalid_call", **ctx)
                provider_failures.append({"attempt_id": call["attempt_id"], "call_index": call["call_index"],
                    "line": line, "classification": "timeout" if "timed out" in text else "transport" if transport else "unclassified",
                    "usage_available": call.get("usage_available"),
                    "known_input_tokens": call.get("input_tokens") if call.get("usage_available") else None,
                    "known_output_tokens": call.get("output_tokens") if call.get("usage_available") else None,
                    "error_tail": str(call.get("error", ""))[-1800:]})
            if call["attempt_id"] in graded_ids:
                check(call.get("call_valid") and not call.get("error") and call.get("usage_available"), "invalid_graded_call", **ctx)
            started = False
            for event in call.get("raw_events", []):
                if event.get("type") == "turn.started":
                    started = True
                if str(event.get("type", "")).startswith("item."):
                    item = event.get("item", {})
                    startup = (not started and event["type"] == "item.completed" and item.get("type") == "error"
                               and item.get("message") == STARTUP_WARNING)
                    transport_diagnostic = (call["attempt_id"] in ungraded_ids and not call.get("call_valid")
                                            and is_transport_diagnostic(event))
                    if transport_diagnostic:
                        transport_diagnostics.append({"attempt_id": call["attempt_id"], "call_index": call["call_index"],
                                                      "line": line, "event": event})
                    check(item.get("type") in ("reasoning", "agent_message") or startup or transport_diagnostic,
                          "unexpected_native_item", item_type=item.get("type"), **ctx)
            if call.get("usage_available"):
                usages = call.get("raw_usage", [])
                usage = usages[0] if len(usages) == 1 else {}
                check(all(usage.get(raw) == call.get(normal) for raw, normal in (
                    ("input_tokens", "input_tokens"), ("cached_input_tokens", "cache_read_input_tokens"),
                    ("output_tokens", "output_tokens"))), "raw_usage_difference", **ctx)
                check(call.get("uncached_input_tokens") == call.get("input_tokens", 0) - call.get("cache_read_input_tokens", 0),
                      "uncached_usage_difference", **ctx)
            names = {"stdin.txt", "instructions.txt", "model-catalog.json", "invocation.json"}
            if call.get("kind") == "decision":
                names.add("response.schema.json")
            files = call.get("input_artifacts", {}).get("files", {})
            check(set(files) == names, "input_artifact_set", **ctx)
            bodies = {}
            for name, metadata in files.items():
                path = Path(metadata["path"])
                body = path.read_bytes() if path.is_file() else b""
                artifact_count += 1
                check(path.is_file() and len(body) == metadata["bytes"] and sha256(body).hexdigest() == metadata["sha256"],
                      "input_artifact_integrity", file=str(path), **ctx)
                check(path.resolve().is_relative_to((folder / "inputs").resolve()), "input_path_outside_run", **ctx)
                check(str(path) not in all_artifact_paths, "input_path_reused", **ctx)
                all_artifact_paths.add(str(path))
                bodies[name] = body
            if not names <= bodies.keys():
                continue
            input_bodies[key] = bodies
            system = runner.system_prompt(call["mode"], config["source_mode"], config["context_policy"],
                                          config["bundle_reads"], config["tool_contract"])
            check(bodies["instructions.txt"] == (adapter.PROTOCOL + "\n\n" + system).encode(), "captured_instructions_difference", **ctx)
            if "response.schema.json" in bodies:
                schema = json.loads(runner.schema(call["mode"], config["context_policy"], config["bundle_reads"], config["tool_contract"]))
                check(bodies["response.schema.json"] == json.dumps(adapter.strict_schema(schema)).encode(), "captured_schema_difference", **ctx)
            catalog = json.loads(bodies["model-catalog.json"])
            check(sha256(bodies["model-catalog.json"]).hexdigest() == config["model_catalog_sha256"], "captured_catalog_hash", **ctx)
            models = catalog.get("models", [])
            check(len(models) == 1 and models[0].get("slug") == config["model"] and models[0].get("tool_mode") == "direct"
                  and models[0].get("shell_type") == "disabled" and models[0].get("apply_patch_tool_type") is None
                  and models[0].get("experimental_supported_tools") == [] and not models[0].get("supports_search_tool", False),
                  "captured_native_tool_policy", **ctx)
            invocation = json.loads(bodies["invocation.json"])
            check(invocation.get("requested_model") == config["model"] and invocation.get("reasoning_effort") == config["effort"],
                  "invocation_identity", **ctx)
            argv = invocation.get("argv_template", [])
            settings = {argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "--config"}
            critical_settings = {'approval_policy="never"', 'web_search="disabled"', "mcp_servers={}",
                "project_doc_max_bytes=0", "skills.include_instructions=false", "orchestrator.skills.enabled=false",
                "orchestrator.mcp.enabled=false", "include_environment_context=false", "include_apps_instructions=false",
                "include_collaboration_mode_instructions=false", "include_permissions_instructions=false",
                "tools.update_plan.enabled=false", "tools.experimental_request_user_input.enabled=false",
                "features.skip_host_skill_discovery=true"}
            critical_settings.update(f"features.{feature}=false" for feature in adapter.DISABLED_FEATURES)
            check(critical_settings <= settings and all(flag in argv for flag in (
                "--ignore-user-config", "--ignore-rules", "--ephemeral", "--strict-config", "--skip-git-repo-check"))
                and ("--cd", "${CALL_TEMP_DIR}/workspace") in set(zip(argv, argv[1:]))
                and ("--sandbox", "read-only") in set(zip(argv, argv[1:])),
                "invocation_isolation_policy", **ctx)
            check(invocation.get("protocol_sha256") == sha256(adapter.PROTOCOL.encode()).hexdigest(), "invocation_protocol_hash", **ctx)
            check("error=code-mode host is disabled" not in call.get("stderr", ""), "blocked_router_stderr", **ctx)
            check(len(bodies["stdin.txt"]) == call.get("prompt_context", {}).get("sent_transcript_bytes"), "captured_transcript_size", **ctx)
            combined = (bodies["stdin.txt"] + bodies["instructions.txt"]).decode()
            check(not any(value in combined for value in hidden_identities), "hidden_judge_identity_leak", **ctx)
        source_counts = Counter()
        last_patch_owner = {r["patch_path"]: r["attempt_id"] for r in row_values}
        patch_artifacts = []
        in_flight_keys = {(c["task"], c["mode"], c["repeat"]) for _, c in calls
                          if c["attempt_id"] not in {r["attempt_id"] for r in row_values}}
        for line, row in rows:
            ctx = {**context, "row_line": line, "attempt_id": row["attempt_id"]}
            for key in ("model", "provider", "context_policy", "hydrate_on_resume", "bundle_reads", "tool_contract", "search_tie_break"):
                check(row.get(key) == config.get(key), "trajectory_config_difference", field=key, **ctx)
            for index, call in enumerate(row["calls"], 1):
                if call.get("kind") == "memory_error":
                    continue
                key = (row["attempt_id"], index)
                row_call_keys.add(key)
                entry = call_map.get(key)
                check(entry is not None and {k: v for k, v in entry.items() if k not in JOURNAL_METADATA} == call,
                      "journal_row_call_difference", call_index=index, **ctx)
                if entry:
                    check((entry["task"], entry["mode"], entry["repeat"]) == (row["task"], row["mode"], row["repeat"]),
                          "journal_trajectory_key_difference", **ctx)
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "elapsed_ms"):
                check(row["totals"][key] == sum(c.get(key) or 0 for c in row["calls"]), "trajectory_total_difference", metric=key, **ctx)
            for key in ("uncached_input_tokens", "cache_creation_input_tokens", "cost_usd"):
                eligible = [c for c in row["calls"] if c.get("kind") != "memory_error"] if key != "cost_usd" else row["calls"]
                check(row["totals"][key] == known_sum(eligible, key), "nullable_trajectory_total_difference", metric=key, **ctx)
            replay = Replay(Path(matrix["repo"]), matrix["commit"], tasks[row["task"]], source_cache) if source_replay else None
            phases = {p["phase"]: p for p in row["phases"]}
            action_indices = {phase: 0 for phase in phases}
            actions_by_handle = {a["archive_handle"]: a for p in row["phases"] for a in p["actions"]
                                 if a.get("archive_handle")}

            def observation_check(action, observation, *, delivered=False):
                source_counts["delivered_observations" if delivered else "action_observations"] += 1
                for block in blocks(action, observation):
                    if block.get("status") != "fresh":
                        check(not block.get("source"), "nonfresh_source_body", **ctx)
                        continue
                    source = block.get("source")
                    check(isinstance(source, str) and sha256(source.encode()).hexdigest() == block.get("hash"), "source_hash_difference", **ctx)
                    source_counts["delivered_source_blocks" if delivered else "source_blocks"] += 1
                    if replay and isinstance(source, str):
                        try:
                            matched = replay.source_matches(block)
                            source_counts["fold_blocks_skipped" if matched is None else "source_blocks_replayed"] += 1
                            check(matched is not False, "source_content_difference", path=block.get("path"), **ctx)
                        except (KeyError, ValueError, subprocess.CalledProcessError, IndexError) as exc:
                            check(False, "source_replay_error", error=str(exc), **ctx)
                if replay and action == "read":
                    source_counts["numbered_reads_replayed"] += 1
                    try:
                        check(replay.read_matches(observation), "numbered_read_difference", **ctx)
                    except (KeyError, ValueError, subprocess.CalledProcessError, IndexError) as exc:
                        check(False, "read_replay_error", error=str(exc), **ctx)

            for index, call in enumerate(row["calls"], 1):
                phase = call.get("phase")
                if phase in phases and call.get("kind") == "decision":
                    actions = phases[phase]["actions"]
                    while action_indices[phase] < len(actions) and actions[action_indices[phase]]["action"] == "hydrate":
                        action = actions[action_indices[phase]]
                        if not action.get("error"):
                            observation_check(action["action"], action.get("observation", ""))
                        action_indices[phase] += 1
                bodies = input_bodies.get((row["attempt_id"], index))
                resident = set()
                if bodies:
                    try:
                        observations, states = prompt_observations(bodies["stdin.txt"].decode())
                        check(len(states) == 1, "missing_working_state", call_index=index, **ctx)
                        for item in observations:
                            observation_check(item["action"], item["observation"], delivered=True)
                            for block in blocks(item["action"], item["observation"]):
                                if block.get("status") == "fresh" and isinstance(block.get("source"), str):
                                    resident.add((block.get("id"), block.get("mode"), block.get("hash")))
                                    origin = actions_by_handle.get(item.get("handle"), {})
                                    if origin.get("action") == "source" and origin.get("input", {}).get("node"):
                                        resident.add((origin["input"]["node"], block.get("mode"), block.get("hash")))
                    except (ValueError, KeyError, TypeError) as exc:
                        check(False, "working_state_parse", error=str(exc), **ctx)
                if call.get("kind") != "decision" or call.get("error") or phase not in phases:
                    continue
                decision = call.get("decision", {})
                actions = phases[phase]["actions"]
                if action_indices[phase] >= len(actions):
                    check(False, "decision_without_action", call_index=index, **ctx)
                    continue
                action = actions[action_indices[phase]]
                action_indices[phase] += 1
                expected_action = ("answer" if action["action"] == "premature_answer" else
                                   decision.get("action") if action["action"] == "early_action" else action["action"])
                check(decision.get("action") == expected_action and
                      ("input" not in action or action["input"] == decision),
                      "decision_action_difference", call_index=index, **ctx)
                if decision.get("action") == "source" and decision.get("if_hash") and not action.get("error"):
                    check((decision.get("node"), decision.get("mode") or config["source_mode"], decision["if_hash"]) in resident,
                          "conditional_source_without_resident_bytes", call_index=index, **ctx)
                if not action.get("error") and "observation" in action:
                    observation_check(action["action"], action["observation"])
                if action["action"] == "edit" and not action.get("error") and replay:
                    check(replay.edit(action["input"]), "edit_anchor_difference", **ctx)
                    source_counts["edits_replayed"] += 1
            for phase, position in action_indices.items():
                check(position == len(phases[phase]["actions"]), "unaccounted_phase_actions", phase=phase, **ctx)
            if replay:
                patch_path = Path(row["patch_path"])
                derived = replay.patch()
                relative = f"derived-patches/{row['attempt_id']}.diff"
                patch_replays.append({"path": relative, "body": derived})
                latest = last_patch_owner[row["patch_path"]] == row["attempt_id"]
                inflight = (row["task"], row["mode"], row["repeat"]) in in_flight_keys
                verify_original = latest and not (allow_incomplete and inflight)
                if verify_original:
                    check(patch_path.is_file() and patch_path.read_text() == derived, "replayed_patch_difference", **ctx)
                patch_artifacts.append({"attempt_id": row["attempt_id"], "original_path": str(patch_path),
                    "original_status": "verified_latest_owner" if verify_original else
                        "deferred_in_flight_retry" if latest else "overwritten_by_later_attempt",
                    "derived_path": relative, "derived_sha256": sha256(derived.encode()).hexdigest(),
                    "derived_bytes": len(derived.encode()), "provenance": "Reconstructed from pinned fixture plus successful exact edit journal; not the original saved artifact."})
        orphaned = set(call_map) - row_call_keys
        if not allow_incomplete:
            check(not orphaned, "journal_calls_without_attempt_row", calls=sorted(orphaned), **context)
        summaries[run["id"]] = {"attempts": len(rows), "graded_trajectories": len(graded),
            "expected_trajectories": len(expected_keys), "missing_trajectory_keys": missing,
            "final_hidden_pass": sum(bool(r["final_patch_pass"]) for r in graded),
            "workflow_complete": sum(r["status"] == "complete" for r in graded),
            "calls": len(calls), "artifacts": artifact_count, "source_checks": dict(source_counts),
            "patch_artifacts": patch_artifacts, "provider_failures": provider_failures,
            "transport_diagnostic_items": transport_diagnostics,
            "metadata_warning": {"message": "Unknown model gpt-5.6-luna is used. This will use fallback model metadata.",
                "count": len(metadata_warnings), "calls": metadata_warnings,
                "interpretation": "CLI metadata warning only; captured requested model, argv and catalog are verified separately. It does not establish the provider-resolved model."},
            "provider_reported_model_ids": sorted(resolved_model_ids),
            "journal_calls_without_attempt_row": len(orphaned),
            "ungraded_attempts": [{"attempt_id": r["attempt_id"], "status": r["status"]} for r in row_values if r["attempt_id"] in ungraded_ids],
            "calls_with_unknown_usage": sum(not c.get("usage_available") for _, c in calls),
            "known_input_tokens_all_attempts": sum(c.get("input_tokens") or 0 for _, c in calls),
            "known_output_tokens_all_attempts": sum(c.get("output_tokens") or 0 for _, c in calls)}
    complete = bool(summaries) and all(not s["missing_trajectory_keys"] and not s["journal_calls_without_attempt_row"] for s in summaries.values())
    return {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "status": "FAIL" if issues else "PASS",
        "stage": stage, "passed": not issues and complete, "coverage_required": not allow_incomplete, "complete_matrix": complete,
        "source_replay": source_replay, "frozen_files": len(manifest), "build_fingerprint": build_hash,
        "runs": summaries, "issues": issues, "_patch_replays": patch_replays,
        "limits": ["Captured artifacts are CLI inputs, not provider HTTP requests; USD billing is unavailable.",
            "Requested model and captured catalog are checked; provider-resolved identity may be unavailable.",
            "Hidden isolation scans filenames, paths and function names, not semantic leakage.",
            "Fold source is hash checked but excluded from exact source replay because it contains an index summary.",
            "An incomplete snapshot does not certify final coverage; transport failures are preserved separately from graded runs.",
            "Earlier retry patch files can be overwritten because their filenames lack attempt IDs; derived patches are reconstructed from exact edit journals and explicitly labeled.",
            "Recognized provider transport diagnostic error items are classified separately only in failed ungraded attempts; actual native tool items remain integrity failures."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stage", choices=("pilot", "full", "all"), default="all")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--source-replay", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    result = audit(args.root, args.stage, args.allow_incomplete, args.source_replay)
    replays = result.pop("_patch_replays", [])
    result["derived_patch_artifacts_written"] = bool(replays) and not args.no_write
    if not args.no_write:
        for replay in replays:
            path = args.root / replay["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(replay["body"])
        (args.root / f"VALIDATION-{args.stage}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return int(result["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
