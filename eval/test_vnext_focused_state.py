"""Public harness integration for deterministic context and graph options."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import vnext_agent as runner
from focused_state import FocusedWorkingState


def test_focused_policy_calls_only_the_requested_reasoner(tmp_path):
    state = FocusedWorkingState(tmp_path / "archive", "session")
    state.task("Explain login; preserve authorization")
    captured = []

    def provider(messages, **kwargs):
        captured.append((messages, kwargs))
        return {"decision": {"action": "answer", "answer": "Need source."},
                "text": '{"action":"answer","answer":"Need source."}', "input_tokens": 10}

    with patch.dict("sys.modules", {"codex_runner": SimpleNamespace(model_call=provider)}):
        row = runner.model_call("Continue", "session", "codemesh", resume=False,
            structured=True, cwd=tmp_path, model="gpt-5.6-terra", budget=None,
            provider="codex", history=[], working_state=state)
    assert len(captured) == 1
    assert captured[0][1]["model"] == "gpt-5.6-terra"
    assert row["prompt_context"]["policy"] == "focused"
    assert "preserve authorization" in json.dumps(captured[0][0])
    assert json.loads(runner.schema("codemesh", "focused")) == json.loads(runner.schema("codemesh", "working-set"))


def test_public_cli_receives_scope_budget_and_relationship_direction(tmp_path):
    calls = []

    def cli(repo, *args):
        calls.append(args)
        return {"results": [], "sources": [], "status": "fresh"}, 1

    with patch.object(runner, "cli", side_effect=cli):
        runner.tool({"action": "search", "query": "get_default", "path": "pydantic", "max_bytes": 1024},
                    "codemesh", tmp_path, {}, tmp_path, "session", 0, set(), 0)
        runner.tool({"action": "inspect", "node": "symbol:lib.py:resolver", "direction": "both"},
                    "codemesh", tmp_path, {}, tmp_path, "session", 0, set(), 0, bundle_reads=True)
    assert calls[0][calls[0].index("--path") + 1] == "pydantic"
    assert calls[0][calls[0].index("--max-bytes") + 1] == "1024"
    assert calls[1][calls[1].index("--direction") + 1] == "both"
    with pytest.raises(ValueError, match="direction"):
        runner.tool({"action": "inspect", "direction": "invented"}, "codemesh", tmp_path, {}, tmp_path,
                    "session", 0, set(), 0, bundle_reads=True)
