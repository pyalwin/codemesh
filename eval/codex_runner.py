"""Isolated Codex CLI decisions for the explicit-action evaluation harness.

Verified against codex-cli 0.155.1. No live model calls occur on import. The
harness supplies the complete transcript on every invocation; Codex sessions
are ephemeral. A scoped model catalog preserves the Luna model slug while
selecting direct tool mode and disabling shell, patch, and experimental tools.
Feature gates disable the remaining native surfaces. Any unexpected item or
router failure invalidates the call even if a valid final answer follows it.
The read-only sandbox and catalog are boundaries, not proof of clean execution.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any


# Names verified with `codex features list`; config fields are also present in
# the installed binary and https://developers.openai.com/codex/config-schema.json.
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "shell_snapshot", "code_mode", "code_mode_only",
    "code_mode_host", "apps", "plugins", "hooks", "multi_agent", "multi_agent_v2",
    "browser_use", "browser_use_external", "computer_use", "view_image",
    "image_generation", "goals", "sleep_tool", "memories", "skill_search",
    "skill_mcp_dependency_install", "tool_suggest", "workspace_dependencies",
    "request_permissions_tool", "standalone_web_search", "context_management",
    "current_time_reminder", "deferred_executor", "token_budget",
)

PROTOCOL = (
    "You are the decision model inside an evaluation harness. The harness owns all "
    "file access, tools, edits, tests, and conversation state. Never call a native "
    "Codex tool. Do not call code_mode or any internal tool router. The action "
    "names describe JSON data for the harness; they are not callable tools. "
    "Read the supplied ordered transcript as the conversation and "
    "respond only to its final user message. Previous assistant entries are prior "
    "decisions; user entries include task instructions and tool observations. "
    "Do not repeat earlier responses. If an output schema is supplied, emit "
    "exactly one JSON action matching it, without tool calls or surrounding text. "
    "Use null for optional parameters unless the task requires an explicit value. "
    "If no output schema is supplied, return only the requested plain text handoff."
)

DISABLED_CODE_MODE_WARNING = (
    "Code Mode is unavailable because code-mode host is disabled. Code mode will fail "
    "closed; enable `features.code_mode_host` and install `codex-code-mode-host`."
)

CODE_MODE_REJECTION = "error=code-mode host is disabled"

MODEL_CATALOG = Path(__file__).with_name("codex_luna_catalog.json")
# Canonical sorted JSON hash of the source gpt-6-luna entry in the installed
# 0.155.1 catalog on 2026-10-18. The checked-in subset preserves the model,
# reasoning, and context metadata; tool policy is explicitly restricted.
# The catalog requires a fallback instruction template; each call replaces it
# with PROTOCOL plus the experiment's system prompt via model_instructions_file.
# model_catalog_json is a documented startup override; tool_mode='direct',
# shell_type='disabled', and nullable apply_patch_tool_type are ModelInfo fields.
# https://developers.openai.com/codex/config-schema.json
# https://github.com/openai/codex/blob/main/codex-rs/protocol/src/openai_models.rs
MODEL_CATALOG_SOURCE_SHA256 = "863dfa45eddb22ef3c05fada317024e116ab8cc145a7bb348885aade080cbcdb"
TERRA_MODEL_CATALOG = Path(__file__).with_name("codex_terra_catalog.json")
# Canonical json.dumps(entry, sort_keys=True) hash of the real gpt-5.6-terra
# entry from models_cache.json fetched 2026-10-18T18:42:57.409091Z. All metadata
# is preserved except the same five native-tool fields and fallback instruction
# template restricted in the Luna catalog. Neither file relabels another model.
TERRA_MODEL_CATALOG_SOURCE_SHA256 = "fac8a277d15864b18651c6698fad4ffb7e7bafe6e72a88e2ca1b542f60942712"


def _catalog_path(model: str) -> Path:
    if model == "gpt-6-luna":
        return MODEL_CATALOG
    if model == "gpt-5.6-terra":
        return TERRA_MODEL_CATALOG
    raise ValueError(f"Controlled model catalog does not contain requested model {model!r}")


def catalog_source_fingerprint(model: str = "gpt-6-luna") -> str:
    """Fingerprint the actual source entry, before native-tool restrictions."""
    _catalog_path(model)  # Reject unknown identities rather than borrowing metadata.
    return (MODEL_CATALOG_SOURCE_SHA256 if model == "gpt-6-luna"
            else TERRA_MODEL_CATALOG_SOURCE_SHA256)


def _catalog_bytes(model: str) -> bytes:
    data = _catalog_path(model).read_bytes()
    entries = json.loads(data).get("models", [])
    if len(entries) != 1 or entries[0].get("slug") != model:
        raise ValueError(f"Controlled model catalog does not contain requested model {model!r}")
    entry = entries[0]
    if (entry.get("tool_mode") != "direct" or entry.get("shell_type") != "disabled"
            or entry.get("apply_patch_tool_type") is not None
            or entry.get("experimental_supported_tools") != []
            or entry.get("supports_search_tool", False)):
        raise ValueError("Controlled model catalog must disable native tool capabilities")
    return data


def catalog_fingerprint(model: str = "gpt-6-luna") -> str:
    """Fingerprint the exact scoped catalog for run manifests and resume checks."""
    return sha256(_catalog_bytes(model)).hexdigest()


def router_diagnostics(stderr: str) -> dict[str, Any]:
    """Count observed rejection diagnostics, never infer model retries or tokens."""
    rejections = stderr.count(CODE_MODE_REJECTION)
    router_lines = []
    unexpected = []
    for line in stderr.splitlines():
        known_rejection = CODE_MODE_REJECTION in line
        router_error = "::tools::router" in line and re.search(r"\bERROR\b|\berror=", line, re.IGNORECASE)
        if known_rejection or router_error:
            router_lines.append(line)
            if not known_rejection:
                unexpected.append(line)
    count = rejections + len(unexpected)
    return {"router_rejection_count": rejections,
            "unexpected_router_error_count": len(unexpected),
            "router_error_count": count, "router_error_lines": router_lines,
            "router_activity_valid": count == 0}


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make optional fields nullable for OpenAI's strict structured output schema."""
    result = deepcopy(schema)
    if result.get("type") == "object":
        required = set(result.get("required", []))
        properties = result.get("properties", {})
        for name, field in list(properties.items()):
            field = strict_schema(field)
            if name not in required:
                field = {"anyOf": [field, {"type": "null"}]}
            properties[name] = field
        result["required"] = list(properties)
        result["additionalProperties"] = False
    if isinstance(result.get("items"), dict):
        result["items"] = strict_schema(result["items"])
    for keyword in ("anyOf", "oneOf", "allOf"):
        if keyword in result:
            result[keyword] = [strict_schema(item) for item in result[keyword]]
    return result


