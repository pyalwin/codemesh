"""Integration gates for opt-in contracts, search controls, and multi-file repairs."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import vnext_agent as runner


def fixture(base):
    package = base / "pydantic"
    package.mkdir(parents=True)
    (package / "entry.py").write_text("value = 'good'\nvalue2 = 'good'\n")
    (package / "helper.py").write_text("result = 'good'\n")
    return {"id": "two_files", "path": "pydantic/entry.py", "mutations": [
        {"path": "pydantic/entry.py", "old": "'good'", "mutant": "'bad'", "occurrence": 2},
        {"path": "pydantic/helper.py", "old": "'good'", "mutant": "'bad'"}],
        "visible_test": "# test fixture\n", "hidden_test": "unused.py",
        "symptom": "Repair the values.", "impact_question": "Follow the helper."}


def test_multifile_seeding_and_index_fingerprint_include_secondary_file(tmp_path):
    task = fixture(tmp_path)
    before = {path: path.read_bytes() for path in (tmp_path / "pydantic").glob("*.py")}
    sources = runner.seeded_sources(tmp_path, task)
    assert sources == {"pydantic/entry.py": b"value = 'good'\nvalue2 = 'bad'\n",
                       "pydantic/helper.py": b"result = 'bad'\n"}
    original = runner.seed_fingerprint(tmp_path, task)
    changed = copy.deepcopy(task)
    changed["mutations"][1]["mutant"] = "'other bug'"
    assert runner.seed_fingerprint(tmp_path, changed) != original
    reordered = {**task, "mutations": list(reversed(task["mutations"]))}
    assert runner.seed_fingerprint(tmp_path, reordered) == original
    assert all(path.read_bytes() == content for path, content in before.items())


def test_single_file_manifest_remains_compatible(tmp_path):
    task = fixture(tmp_path)
    legacy = {**task, **task["mutations"][0]}
    del legacy["mutations"]
    assert runner.seeded_source(tmp_path, legacy) == b"value = 'good'\nvalue2 = 'bad'\n"


@pytest.mark.parametrize("mutations", [None, [], [None], [{"path": "pydantic/../entry.py"}],
    [{"path": "pydantic/entry.py", "old": "'good'", "mutant": "'bad'", "occurrence": True}],
    [{"path": "pydantic/entry.py", "old": "absent", "mutant": "'bad'"}]])
def test_invalid_seed_fails_before_creating_a_worktree(tmp_path, mutations):
    task = fixture(tmp_path)
    task["mutations"] = mutations
    with patch.object(runner, "command") as command:
        with pytest.raises(ValueError):
            runner.worktree(tmp_path, tmp_path / "new", task)
    command.assert_not_called()


def test_worktree_writes_each_seeded_file(tmp_path):
    base = tmp_path / "base"
    task = fixture(base)
    target = tmp_path / "tree"

    def add_worktree(argv, cwd):
        assert cwd == base
        fixture(Path(argv[-2]))
        return SimpleNamespace(returncode=0)

    with patch.object(runner, "command", side_effect=add_worktree):
        runner.worktree(base, target, task)
    assert (target / "pydantic/entry.py").read_bytes() == runner.seeded_sources(base, task)[task["path"]]
    assert (target / "pydantic/helper.py").read_text() == "result = 'bad'\n"
    assert "'bad'" not in (base / "pydantic/helper.py").read_text()


@pytest.mark.parametrize("tie_break", ["pagerank", "path-depth"])
def test_fixed_ranking_control_reaches_search_cli(tmp_path, tie_break):
    with patch.object(runner, "cli", return_value=({"results": []}, 1)) as cli:
        runner.tool({"action": "search", "query": "check", "path": "pkg"}, "codemesh",
                    tmp_path, {}, Path("unused"), "session", 0, set(), 0,
                    search_tie_break=tie_break)
    arguments = cli.call_args.args
    if tie_break == "path-depth":
        assert arguments[arguments.index("--exact-tie-break") + 1] == "path-depth"
    else:
        assert "--exact-tie-break" not in arguments
    assert arguments[arguments.index("--path") + 1] == "pkg"


def test_two_file_trajectory_captures_full_patch_and_lean_provider_inputs(tmp_path):
    base = tmp_path / "base"
    task = fixture(base)
    out = tmp_path / "run"
    out.mkdir()
    decisions = iter([
        {"action": "read", "path": "pydantic/entry.py", "answer": ""},
        {"action": "read", "path": "pydantic/helper.py", "answer": ""},
        {"action": "answer", "answer": "Both values need repair."},
        {"action": "answer", "answer": "Both values must be preserved."},
        "Repair both values in entry.py and helper.py.",
        {"action": "read", "path": "pydantic/entry.py", "answer": ""},
        {"action": "edit", "path": "pydantic/entry.py", "old": "'bad'", "new": "'good'", "answer": ""},
        {"action": "read", "path": "pydantic/helper.py", "answer": ""},
        {"action": "edit", "path": "pydantic/helper.py", "old": "'bad'", "new": "'good'", "answer": ""},
        {"action": "answer", "answer": "Both repaired."},
    ])
    schemas = []

    def provider(history, **kwargs):
        assert "Read exact source before editing" in kwargs["system"]
        schemas.append(kwargs["schema"])
        decision = next(decisions)
        structured = isinstance(decision, dict)
        assert (kwargs["schema"] is not None) == structured
        return {"decision": decision if structured else None,
                "text": json.dumps(decision) if structured else decision, "cost_usd": None}

    def worktree(base, path, task, index):
        fixture(path)
        for name, content in runner.seeded_sources(base, task).items():
            (path / name).write_bytes(content)

    def judge(path, python, test):
        passed = all("'bad'" not in file.read_text() for file in (path / "pydantic").glob("*.py"))
        return {"passed": passed}

    with patch.dict("sys.modules", {"codex_runner": SimpleNamespace(model_call=provider)}), \
            patch.object(runner, "worktree", side_effect=worktree), \
            patch.object(runner, "remove_worktree"), patch.object(runner, "pytest", side_effect=judge):
        result = runner.trajectory(base, task, "filesystem", 1, out, tmp_path / "unused.db",
            Path("unused"), "gpt-5.6-terra", None, 6, provider="codex",
            context_policy="working-set", tool_contract="lean", search_tie_break="path-depth")
    assert result["status"] == "complete" and result["final_patch_pass"]
    assert result["first_patch_pass"] is False  # An individual edit cannot fix two files.
    assert result["first_correct_edit"]["edit_count"] == 2
    assert result["tool_contract"] == "lean" and result["search_tie_break"] == "path-depth"
    assert result["mutation_paths"] == ["pydantic/entry.py", "pydantic/helper.py"]
    diff = Path(result["patch_path"]).read_text()
    assert "+++ b/pydantic/entry.py" in diff and "+++ b/pydantic/helper.py" in diff
    assert all("node" not in schema["properties"] for schema in schemas if schema)
    assert all("restore" in schema["properties"]["action"]["enum"] for schema in schemas if schema)
