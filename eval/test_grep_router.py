from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from grep_router import ReadRouter, route_grep


class GrepRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name)
        (self.repo / ".codemesh").mkdir()
        self.file = self.repo / "example.py"
        self.file.write_text("def model_dump():\n    pass\ndef model_dump_json():\n    pass\n")
        self.db = sqlite3.connect(self.repo / ".codemesh" / "codemesh.db")
        self.db.execute("CREATE TABLE nodes (id TEXT, type TEXT, name TEXT, hash TEXT, data TEXT)")
        digest = sha256(self.file.read_bytes()).hexdigest()
        self.db.execute("INSERT INTO nodes VALUES ('file:example.py','file','example.py',?,NULL)", (digest,))
        self.db.execute("INSERT INTO nodes VALUES ('symbol:example.py:model_dump','symbol','model_dump',NULL,?)",
                        ('{"filePath":"example.py","lineStart":1,"lineEnd":2}',))
        self.db.execute("INSERT INTO nodes VALUES ('symbol:example.py:model_dump_json','symbol','model_dump_json',NULL,?)",
                        ('{"filePath":"example.py","lineStart":3,"lineEnd":4}',))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def test_routes_fresh_declaration_prefix_with_native_parity(self) -> None:
        observation, route = route_grep("def model_dump", self.repo)
        self.assertEqual(route["backend"], "codemesh")
        self.assertEqual(route["native_matches"], 2)
        self.assertIn("symbol:example.py:model_dump", observation)
        self.assertIn("symbol:example.py:model_dump_json", observation)
        self.assertIn("span: 1-2", observation)

    def test_bare_symbol_keeps_native_call_sites_and_annotates_declarations(self) -> None:
        self.file.write_text(self.file.read_text() + "model_dump()\n")
        self._refresh_hash()
        observation, route = route_grep("model_dump", self.repo)
        self.assertEqual(route["backend"], "hybrid")
        self.assertIn("./example.py:5:model_dump()", observation)
        self.assertIn("span: 1-2", observation)
        self.assertNotIn("span: 1-2", observation.splitlines()[-1])

    def test_keeps_general_body_text_on_native_grep(self) -> None:
        observation, route = route_grep("pass", self.repo)
        self.assertEqual(route["backend"], "native")
        self.assertEqual(route["reason"], "no_indexed_declarations")
        self.assertIn("./example.py:2:    pass", observation)

    def test_stale_index_falls_back_without_symbol_id(self) -> None:
        self.file.write_text("def model_dump():\n    return 2\ndef model_dump_json():\n    pass\n")
        observation, route = route_grep("def model_dump", self.repo)
        self.assertEqual(route["backend"], "native")
        self.assertEqual(route["reason"], "stale_index")
        self.assertNotIn("codemesh-id", observation)

    def test_missing_index_match_falls_back_to_complete_native_result(self) -> None:
        self.db.execute("DELETE FROM nodes WHERE id='symbol:example.py:model_dump_json'")
        self.db.commit()
        observation, route = route_grep("def model_dump", self.repo)
        self.assertEqual(route["backend"], "native")
        self.assertEqual(route["reason"], "parity_mismatch")
        self.assertIn("def model_dump_json", observation)

    def test_read_footer_and_same_session_dedup_then_reset(self) -> None:
        self.file.write_text(
            "def model_dump(self, mode=None):\n"
            "    \"\"\"A long docstring.\"\"\"\n"
            "    return self.serializer.to_python(mode=mode)\n")
        self.db.execute("DELETE FROM nodes WHERE id='symbol:example.py:model_dump_json'")
        self.db.execute("UPDATE nodes SET data=? WHERE id='symbol:example.py:model_dump'",
                        ('{"filePath":"example.py","lineStart":1,"lineEnd":3}',))
        self._refresh_hash()
        router = ReadRouter()
        first, route = router.route_read("example.py", 1, 2, self.repo)
        self.assertEqual(route["reason"], "verified_tail")
        self.assertIn("1: def model_dump", first)
        self.assertIn("return L3-L3: self.serializer.to_python(…); bindings: mode=mode", first)
        second, route = router.route_read("example.py", 1, 2, self.repo)
        self.assertEqual(route["reason"], "repeat_read")
        self.assertIn("Unchanged since read 1", second)
        self.assertNotIn("1: def model_dump", second)
        router.reset_session()
        third, route = router.route_read("example.py", 1, 2, self.repo)
        self.assertEqual(route["reason"], "verified_tail")
        self.assertIn("1: def model_dump", third)

    def test_read_stale_index_returns_numbered_native_source(self) -> None:
        self.file.write_text("def changed():\n    return 1\n")
        observation, route = ReadRouter().route_read("example.py", 1, 2, self.repo)
        self.assertEqual(route["reason"], "stale_index")
        self.assertIn("1: def changed():", observation)
        self.assertNotIn("Codemesh verified", observation)

    def test_read_dedup_is_not_limited_to_symbol_start(self) -> None:
        router = ReadRouter()
        first, route = router.route_read("example.py", 4, 1, self.repo)
        self.assertEqual(route["reason"], "no_missing_tail")
        self.assertIn("4:     pass", first)
        second, route = router.route_read("example.py", 4, 1, self.repo)
        self.assertEqual(route["reason"], "repeat_read")
        self.assertNotIn("4:     pass", second)

    def test_json_tail_preserves_decode_and_forwarded_keywords(self) -> None:
        self.file.write_text(
            "def model_dump():\n    pass\n"
            "def model_dump_json(self, indent=None, ensure_ascii=False):\n"
            "    \"\"\"A long docstring.\"\"\"\n"
            "    return self.serializer.to_json(indent=indent, ensure_ascii=ensure_ascii).decode()\n")
        self.db.execute("UPDATE nodes SET data=? WHERE id='symbol:example.py:model_dump_json'",
                        ('{"filePath":"example.py","lineStart":3,"lineEnd":5}',))
        self._refresh_hash()
        observation, route = ReadRouter().route_read("example.py", 3, 2, self.repo)
        self.assertEqual(route["reason"], "verified_tail")
        self.assertIn("to_json(…).decode()", observation)
        self.assertIn("bindings: indent=indent, ensure_ascii=ensure_ascii", observation)

    def test_tail_ignores_nested_function_return(self) -> None:
        self.file.write_text(
            "def model_dump():\n"
            "    \"\"\"Docstring.\"\"\"\n"
            "    def inner():\n"
            "        return 99\n"
            "    return 1\n")
        self.db.execute("UPDATE nodes SET data=? WHERE id='symbol:example.py:model_dump'",
                        ('{"filePath":"example.py","lineStart":1,"lineEnd":5}',))
        self._refresh_hash()
        observation, route = ReadRouter().route_read("example.py", 1, 2, self.repo)
        self.assertEqual(route["reason"], "verified_tail")
        self.assertIn("return L5-L5: 1", observation)
        self.assertNotIn("return L4-L4", observation)

    def _refresh_hash(self) -> None:
        self.db.execute("UPDATE nodes SET hash=? WHERE id='file:example.py'",
                        (sha256(self.file.read_bytes()).hexdigest(),))
        self.db.commit()


if __name__ == "__main__":
    unittest.main()
