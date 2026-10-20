from __future__ import annotations

import json
from pathlib import Path
import unittest

from map_navigation_eval import schema_for_mode, tool_call


class RoutedProtocolTests(unittest.TestCase):
    def test_routed_schema_exposes_only_familiar_tools(self) -> None:
        actions = json.loads(schema_for_mode("routed"))["properties"]["action"]["enum"]
        self.assertEqual(actions, ["grep", "read", "answer"])

    def test_routed_source_call_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unavailable"):
            tool_call({"action": "source", "node": "symbol:x"}, "routed", Path("."))


if __name__ == "__main__":
    unittest.main()
