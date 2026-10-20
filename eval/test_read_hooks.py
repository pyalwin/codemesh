"""Protocol checks for the Claude Code read hooks."""

import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


class ReadHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codemesh-read-hook-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        (self.project / ".codemesh").mkdir()
        self.db_path = self.project / ".codemesh" / "codemesh.db"
        with sqlite3.connect(self.db_path) as db:
            db.executescript(
                "CREATE TABLE nodes (id TEXT, type TEXT, path TEXT, name TEXT, data TEXT);"
                "CREATE TABLE edges (from_id TEXT, to_id TEXT, type TEXT);"
            )
            db.execute("INSERT INTO nodes VALUES (?,?,?,?,?)", (
                "file", "file", "src/example.py", None, "{}"))
        self.payload = json.dumps({"tool_input": {
            "file_path": str(self.project / "src" / "example.py")}})

    def run_hook(self, name):
        result = subprocess.run(
            [str(ROOT / "hooks" / name)], cwd=self.project, input=self.payload,
            text=True, capture_output=True, check=True,
        )
        return json.loads(result.stdout) if result.stdout else None

    def test_pre_read_json_escapes_summary_and_skips_stale(self):
        summary = 'A "quoted" summary with\na newline'
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO nodes VALUES (?,?,?,?,?)", (
                "concept", "concept", None, None,
                json.dumps({"summary": summary, "stale": 0})))
            db.execute("INSERT INTO edges VALUES (?,?,?)", (
                "concept", "file", "describes"))
        output = self.run_hook("pre-read.sh")
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertIn(summary, output["hookSpecificOutput"]["additionalContext"])
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE nodes SET data=? WHERE id='concept'", (
                json.dumps({"summary": summary, "stale": 1}),))
        self.assertIsNone(self.run_hook("pre-read.sh"))

    def test_post_read_json_only_without_fresh_concept(self):
        output = self.run_hook("post-read.sh")
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("no cached summary", output["hookSpecificOutput"]["additionalContext"])
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO nodes VALUES (?,?,?,?,?)", (
                "concept", "concept", None, None, json.dumps({"summary": "Known"})))
            db.execute("INSERT INTO edges VALUES (?,?,?)", (
                "concept", "file", "describes"))
        self.assertIsNone(self.run_hook("post-read.sh"))


if __name__ == "__main__":
    unittest.main()
