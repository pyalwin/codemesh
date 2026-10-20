from __future__ import annotations

import copy
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import vnext_agent as runner


class VNextMetricsTests(unittest.TestCase):
    def test_cost_requires_all_calls_to_have_known_cost(self) -> None:
        self.assertAlmostEqual(runner.total_cost([{"cost_usd": 0.2}, {"cost_usd": 0.3}]), 0.5)
        self.assertEqual(runner.total_cost([{"cost_usd": 0.0}]), 0.0)
        self.assertEqual(runner.total_cost([]), 0)
        self.assertIsNone(runner.total_cost([{"cost_usd": 0.2}, {"cost_usd": None}]))
        self.assertIsNone(runner.total_cost([{"cost_usd": 0.2}, {}]))

    def test_paired_quality_and_tokens_survive_unavailable_cost(self) -> None:
        pairs = [{
            "filesystem": {"final_patch_pass": True, "input_tokens": 100, "cost_usd": 0.2},
            "codemesh": {"final_patch_pass": False, "input_tokens": 80, "cost_usd": None},
        }]
        report = runner.paired_summary(pairs)
        self.assertEqual(report["filesystem_cost_usd"], 0.2)
        self.assertIsNone(report["codemesh_cost_usd"])
        self.assertIsNone(report["cost_change_percent"])
        self.assertEqual(report["quality_change_percentage_points"], -100)
        self.assertAlmostEqual(report["input_token_change_percent"], -20)

    def test_zero_cost_is_distinct_from_unknown_and_has_no_zero_denominator(self) -> None:
        def pair(native: float, mesh: float) -> list[dict]:
            return [{
                "filesystem": {"final_patch_pass": True, "input_tokens": 0, "cost_usd": native},
                "codemesh": {"final_patch_pass": True, "input_tokens": 0, "cost_usd": mesh},
            }]
        self.assertEqual(runner.paired_summary(pair(1, 0))["cost_change_percent"], -100)
        self.assertIsNone(runner.paired_summary(pair(0, 1))["cost_change_percent"])
        self.assertIsNone(runner.paired_summary(pair(0, 0))["input_token_change_percent"])
        self.assertIsNone(runner.paired_summary([]))

    def test_summary_keeps_runner_error_spend_out_of_graded_totals(self) -> None:
        def row(status: str, cost: float | None) -> dict:
            totals = dict.fromkeys(["input_tokens", "uncached_input_tokens", "cache_creation_input_tokens",
                                    "cache_read_input_tokens", "output_tokens", "elapsed_ms"], 10)
            totals["cost_usd"] = cost
            return {"mode": "codemesh", "status": status, "totals": totals, "final_patch_pass": True,
                    "router_error_count": 2 if status == "model_error" else 0,
                    "router_affected_calls": int(status == "model_error")}
        report = runner.summarize([row("complete", 0.3), row("model_error", None)], ["codemesh"])["codemesh"]
        self.assertEqual(report["attempts"], 2)
        self.assertEqual(report["graded_trajectories"], 1)
        self.assertEqual(report["runner_errors"], 1)
        self.assertEqual(report["cost_usd"], 0.3)
        self.assertIsNone(report["ungraded_model_cost_usd"])
        self.assertIsNone(report["all_attempt_cost_usd"])
        self.assertEqual(report["input_tokens"], 10)
        self.assertEqual(report["all_attempt_input_tokens"], 20)
        self.assertEqual(report["all_attempt_router_error_count"], 2)
        self.assertEqual(report["all_attempt_router_affected_calls"], 1)

    def test_protocol_fingerprint_changes_with_harness_provider_or_hidden_test(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / "vnext_agent.py"
            provider = root / "codex_runner.py"
            working_state = root / "working_state.py"
            catalog = root / "codex_luna_catalog.json"
            hidden = root / "hidden.py"
            tasks = root / "tasks.json"
            for name in ["vnext_agent.py", "codex_runner.py", "working_state.py", "codex_luna_catalog.json", "grep_router.py", "vnext_contract.py", "hidden.py"]:
                (root / name).write_text(f"# {name}\n")
            tasks.write_text(json.dumps({"tasks": [{"hidden_test": "hidden.py"}]}))
            with patch.object(runner, "__file__", str(harness)), patch.object(runner, "ROOT", root):
                previous = runner.protocol_fingerprint(tasks)
                for path in [harness, provider, working_state, catalog, hidden]:
                    path.write_text(path.read_text() + "# changed protocol\n")
                    current = runner.protocol_fingerprint(tasks)
                    self.assertNotEqual(previous, current)
                    previous = current

    def test_incompatible_resume_is_rejected_before_rebuilding_saved_indexes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = root / "tasks.json"
            tasks.write_text(json.dumps({"commit": runner.PIN, "tasks": [{"id": "fixture"}]}))
            output = root / "existing-run"
            output.mkdir()
            manifest = output / "run_config.json"
            manifest.write_text('{"protocol_fingerprint":"old-protocol"}\n')
            (output / "runs.jsonl").write_text("")
            original_manifest = manifest.read_bytes()
            argv = ["vnext_agent.py", "--repo", str(root), "--out", str(output),
                    "--tasks", str(tasks), "--python", sys.executable, "--run", "--resume"]

            def command(argv, cwd):
                return SimpleNamespace(returncode=0, stdout=runner.PIN if argv[0] == "git" else "codex test")

            with patch.object(sys, "argv", argv), patch.object(runner, "command", side_effect=command), \
                    patch.object(runner, "build_fingerprint", return_value="build"), \
                    patch.object(runner, "protocol_fingerprint", return_value="new-protocol"), \
                    patch.object(runner, "prepare") as prepare, redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as stopped:
                    runner.main()
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn("resume settings differ", errors.getvalue())
            prepare.assert_not_called()
            self.assertEqual(manifest.read_bytes(), original_manifest)

    @staticmethod
    def read(text: str = "1: source evidence", **changes) -> dict:
        action = {"action": "read", "input": {"path": "pydantic/example.py", "start": 1, "limit": 1},
                  "observation": text, "observation_bytes": len(text.encode()), "route": {}, "error": None}
        action.update(changes)
        return action

    def test_repeats_distinguish_active_context_from_fresh_session_rehydration(self) -> None:
        read = self.read()
        phases = [
            {"phase": 0, "context_epoch": 0, "actions": [read]},
            {"phase": 1, "context_epoch": 0, "actions": [read]},
            {"phase": 2, "context_epoch": 1, "actions": [read, read]},
        ]
        metrics = runner.repeated_reads(phases)
        self.assertEqual(metrics["same_context_repeat_read_calls"], 2)
        self.assertEqual(metrics["cross_context_repeat_read_calls"], 1)
        self.assertEqual(metrics["same_context_repeated_bytes"], 2 * read["observation_bytes"])
        self.assertEqual(metrics["cross_context_repeated_bytes"], read["observation_bytes"])
        self.assertEqual(metrics["repeat_read_calls"], 3)
        self.assertEqual(metrics["repeated_observation_bytes"], 3 * read["observation_bytes"])

    def test_old_checkpoint_phases_infer_the_fresh_session_boundary(self) -> None:
        read = self.read()
        metrics = runner.repeated_reads([
            {"phase": 0, "actions": [read]},
            {"phase": 1, "actions": [read]},
            {"phase": 2, "actions": [read]},
        ])
        self.assertEqual(metrics["same_context_repeat_read_calls"], 1)
        self.assertEqual(metrics["cross_context_repeat_read_calls"], 1)

    def test_changed_or_undelivered_content_is_not_counted_as_repeated_bytes(self) -> None:
        read = self.read()
        actions = [read, self.read("1: changed evidence"), self.read(error="failed")]
        for status in ["unchanged", "stale", "deleted"]:
            actions.append(self.read(route={"status": status}))
        actions.append({"action": "premature_answer"})
        metrics = runner.repeated_reads([{"phase": 0, "actions": actions}])
        self.assertEqual(metrics["repeat_read_calls"], 0)
        self.assertEqual(metrics["repeated_observation_bytes"], 0)

    def test_codex_requires_explicit_history_without_silently_resuming(self) -> None:
        fake = SimpleNamespace(model_call=lambda *args, **kwargs: self.fail("provider must not run"))
        with patch.dict("sys.modules", {"codex_runner": fake}):
            with self.assertRaisesRegex(ValueError, "explicit session history"):
                runner.model_call("investigate", "context-id", "filesystem", resume=False,
                                  structured=True, cwd=Path("/tmp"), model="gpt-6-luna",
                                  budget=None, provider="codex")

    def test_codex_transcript_continues_investigation_and_resets_before_editing(self) -> None:
        snapshots = []
        decisions = iter([
            {"action": "read", "path": "pydantic/example.py", "start": 1, "limit": 1, "answer": ""},
            {"action": "answer", "answer": "Investigated."},
            {"action": "answer", "answer": "Impact checked."},
            "Handoff: change the value in pydantic/example.py after checking source.",
            {"action": "edit", "path": "pydantic/example.py", "old": "= 1", "new": "= 2", "answer": ""},
            {"action": "answer", "answer": "Patched."},
        ])

        def provider(history, **kwargs):
            snapshots.append(copy.deepcopy(history))
            result = next(decisions)
            structured = isinstance(result, dict)
            self.assertEqual(kwargs["schema"] is not None, structured)
            return {"decision": result if structured else None,
                    "text": json.dumps(result) if structured else result, "cost_usd": None}

        def worktree(base, path, task, index):
            (path / "pydantic").mkdir(parents=True)
            (path / task["path"]).write_text("unique_source_evidence = 1\n")

        task = {"id": "test", "path": "pydantic/example.py", "symptom": "Wrong value.",
                "impact_question": "Check callers.", "hidden_test": "unused.py"}
        fake = SimpleNamespace(model_call=provider)
        with TemporaryDirectory() as directory:
            out = Path(directory)
            with patch.dict("sys.modules", {"codex_runner": fake}), \
                    patch.object(runner, "worktree", side_effect=worktree), \
                    patch.object(runner, "remove_worktree"), \
                    patch.object(runner, "pytest", return_value={"passed": True}):
                result = runner.trajectory(out, task, "filesystem", 1, out, out / "unused.db",
                                           Path("/unused/python"), "gpt-6-luna", None, 4, provider="codex")

        self.assertEqual(result["status"], "complete")
        self.assertEqual([len(history) for history in snapshots], [1, 3, 5, 7, 1, 3])
        self.assertIn("unique_source_evidence", json.dumps(snapshots[2]))
        self.assertIn("Handoff: change the value", snapshots[4][0]["content"])
        self.assertNotIn("unique_source_evidence", json.dumps(snapshots[4]))
        self.assertEqual(result["calls"][0]["context_session_id"], result["calls"][3]["context_session_id"])
        self.assertNotEqual(result["calls"][0]["context_session_id"], result["calls"][4]["context_session_id"])
        self.assertIsNone(result["totals"]["cost_usd"])


if __name__ == "__main__":
    unittest.main()
