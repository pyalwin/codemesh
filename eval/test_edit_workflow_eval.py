from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from edit_workflow_eval import capsule_read, mutated_content, safe_file, schema_for_mode


class EditWorkflowEvalTests(unittest.TestCase):
    def test_capsule_action_is_scoped_to_capsule_modes(self) -> None:
        filesystem = json.loads(schema_for_mode("filesystem"))["properties"]["action"]["enum"]
        capsule = json.loads(schema_for_mode("capsule_v2"))["properties"]["action"]["enum"]
        self.assertNotIn("read_raw", filesystem)
        self.assertIn("read_raw", capsule)

    def test_mutation_can_target_one_of_repeated_anchors(self) -> None:
        with TemporaryDirectory() as directory:
            repo = Path(directory)
            file = repo / "sample.py"
            file.write_text("x = 1\nx = 1\n")
            task = {"id": "t", "path": "sample.py", "old": "x = 1",
                    "mutant": "x = 2", "occurrence": 2}
            self.assertEqual(mutated_content(repo, task), b"x = 1\nx = 2\n")

    def test_capsule_is_derived_from_current_source(self) -> None:
        with TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "sample.py").write_text(
                "def convert(value):\n"
                "    \"\"\"Convert a value.\"\"\"\n"
                "    return value.replace('-', '')\n")
            observation, route = capsule_read("sample.py", 1, 3, repo, "replace separator")
            self.assertEqual(route["backend"], "capsule_v2")
            self.assertIn("Overview (source docstring", observation)
            self.assertIn("replace('-', '')", observation)

    def test_safe_file_rejects_escape(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "repository-relative file"):
                safe_file(Path(directory), "../outside.py")


if __name__ == "__main__":
    unittest.main()
