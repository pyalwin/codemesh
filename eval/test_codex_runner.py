from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_runner import (CODE_MODE_REJECTION, DISABLED_CODE_MODE_WARNING, DISABLED_FEATURES,
                          InputArtifactError, MODEL_CATALOG, MODEL_CATALOG_SOURCE_SHA256,
                          TERRA_MODEL_CATALOG, TERRA_MODEL_CATALOG_SOURCE_SHA256,
                          PROTOCOL, catalog_fingerprint, catalog_source_fingerprint,
                          model_call, parse_events, strict_schema)


SCHEMA = {
    "type": "object", "properties": {
        "action": {"type": "string", "enum": ["read", "answer"]},
        "answer": {"type": "string"}, "path": {"type": "string"},
        "start": {"type": "integer"},
    }, "required": ["action", "answer"], "additionalProperties": False,
}


def events(text: str = '{"action":"read","answer":"","path":"example.py","start":null}') -> list[dict]:
    return [
        {"type": "thread.started", "thread_id": "ephemeral-thread"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "r1", "type": "reasoning", "text": "Reasoning"}},
        {"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 25}},
    ]


def parse(items: list[dict], **kwargs) -> dict:
    return parse_events("\n".join(json.dumps(event) for event in items), schema=kwargs.pop("schema", SCHEMA),
                        model="gpt-6-luna", exit_code=kwargs.pop("exit_code", 0),
                        stderr=kwargs.pop("stderr", ""), elapsed_ms=12, **kwargs)