def _decision(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Structured output must be an object")
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    if required - value.keys():
        raise ValueError("Structured output is missing required fields")
    if schema.get("additionalProperties") is False and value.keys() - properties.keys():
        raise ValueError("Structured output has unexpected fields")
    # Optional nulls are an output-schema transport convention, not harness args.
    value = {key: item for key, item in value.items() if item is not None or key in required}
    python_types = {"string": str, "integer": int, "number": (int, float),
                    "boolean": bool, "object": dict, "array": list}
    for key, item in value.items():
        field = properties.get(key, {})
        kind = field.get("type")
        if isinstance(kind, str) and kind in python_types:
            if not isinstance(item, python_types[kind]) or (kind in ("integer", "number") and isinstance(item, bool)):
                raise ValueError(f"Structured field {key!r} has an invalid type")
        if "enum" in field and item not in field["enum"]:
            raise ValueError(f"Structured field {key!r} is outside its enum")
    return value


def parse_events(stdout: str, *, schema: dict[str, Any] | None, model: str,
                 exit_code: int, stderr: str, elapsed_ms: int) -> dict[str, Any]:
    """Fail closed on native tool activity and retain the original event evidence."""
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    router = router_diagnostics(stderr)
    if not router["router_activity_valid"]:
        errors.append(f"Unexpected internal tool router activity: {router['router_rejection_count']} "
                      f"code-mode host rejection(s), {router['unexpected_router_error_count']} other router error(s)")
    unparsed: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("Expected a JSON object")
            events.append(event)
        except (ValueError, json.JSONDecodeError):
            unparsed.append(line)
    if unparsed:
        errors.append("Codex emitted malformed JSONL")

    thread_id = None
    messages: list[str] = []
    usages: list[dict[str, Any]] = []
    completed = 0
    turn_started = False
    diagnostics: list[dict[str, Any]] = []
    for event in events:
        kind = event.get("type")
        if kind == "thread.started":
            thread_id = event.get("thread_id")
        elif kind == "turn.started":
            turn_started = True
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            item_kind = item.get("type") if isinstance(item, dict) else None
            if (not turn_started and kind == "item.completed" and item_kind == "error"
                    and item.get("message") == DISABLED_CODE_MODE_WARNING):
                # This exact startup diagnostic confirms our disabled host. A
                # generic error or the same message during a turn remains fatal.
                diagnostics.append(event)
            elif item_kind not in ("reasoning", "agent_message"):
                errors.append(f"Unexpected native tool/item activity: {item_kind!r}")
            elif kind == "item.completed" and item_kind == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    messages.append(text)
        elif kind == "turn.completed":
            completed += 1
            if isinstance(event.get("usage"), dict):
                usages.append(event["usage"])
        elif kind in ("error", "turn.failed"):
            error = event.get("error") or event.get("message") or kind
            errors.append(error.get("message", str(error)) if isinstance(error, dict) else str(error))
        else:
            errors.append(f"Unexpected Codex event type: {kind!r}")
    if exit_code:
        errors.append(f"Codex exited with status {exit_code}: {stderr[-1200:]}")
    if completed != 1:
        errors.append(f"Expected exactly one completed turn; received {completed}")

    usage = usages[0] if len(usages) == 1 else {}
    usage_available = all(type(usage.get(key)) is int and usage[key] >= 0
                          for key in ("input_tokens", "cached_input_tokens", "output_tokens"))
    cache_write = usage.get("cache_write_input_tokens")
    if "cache_write_input_tokens" in usage and (type(cache_write) is not int or cache_write < 0):
        usage_available = False
    if usage_available and usage["cached_input_tokens"] + (cache_write or 0) > usage["input_tokens"]:
        usage_available = False
    if not usage_available:
        errors.append("Missing or invalid Codex token usage")
    text = messages[-1] if messages else ""
    decision = None
    if not text.strip():
        errors.append("Invalid model response: no final text")
    elif schema is not None:
        try:
            decision = _decision(text, schema)
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Invalid structured response: {exc}")

    row = {
        "decision": decision if not errors else None, "text": text,
        "session_id": thread_id, "requested_model": model, "model_ids": [],
        # Codex input_tokens already INCLUDES the cached subset: never add it twice.
        "input_tokens": usage.get("input_tokens", 0) if usage_available else 0,
        "uncached_input_tokens": usage["input_tokens"] - usage["cached_input_tokens"] - cache_write
        if usage_available and cache_write is not None else None,
        "cache_read_input_tokens": usage.get("cached_input_tokens", 0) if usage_available else 0,
        "cache_creation_input_tokens": cache_write if usage_available else None,
        "cache_creation_accounting": "reported" if usage_available and cache_write is not None else "not_reported",
        "uncached_input_accounting": "excludes_cache_reads_and_writes" if usage_available and cache_write is not None else "unavailable",
        "output_tokens": usage.get("output_tokens", 0) if usage_available else 0,
        "usage_available": usage_available, "cost_usd": None, "cost_basis": "unavailable",
        "elapsed_ms": elapsed_ms, "exit_code": exit_code,
        "call_valid": not errors, **router,
        "raw_events": events, "raw_usage": usages, "stderr": stderr, "diagnostics": diagnostics,
    }
    if unparsed:
        row["unparsed_stdout"] = unparsed
    if errors:
        row["error"] = "; ".join(dict.fromkeys(errors))
    return row


class InputArtifactError(RuntimeError):
    """Input capture failed before the model could be invoked."""


def _capture_inputs(directory: Path, payloads: dict[str, bytes]) -> dict[str, Any]:
    """Persist exact inputs without replacing existing evidence.

    Identical artifacts may be reused after an interrupted attempt. Conflicting
    or partial files are left intact and fail closed; callers can choose a new
    call directory. No authentication or environment values are inspected.
    """
    try:
        directory = directory.absolute()
        directory.mkdir(parents=True, exist_ok=True)
        unexpected = {path.name for path in directory.iterdir()} - payloads.keys()
        if unexpected:
            raise InputArtifactError(f"Unexpected files in input artifact directory: {sorted(unexpected)}")
        # Check all pre-existing files before creating any missing ones.
        for name, data in payloads.items():
            path = directory / name
            if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != data)):
                raise InputArtifactError(f"Input artifact conflicts with existing evidence: {path}")
        for name, data in payloads.items():
            path = directory / name
            try:
                with path.open("xb") as handle:
                    handle.write(data)
            except FileExistsError:
                # A concurrent writer must also have supplied identical bytes.
                pass
            if path.is_symlink() or path.read_bytes() != data:
                raise InputArtifactError(f"Input artifact verification failed: {path}")
        return {"directory": str(directory), "scope": "harness_inputs",
                "provider_request_captured": False,
                "files": {name: {"path": str(directory / name), "sha256": sha256(data).hexdigest(),
                                 "bytes": len(data)} for name, data in payloads.items()}}
    except OSError as exc:
        raise InputArtifactError(f"Could not persist model input artifacts in {directory}: {exc}") from exc


