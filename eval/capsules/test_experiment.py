import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiment import (
    apply_summaries, extract_unit, format_units, lexical_checks, make_prompt,
    normalize_summary, parse_unit_id, requested_source_units, run_with_fallback,
)
from capsule_ids import expand_citations, format_v2_ids
from quality_review import answer_hash, attach_quality


SOURCE = '''class Example:
    def run(self, value: int) -> int:
        """Return the adjusted value."""
        return helper(value)
'''


class CapsuleExperimentTests(unittest.TestCase):
    def test_extract_fingerprint_changes_with_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(SOURCE, encoding="utf-8")
            first = extract_unit(root, "sample.py::Example.run")
            self.assertEqual(first["purpose"], "Return the adjusted value.")
            self.assertIn("helper", first["calls"])
            self.assertEqual(first["lines"], "2-4")

            (root / "sample.py").write_text(SOURCE.replace("return helper(value)", "return  helper(value)"), encoding="utf-8")
            whitespace_only = extract_unit(root, "sample.py::Example.run")
            self.assertNotEqual(first["source_sha256"], whitespace_only["source_sha256"])
            self.assertEqual(first["ast_sha256"], whitespace_only["ast_sha256"])

            (root / "sample.py").write_text(SOURCE.replace("helper(value)", "helper(value + 1)"), encoding="utf-8")
            second = extract_unit(root, "sample.py::Example.run")
            self.assertNotEqual(first["source_sha256"], second["source_sha256"])

    def test_cached_summary_requires_matching_source_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(SOURCE, encoding="utf-8")
            unit = extract_unit(root, "sample.py::Example.run")
            cache = root / "summaries.json"
            cache.write_text(json.dumps({unit["id"]: {
                "source_sha256": "wrong", "summary": "stale claim"
            }}), encoding="utf-8")
            units = {unit["id"]: unit}
            apply_summaries(units, cache)
            self.assertEqual(unit["purpose"], "Return the adjusted value.")

            cache.write_text(json.dumps({unit["id"]: {
                "source_sha256": unit["source_sha256"], "summary": "fresh claim"
            }}), encoding="utf-8")
            apply_summaries(units, cache)
            self.assertEqual(unit["purpose"], "fresh claim")

    def test_capsule_and_compact_keep_same_facts_without_source_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(SOURCE, encoding="utf-8")
            unit = extract_unit(root, "sample.py::Example.run")
            task = {"question": "What does run do?", "units": [unit["id"]]}
            units = {unit["id"]: unit}
            for mode in ("capsule", "compact"):
                prompt = make_prompt(task, mode, units)
                self.assertIn(unit["purpose"], prompt)
                self.assertIn(unit["signature"], prompt)
                self.assertIn(unit["source_sha256"][:12], prompt)
                self.assertNotIn("return helper(value)", prompt)
            self.assertIn("return helper(value)", format_units("raw", [unit]))

    def test_rejects_escaping_path(self):
        with self.assertRaises(ValueError):
            parse_unit_id("../outside.py::run")

    def test_extracts_type_alias_as_code_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("Modes: TypeAlias = Literal['a', 'b']\n", encoding="utf-8")
            unit = extract_unit(root, "sample.py::Modes")
            self.assertEqual(unit["lines"], "1-1")
            self.assertIn("Literal['a', 'b']", unit["signature"])

    def test_model_summary_normalizes_header_and_whitespace(self):
        self.assertEqual(normalize_summary("# Summary\n\nDoes one thing.\nThen another."), "Does one thing. Then another.")

    def test_lexical_checks_are_case_insensitive_and_require_all_fragments(self):
        task = {"checks": [{"label": "delegation", "all_of": ["serializer", "to_python"]}]}
        self.assertEqual(lexical_checks("SERIALIZER calls to_python", task), [{"label": "delegation", "hit": True}])
        self.assertEqual(lexical_checks("serializer runs", task), [{"label": "delegation", "hit": False}])

    def test_v2_preserves_doc_behavior_and_guarded_call_arguments(self):
        source = '''class Example:
    def run(self, mode='python', deep=False):
        """Run the operation.

        If mode is 'json', output is serializable.
        If mode is 'python', objects may remain.
        """
        if deep:
            return helper(mode=mode)
        else:
            return other(mode=mode)
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(source, encoding="utf-8")
            unit = extract_unit(root, "sample.py::Example.run")
            rendered = format_units("v2", [unit], "How do json and python modes differ when deep?")
            self.assertIn("If mode is 'json', output is serializable.", rendered)
            self.assertIn("If mode is 'python', objects may remain.", rendered)
            self.assertIn("if deep: helper(mode=mode)", rendered)
            self.assertIn("if not (deep): other(mode=mode)", rendered)

    def test_v2_ids_preserve_evidence_and_resolve_citations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(SOURCE, encoding="utf-8")
            unit = extract_unit(root, "sample.py::Example.run")
            context, mapping = format_v2_ids([unit], "What does run call?")
            prompt = make_prompt({"question": "What does run call?", "units": [unit["id"]]}, "v2_ids", {unit["id"]: unit})
            self.assertIn("### U1 Example.run", context)
            self.assertIn("helper(value)", context)
            self.assertNotIn("sample.py:", prompt)
            self.assertNotIn("Version: sha256", prompt)
            self.assertEqual(mapping["units"]["U1"]["source_sha256"], unit["source_sha256"])
            call_alias = next(alias for alias, item in mapping["evidence"].items() if item["lines"] == "4")
            self.assertEqual(expand_citations(f"It calls helper ({call_alias}).", mapping), "It calls helper (sample.py:4).")
            self.assertEqual(expand_citations("See U1.", mapping), "See sample.py:2-4.")
            task = {"question": "What does run call?", "units": [unit["id"]]}
            self.assertEqual(requested_source_units("NEED_SOURCE: U1", task, mapping), [unit["id"]])

    def test_v2_ids_map_same_file_evidence_to_containing_unit(self):
        source = 'def first():\n    return helper()\n\ndef second():\n    return other()\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(source, encoding="utf-8")
            first = extract_unit(root, "sample.py::first")
            second = extract_unit(root, "sample.py::second")
            _, mapping = format_v2_ids([first, second], "What do these call?")
            by_line = {item["lines"]: item["unit_id"] for item in mapping["evidence"].values()}
            self.assertEqual(by_line["2"], first["id"])
            self.assertEqual(by_line["5"], second["id"])

    def test_source_fallback_targets_requested_unit_and_counts_both_calls(self):
        task = {"question": "What happens?", "units": ["sample.py::run"]}
        unit = {"id": "sample.py::run", "path": "sample.py", "lines": "1-2", "source_sha256": "abc", "source": "def run():\n    return 1\n"}
        responses = [
            {"answer": "NEED_SOURCE: sample.py::run", "input_tokens": 10, "output_tokens": 3, "cost_usd": 0.01, "elapsed_ms": 100},
            {"answer": "It returns 1.", "input_tokens": 20, "output_tokens": 4, "cost_usd": 0.02, "elapsed_ms": 200},
        ]
        with patch("experiment.run_claude", side_effect=responses) as mocked:
            result = run_with_fallback("capsule", task, {unit["id"]: unit}, "haiku", 0.2, Path("."))
        self.assertEqual(mocked.call_count, 2)
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["fallback_units"], [unit["id"]])
        self.assertEqual(result["input_tokens"], 30)
        self.assertAlmostEqual(result["cost_usd"], 0.03)
        self.assertIn("def run():", mocked.call_args.args[0])
        self.assertIn("    2:     return 1", mocked.call_args.args[0])
        self.assertEqual(requested_source_units("NEED_SOURCE: sample.py::run", task), [unit["id"]])
        self.assertEqual(requested_source_units("Detail is not shown.\n**NEED_SOURCE:** sample.py lines 1-2", task), [unit["id"]])
        multi = {"units": ["sample.py::run", "other.py::walk"]}
        self.assertEqual(requested_source_units("NEED_SOURCE: other.py lines 2-3", multi), ["other.py::walk"])

    def test_v2_keeps_assignment_targets_and_conditional_expression_guards(self):
        source = '''def copy(update=None, deep=False):
    copied = clone() if deep and update else shallow()
    if update:
        if update.get('extra'):
            copied.extra = update['extra']
        else:
            copied.fields = update
    return copied
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(source, encoding="utf-8")
            unit = extract_unit(root, "sample.py::copy")
            rendered = format_units("v2", [unit], "How does deep and update change extra and fields?")
            self.assertIn("if deep and update: clone()", rendered)
            self.assertIn("if not (deep and update): shallow()", rendered)
            self.assertIn("copied.extra = update['extra']", rendered)
            self.assertIn("copied.fields = update", rendered)

    def test_quality_review_requires_exact_answer_hash(self):
        report = {
            "tasks": [{"id": "t", "rubric": ["fact"]}],
            "results": [{"task": "t", "mode": "v2", "repeat": 1, "model_result": {"answer": "fact"}}],
        }
        key = ("t", "v2", 1)
        labels = {key: {"answer_sha256": answer_hash("fact"), "rubric_hits": [True], "false_claim": False}}
        graded = attach_quality(report, labels)
        self.assertEqual(graded["quality_summary"]["v2"]["acceptable_answers"], 1)
        labels[key]["answer_sha256"] = answer_hash("changed")
        with self.assertRaisesRegex(ValueError, "Answer changed"):
            attach_quality(report, labels)


if __name__ == "__main__":
    unittest.main()