class CodexRunnerTests(unittest.TestCase):
    def test_usage_does_not_double_count_cached_input_or_invent_cost(self):
        raw = events()
        row = parse(raw)
        self.assertNotIn("error", row)
        self.assertTrue(row["call_valid"])
        self.assertTrue(row["router_activity_valid"])
        self.assertEqual(row["router_error_count"], 0)
        self.assertEqual(row["decision"], {"action": "read", "answer": "", "path": "example.py"})
        self.assertEqual(row["input_tokens"], 1000)
        self.assertIsNone(row["uncached_input_tokens"])
        self.assertEqual(row["cache_read_input_tokens"], 800)
        self.assertEqual(row["output_tokens"], 25)
        self.assertEqual(row["raw_events"], raw)
        self.assertEqual(row["router_rejection_count"], 0)
        self.assertTrue(row["call_valid"])
        self.assertEqual(row["raw_usage"], [raw[-1]["usage"]])
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(row["cost_basis"], "unavailable")
        self.assertEqual(row["model_ids"], [])
        self.assertEqual(row["requested_model"], "gpt-6-luna")
        self.assertIsNone(row["cache_creation_input_tokens"])
        self.assertEqual(row["cache_creation_accounting"], "not_reported")

    def test_cache_writes_are_a_separate_subset_of_total_input(self):
        raw = events()
        raw[-1]["usage"]["cache_write_input_tokens"] = 150
        row = parse(raw)
        self.assertNotIn("error", row)
        self.assertEqual(row["input_tokens"], 1000)
        self.assertEqual(row["uncached_input_tokens"], 50)
        self.assertEqual(row["cache_read_input_tokens"], 800)
        self.assertEqual(row["cache_creation_input_tokens"], 150)
        self.assertEqual(row["cache_creation_accounting"], "reported")
        for invalid in (201, -1, True, "150", None):
            with self.subTest(invalid=invalid):
                raw[-1]["usage"]["cache_write_input_tokens"] = invalid
                self.assertIn("invalid Codex token usage", parse(raw)["error"])

    def test_only_exact_code_mode_disabled_startup_diagnostic_is_accepted(self):
        warning = {"type": "item.completed", "item": {"type": "error", "message": DISABLED_CODE_MODE_WARNING}}
        raw = events()
        raw.insert(1, warning)
        row = parse(raw)
        self.assertNotIn("error", row)
        self.assertEqual(row["diagnostics"], [warning])
        self.assertEqual(row["raw_events"], raw)
        # The identical text after turn.started can be associated with tool
        # activity. Other startup errors also must never be ignored.
        raw = events()
        raw.insert(2, warning)
        self.assertIn("Unexpected native tool/item activity", parse(raw)["error"])
        for text in ("Code Mode is unavailable", "Unknown error", DISABLED_CODE_MODE_WARNING + " extra"):
            raw = events()
            raw.insert(1, {"type": "item.completed", "item": {"type": "error", "message": text}})
            self.assertIn("Unexpected native tool/item activity", parse(raw)["error"])

    def test_stderr_router_rejections_invalidate_valid_json_and_preserve_usage(self):
        raw = events()
        raw[-1]["usage"]["cache_write_input_tokens"] = 25
        warning = {"type": "item.completed", "item": {"type": "error", "message": DISABLED_CODE_MODE_WARNING}}
        raw.insert(1, warning)
        rejected = f"2026-10-18T14:31:15Z ERROR codex_core::tools::router: {CODE_MODE_REJECTION}\n"
        stderr = rejected + rejected
        row = parse(raw, stderr=stderr)
        self.assertIn("Unexpected internal tool router activity", row["error"])
        self.assertIsNone(row["decision"])
        self.assertFalse(row["call_valid"])
        self.assertFalse(row["router_activity_valid"])
        self.assertEqual(row["router_rejection_count"], 2)
        self.assertEqual(row["unexpected_router_error_count"], 0)
        self.assertEqual(row["router_error_count"], 2)
        self.assertEqual(row["router_error_lines"], stderr.splitlines())
        self.assertEqual(row["stderr"], stderr)
        self.assertEqual(row["raw_events"], raw)
        self.assertEqual(row["diagnostics"], [warning])
        # The log exposes rejected dispatches, not per-retry usage. Never remove
        # tokens attributed to hypothetical retries from the provider's totals.
        self.assertEqual(row["input_tokens"], 1000)
        self.assertEqual(row["cache_read_input_tokens"], 800)
        self.assertEqual(row["cache_creation_input_tokens"], 25)
        self.assertEqual(row["uncached_input_tokens"], 175)
        self.assertEqual(row["raw_usage"], [raw[-1]["usage"]])
        self.assertNotIn("retry_count", row)

    def test_unexpected_router_error_variants_fail_closed(self):
        for line in (
            "2026-10-18 ERROR codex_core::tools::router: error=unknown tool",
            "codex_core::tools::router: error=tool dispatch failed",
            '{"level":"ERROR","target":"codex_core::tools::router","message":"unknown tool"}',
        ):
            with self.subTest(stderr=line):
                row = parse(events(), stderr=line)
                self.assertFalse(row["call_valid"])
                self.assertIsNone(row["decision"])
                self.assertEqual(row["router_rejection_count"], 0)
                self.assertEqual(row["unexpected_router_error_count"], 1)
                self.assertEqual(row["router_error_count"], 1)
                self.assertEqual(row["router_error_lines"], [line])

    def test_known_and_other_router_errors_are_counted_without_double_counting(self):
        known = f"ERROR codex_core::tools::router: {CODE_MODE_REJECTION}"
        unknown = "ERROR codex_core::tools::router: error=unknown tool"
        row = parse(events(), stderr=f"{known}\n{unknown}\n")
        self.assertEqual(row["router_rejection_count"], 1)
        self.assertEqual(row["unexpected_router_error_count"], 1)
        self.assertEqual(row["router_error_count"], 2)

    def test_unrelated_stderr_warning_is_retained_without_inventing_router_activity(self):
        warning = "WARNING: proceeding, even though we could not create PATH aliases"
        row = parse(events(), stderr=warning)
        self.assertTrue(row["call_valid"])
        self.assertTrue(row["router_activity_valid"])
        self.assertEqual(row["router_error_count"], 0)
        self.assertEqual(row["stderr"], warning)

    def test_all_native_item_attempts_invalidate_an_otherwise_valid_decision(self):
        for kind in ("command_execution", "file_change", "mcp_tool_call", "web_search", "todo_list", "collab_tool_call", "future_tool"):
            with self.subTest(kind=kind):
                raw = events()
                raw.insert(2, {"type": "item.started", "item": {"type": kind, "id": "forbidden"}})
                row = parse(raw)
                self.assertIn("Unexpected native tool/item activity", row["error"])
                self.assertIsNone(row["decision"])
                self.assertEqual(row["raw_events"], raw)

    def test_missing_malformed_and_inconsistent_usage_cannot_be_graded(self):
        for usage in (None, {}, {"input_tokens": 10, "cached_input_tokens": 11, "output_tokens": 1},
                      {"input_tokens": True, "cached_input_tokens": 0, "output_tokens": 1}):
            with self.subTest(usage=usage):
                raw = events()
                raw[-1]["usage"] = usage
                row = parse(raw)
                self.assertIn("invalid Codex token usage", row["error"])
                self.assertFalse(row["usage_available"])
                self.assertIsNone(row["decision"])

    def test_multiple_turns_and_unknown_event_fail_closed(self):
        raw = events()
        raw.append(raw[-1])
        row = parse(raw)
        self.assertIn("exactly one completed turn", row["error"])
        raw = events() + [{"type": "function_call", "name": "exec_command"}]
        self.assertIn("Unexpected Codex event type", parse(raw)["error"])

    def test_plain_text_handoff_and_errors_preserve_diagnostics(self):
        handoff = "The validator returns the wrong value. Check example.py."
        row = parse(events(handoff), schema=None)
        self.assertNotIn("error", row)
        self.assertEqual(row["text"], handoff)
        self.assertIsNone(row["decision"])
        raw = [{"type": "turn.failed", "error": {"message": "usage limit reached"}}]
        row = parse(raw, exit_code=1, stderr="backend rejected request")
        self.assertIn("usage limit reached", row["error"])
        self.assertIn("backend rejected request", row["error"])
        self.assertEqual(row["raw_events"], raw)

    def test_malformed_jsonl_keeps_original_line_and_rejects_decision(self):
        stdout = "\n".join(json.dumps(event) for event in events()) + "\nnot-json"
        row = parse_events(stdout, schema=SCHEMA, model="gpt-6-luna", exit_code=0, stderr="", elapsed_ms=1)
        self.assertIn("malformed JSONL", row["error"])
        self.assertEqual(row["unparsed_stdout"], ["not-json"])
        self.assertIsNone(row["decision"])

    def test_structured_decisions_enforce_required_types_and_actions(self):
        invalid = [[], {"action": "read"}, {"action": "shell", "answer": ""},
                   {"action": "read", "answer": "", "start": True},
                   {"action": "read", "answer": "", "unknown": "x"}]
        for value in invalid:
            with self.subTest(value=value):
                row = parse(events(json.dumps(value)))
                self.assertIn("Invalid structured response", row["error"])
                self.assertIsNone(row["decision"])

    def test_strict_schema_makes_only_optional_fields_nullable_without_mutation(self):
        original = json.loads(json.dumps(SCHEMA))
        result = strict_schema(SCHEMA)
        self.assertEqual(result["required"], list(SCHEMA["properties"]))
        self.assertEqual(result["properties"]["path"], {"anyOf": [{"type": "string"}, {"type": "null"}]})
        self.assertEqual(result["properties"]["action"], SCHEMA["properties"]["action"])
        self.assertEqual(SCHEMA, original)

    def test_invocation_uses_explicit_transcript_isolation_and_tool_disabling(self):
        transcript = [{"role": "user", "content": "Find the bug"},
                      {"role": "assistant", "content": '{"action":"read","path":"example.py"}'},
                      {"role": "user", "content": "Tool observation: return False"}]
        captured = {}

        def fake_run(argv, **kwargs):
            captured.update(argv=argv, **kwargs)
            workspace = Path(argv[argv.index("--cd") + 1])
            self.assertEqual(list(workspace.iterdir()), [])
            self.assertIn("--ephemeral", argv)
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--ignore-rules", argv)
            self.assertNotIn("resume", argv)
            self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
            self.assertEqual(argv[argv.index("--model") + 1], "gpt-6-luna")
            self.assertNotIn("env", kwargs)  # Preserve authentication; do not replace CODEX_HOME.
            for feature in DISABLED_FEATURES:
                self.assertIn(f"features.{feature}=false", argv)
            self.assertIn('web_search="disabled"', argv)
            self.assertIn("suppress_unstable_features_warning=true", argv)
            self.assertIn("tools.update_plan.enabled=false", argv)
            schema_path = Path(argv[argv.index("--output-schema") + 1])
            self.assertEqual(json.loads(schema_path.read_text()), strict_schema(SCHEMA))
            instructions_setting = next(item for item in argv if item.startswith("model_instructions_file="))
            instructions = Path(json.loads(instructions_setting.partition("=")[2])).read_text()
            self.assertIn("Repair the regression", instructions)
            self.assertIn("Never call a native Codex tool", instructions)
            self.assertIn("Do not call code_mode or any internal tool router", instructions)
            self.assertIn("exactly one JSON action", instructions)
            self.assertIn("Use null for optional parameters", instructions)
            catalog_setting = next(item for item in argv if item.startswith("model_catalog_json="))
            catalog_path = Path(json.loads(catalog_setting.partition("=")[2]))
            self.assertNotEqual(catalog_path, MODEL_CATALOG)
            self.assertEqual(catalog_path.read_bytes(), MODEL_CATALOG.read_bytes())
            controlled = json.loads(catalog_path.read_text())["models"]
            self.assertEqual(len(controlled), 1)
            self.assertEqual(controlled[0]["slug"], "gpt-6-luna")
            self.assertEqual(controlled[0]["tool_mode"], "direct")
            self.assertEqual(controlled[0]["shell_type"], "disabled")
            self.assertIsNone(controlled[0]["apply_patch_tool_type"])
            self.assertEqual(controlled[0]["experimental_supported_tools"], [])
            sent = json.loads(kwargs["input"].split("\n", 1)[1])
            self.assertEqual(sent, transcript)
            return subprocess.CompletedProcess(argv, 0, "\n".join(json.dumps(event) for event in events()), "")

        with patch("codex_runner.subprocess.run", side_effect=fake_run):
            row = model_call(transcript, system="Repair the regression", schema=SCHEMA)
        self.assertNotIn("error", row)
        self.assertNotIn("input_artifacts", row)
        self.assertEqual(row["model_catalog_sha256"], catalog_fingerprint())
        self.assertEqual(row["model_catalog_source_sha256"], MODEL_CATALOG_SOURCE_SHA256)
        self.assertFalse(Path(captured["argv"][captured["argv"].index("--cd") + 1]).exists())

    def test_artifacts_capture_exact_cli_inputs_before_execution(self):
        transcript = [{"role": "user", "content": 'Investigate naïve\nsource: "✓"'}]
        system = "Preserve λ behavior."
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "call-1"

            def fake_run(argv, **kwargs):
                # The capture must already be complete when execution begins.
                self.assertEqual((directory / "stdin.txt").read_bytes(), kwargs["input"].encode("utf-8"))
                self.assertEqual(kwargs["encoding"], "utf-8")
                schema_file = Path(argv[argv.index("--output-schema") + 1])
                self.assertEqual((directory / "response.schema.json").read_bytes(), schema_file.read_bytes())
                for setting, name in (("model_instructions_file=", "instructions.txt"),
                                      ("model_catalog_json=", "model-catalog.json")):
                    actual = next(item for item in argv if item.startswith(setting))
                    actual_file = Path(json.loads(actual.partition("=")[2]))
                    self.assertEqual((directory / name).read_bytes(), actual_file.read_bytes())
                config = json.loads((directory / "invocation.json").read_bytes())
                self.assertEqual(config["requested_model"], "gpt-6-luna")
                self.assertEqual(config["reasoning_effort"], "medium")
                self.assertEqual(config["timeout_seconds"], 11)
                self.assertEqual(config["protocol_sha256"], sha256(PROTOCOL.encode()).hexdigest())
                self.assertEqual(config["model_catalog_sha256"], catalog_fingerprint())
                self.assertTrue(config["structured_output"])
                self.assertFalse(config["provider_request_captured"])
                self.assertEqual(config["launch_cwd_template"], "${CALL_TEMP_DIR}/workspace")
                self.assertIn('model_instructions_file="${CALL_TEMP_DIR}/instructions.txt"', config["argv_template"])
                self.assertNotIn("env", config)
                self.assertNotIn("auth", config)
                self.assertNotIn(str(schema_file.parent), json.dumps(config))
                return subprocess.CompletedProcess(argv, 0, "\n".join(json.dumps(event) for event in events()), "")

            with patch("codex_runner.subprocess.run", side_effect=fake_run):
                row = model_call(transcript, system=system, schema=SCHEMA, effort="medium",
                                 timeout=11, artifact_dir=directory)
            self.assertTrue(row["call_valid"])
            self.assertEqual((directory / "instructions.txt").read_bytes(), (PROTOCOL + "\n\n" + system).encode())
            metadata = row["input_artifacts"]
            self.assertEqual(metadata["directory"], str(directory))
            self.assertFalse(metadata["provider_request_captured"])
            self.assertEqual(set(metadata["files"]), {"stdin.txt", "instructions.txt", "response.schema.json",
                                                     "model-catalog.json", "invocation.json"})
            for name, record in metadata["files"].items():
                data = (directory / name).read_bytes()
                self.assertEqual(record, {"path": str(directory / name), "bytes": len(data),
                                          "sha256": sha256(data).hexdigest()})

    def test_handoff_artifacts_omit_schema_and_survive_model_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "handoff"
            with patch("codex_runner.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 1)):
                row = model_call([{"role": "user", "content": "Write a handoff"}], system="Evaluate",
                                 schema=None, timeout=1, artifact_dir=directory)
            self.assertIn("Codex invocation failed", row["error"])
            self.assertFalse(row["call_valid"])
            self.assertFalse((directory / "response.schema.json").exists())
            self.assertNotIn("response.schema.json", row["input_artifacts"]["files"])
            config = json.loads((directory / "invocation.json").read_bytes())
            self.assertFalse(config["structured_output"])
            self.assertNotIn("--output-schema", config["argv_template"])

    def test_identical_artifacts_can_be_reused_but_changes_never_overwrite(self):
        transcript = [{"role": "user", "content": "Find the bug"}]
        result = subprocess.CompletedProcess("codex", 0, "\n".join(json.dumps(event) for event in events()), "")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "call"
            args = {"system": "Evaluate", "schema": SCHEMA, "artifact_dir": directory}
            with patch("codex_runner.subprocess.run", return_value=result) as run:
                first = model_call(transcript, **args)
                second = model_call(transcript, **args)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(first["input_artifacts"], second["input_artifacts"])
            original = {path.name: path.read_bytes() for path in directory.iterdir()}
            for changes in ({"system": "Changed instructions"}, {"effort": "medium"}, {"schema": None}):
                with self.subTest(changes=changes), patch("codex_runner.subprocess.run") as run:
                    with self.assertRaises(InputArtifactError):
                        model_call(transcript, **(args | changes))
                    run.assert_not_called()
                    self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, original)
            with patch("codex_runner.subprocess.run") as run:
                with self.assertRaises(InputArtifactError):
                    model_call([{"role": "user", "content": "Different input"}], **args)
                run.assert_not_called()
                self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, original)

    def test_artifact_directory_or_write_failure_prevents_model_invocation(self):
        transcript = [{"role": "user", "content": "Find the bug"}]
        with tempfile.TemporaryDirectory() as temporary:
            blocked = Path(temporary) / "not-a-directory"
            blocked.write_text("Keep this file")
            with patch("codex_runner.subprocess.run") as run:
                with self.assertRaises(InputArtifactError):
                    model_call(transcript, system="Evaluate", schema=SCHEMA, artifact_dir=blocked)
                run.assert_not_called()
                self.assertEqual(blocked.read_text(), "Keep this file")
            directory = Path(temporary) / "write-failure"
            original_open = Path.open

            def fail_artifact_write(path, *args, **kwargs):
                if path == directory / "instructions.txt" and args == ("xb",):
                    raise PermissionError("simulated artifact write failure")
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", fail_artifact_write), patch("codex_runner.subprocess.run") as run:
                with self.assertRaisesRegex(InputArtifactError, "simulated artifact write failure"):
                    model_call(transcript, system="Evaluate", schema=SCHEMA, artifact_dir=directory)
                run.assert_not_called()

    def test_existing_conflict_is_checked_before_any_new_artifact_is_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            existing = directory / "instructions.txt"
            existing.write_bytes(b"Existing evidence")
            with patch("codex_runner.subprocess.run") as run:
                with self.assertRaisesRegex(InputArtifactError, "conflicts with existing evidence"):
                    model_call([{"role": "user", "content": "Find the bug"}], system="Evaluate",
                               schema=SCHEMA, artifact_dir=directory)
                run.assert_not_called()
            self.assertEqual(list(directory.iterdir()), [existing])
            self.assertEqual(existing.read_bytes(), b"Existing evidence")

    def test_catalog_fingerprint_covers_the_exact_checked_in_bytes(self):
        self.assertEqual(catalog_fingerprint(), sha256(MODEL_CATALOG.read_bytes()).hexdigest())

    def test_terra_uses_its_own_identity_catalog_and_source_provenance(self):
        terra = json.loads(TERRA_MODEL_CATALOG.read_text())["models"][0]
        self.assertEqual(terra["slug"], "gpt-5.6-terra")
        self.assertEqual(terra["display_name"], "GPT-5.6-Terra")
        self.assertIn("low", [item["effort"] for item in terra["supported_reasoning_levels"]])
        self.assertEqual(catalog_fingerprint("gpt-5.6-terra"), sha256(TERRA_MODEL_CATALOG.read_bytes()).hexdigest())
        self.assertEqual(catalog_source_fingerprint("gpt-5.6-terra"), TERRA_MODEL_CATALOG_SOURCE_SHA256)
        self.assertNotEqual(catalog_source_fingerprint("gpt-5.6-terra"), MODEL_CATALOG_SOURCE_SHA256)

        def fake_run(argv, **kwargs):
            self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.6-terra")
            setting = next(item for item in argv if item.startswith("model_catalog_json="))
            self.assertEqual(Path(json.loads(setting.partition("=")[2])).read_bytes(), TERRA_MODEL_CATALOG.read_bytes())
            return subprocess.CompletedProcess(argv, 0, "\n".join(json.dumps(event) for event in events()), "")

        with tempfile.TemporaryDirectory() as temporary:
            artifact_dir = Path(temporary) / "call"
            with patch("codex_runner.subprocess.run", side_effect=fake_run):
                row = model_call([{"role": "user", "content": "repair"}], system="Repair",
                                 schema=SCHEMA, model="gpt-5.6-terra", artifact_dir=artifact_dir)
            self.assertTrue(row["call_valid"])
            self.assertEqual(row["requested_model"], "gpt-5.6-terra")
            self.assertEqual(row["model_catalog_source_sha256"], TERRA_MODEL_CATALOG_SOURCE_SHA256)
            invocation = json.loads((artifact_dir / "invocation.json").read_text())
            self.assertEqual(invocation["requested_model"], "gpt-5.6-terra")
            self.assertEqual(invocation["model_catalog_source_sha256"], TERRA_MODEL_CATALOG_SOURCE_SHA256)

    def test_terra_cannot_reenable_native_tools_or_borrow_luna_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            for field, value in (("tool_mode", "code_mode_only"), ("slug", "gpt-6-luna")):
                with self.subTest(field=field):
                    data = json.loads(TERRA_MODEL_CATALOG.read_text())
                    data["models"][0][field] = value
                    path.write_text(json.dumps(data))
                    with patch("codex_runner.TERRA_MODEL_CATALOG", path), patch("codex_runner.subprocess.run") as run:
                        with self.assertRaises(ValueError):
                            model_call([{"role": "user", "content": "repair"}], system="Repair",
                                       schema=SCHEMA, model="gpt-5.6-terra")
                        run.assert_not_called()

    def test_controlled_catalog_rejects_unknown_models_before_execution(self):
        with patch("codex_runner.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "does not contain requested model"):
                model_call([{"role": "user", "content": "answer"}], system="Evaluate",
                           schema=SCHEMA, model="some-other-model")
            run.assert_not_called()

    def test_catalog_cannot_accidentally_reenable_native_capabilities(self):
        overrides = {"tool_mode": "code_mode_only", "shell_type": "unified_exec",
                     "apply_patch_tool_type": "freeform", "experimental_supported_tools": ["clock"],
                     "supports_search_tool": True}
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            for key, value in overrides.items():
                with self.subTest(field=key):
                    data = json.loads(MODEL_CATALOG.read_text())
                    data["models"][0][key] = value
                    catalog.write_text(json.dumps(data))
                    with patch("codex_runner.MODEL_CATALOG", catalog), patch("codex_runner.subprocess.run") as run:
                        with self.assertRaisesRegex(ValueError, "must disable native tool capabilities"):
                            model_call([{"role": "user", "content": "answer"}], system="Evaluate", schema=SCHEMA)
                        run.assert_not_called()

    def test_timeout_preserves_partial_events(self):
        partial = json.dumps({"type": "thread.started", "thread_id": "partial"}).encode()
        with patch("codex_runner.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 1, output=partial, stderr=b"waiting")):
            row = model_call([{"role": "user", "content": "answer"}], system="Evaluate", schema=None, timeout=1)
        self.assertIn("Codex invocation failed", row["error"])
        self.assertEqual(row["session_id"], "partial")
        self.assertEqual(row["stderr"], "waiting")
        self.assertIsNone(row["cost_usd"])

    def test_transcript_must_include_the_current_user_message(self):
        with patch("codex_runner.subprocess.run") as run:
            for transcript in ([], [{"role": "assistant", "content": "done"}], [{"role": "user", "content": {}}]):
                with self.assertRaises(ValueError):
                    model_call(transcript, system="Evaluate", schema=None)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