def model_call(transcript: list[dict[str, str]], *, system: str,
               schema: dict[str, Any] | None, model: str = "gpt-6-luna",
               effort: str = "low", timeout: float = 180,
               cwd: Path | None = None, artifact_dir: Path | None = None) -> dict[str, Any]:
    """Make one explicit-transcript decision using existing Codex authentication.

    cwd controls only the subprocess launch directory. The model-visible working
    directory is always a fresh empty directory, preventing project config and
    repository access from becoming an implicit observation source.

    artifact_dir optionally archives the exact harness inputs before invocation.
    Capture failures raise InputArtifactError without calling the model. These
    artifacts describe the CLI input, not the CLI's final provider wire request.
    """
    if not transcript or transcript[-1].get("role") != "user":
        raise ValueError("Transcript must end in the current user message")
    for entry in transcript:
        if entry.get("role") not in ("user", "assistant") or not isinstance(entry.get("content"), str):
            raise ValueError("Transcript entries require a user/assistant role and string content")
    catalog = _catalog_bytes(model)
    catalog_hash = sha256(catalog).hexdigest()
    catalog_source_hash = catalog_source_fingerprint(model)
    prompt = "Continue this explicit conversation transcript:\n" + json.dumps(transcript, ensure_ascii=False)
    instruction_bytes = (PROTOCOL + "\n\n" + system).encode("utf-8")
    schema_bytes = json.dumps(strict_schema(schema)).encode("utf-8") if schema is not None else None
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="codemesh-codex-decision-") as temporary:
        directory = Path(temporary)
        isolated_cwd = directory / "workspace"
        isolated_cwd.mkdir()
        instructions = directory / "instructions.txt"
        instructions.write_bytes(instruction_bytes)
        # Startup-scoped only: neither the global catalog nor auth is changed.
        catalog_file = directory / "model-catalog.json"
        catalog_file.write_bytes(catalog)
        argv = ["codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                "--skip-git-repo-check", "--sandbox", "read-only", "--json", "--color", "never",
                "--strict-config", "--model", model, "--cd", str(isolated_cwd)]
        settings = [
            'approval_policy="never"', 'web_search="disabled"', "mcp_servers={}",
            "suppress_unstable_features_warning=true",
            "project_doc_max_bytes=0", "skills.include_instructions=false",
            "orchestrator.skills.enabled=false", "orchestrator.mcp.enabled=false",
            "include_environment_context=false", "include_apps_instructions=false",
            "include_collaboration_mode_instructions=false", "include_permissions_instructions=false",
            "tools.update_plan.enabled=false", "tools.experimental_request_user_input.enabled=false",
            "features.skip_host_skill_discovery=true",
            "model_reasoning_effort=" + json.dumps(effort),
            "model_instructions_file=" + json.dumps(str(instructions)),
            "model_catalog_json=" + json.dumps(str(catalog_file)),
        ]
        settings += [f"features.{feature}=false" for feature in DISABLED_FEATURES]
        for setting in settings:
            argv.extend(["--config", setting])
        if schema_bytes is not None:
            schema_file = directory / "response.schema.json"
            schema_file.write_bytes(schema_bytes)
            argv.extend(["--output-schema", str(schema_file)])
        argv.append("-")
        artifacts = None
        if artifact_dir is not None:
            # Normalize only the generated temporary paths. This makes the
            # configuration reproducible across attempts while preserving every
            # actual flag/value and pointing to the corresponding saved bytes.
            config = {"format": "codemesh-codex-input-v1", "scope": "harness_inputs",
                      "provider_request_captured": False, "encoding": "utf-8",
                      "requested_model": model, "reasoning_effort": effort,
                      "timeout_seconds": timeout, "structured_output": schema_bytes is not None,
                      "protocol_sha256": sha256(PROTOCOL.encode("utf-8")).hexdigest(),
                      "model_catalog_sha256": catalog_hash,
                      "model_catalog_source_sha256": catalog_source_hash,
                      "argv_template": [part.replace(str(directory), "${CALL_TEMP_DIR}") for part in argv],
                      "launch_cwd_template": str(cwd or isolated_cwd).replace(str(directory), "${CALL_TEMP_DIR}")}
            payloads = {"stdin.txt": prompt.encode("utf-8"), "instructions.txt": instruction_bytes,
                        "model-catalog.json": catalog,
                        "invocation.json": (json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")}
            if schema_bytes is not None:
                payloads["response.schema.json"] = schema_bytes
            artifacts = _capture_inputs(artifact_dir, payloads)
        try:
            result = subprocess.run(argv, input=prompt, text=True, encoding="utf-8", capture_output=True,
                                    cwd=cwd or isolated_cwd, timeout=timeout)
            row = parse_events(result.stdout, schema=schema, model=model,
                               exit_code=result.returncode, stderr=result.stderr,
                               elapsed_ms=int((time.monotonic() - started) * 1000))
        except (OSError, subprocess.TimeoutExpired) as exc:
            def decoded(value: Any) -> str:
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            row = parse_events(decoded(getattr(exc, "stdout", "")), schema=schema, model=model,
                               exit_code=-1, stderr=decoded(getattr(exc, "stderr", "")),
                               elapsed_ms=int((time.monotonic() - started) * 1000))
            row["error"] = f"Codex invocation failed: {exc}; {row['error']}"
        row["model_catalog_sha256"] = catalog_hash
        row["model_catalog_source_sha256"] = catalog_source_hash
        row["model_catalog_policy"] = "direct_without_native_capabilities"
        if artifacts is not None:
            row["input_artifacts"] = artifacts
        return row
