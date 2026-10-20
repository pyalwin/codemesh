"""Bundled read and reset controls; all provider responses are scripted."""

import copy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import vnext_agent as runner
import source_bundles
from source_bundles import native_bundle, read_reference, read_span, span_reference
from working_state import WorkingState


def fixture(repo, code="def example():\n    return broken\n"):
    path = repo / "pydantic/example.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code)
    return {"path": "pydantic/example.py", "start": 1, "limit": 2}


def test_native_bundle_preserves_exact_code_and_bounds_whole_json(tmp_path):
    request = fixture(tmp_path, "return '" + "😀" * 400 + "'\n")
    small = native_bundle(tmp_path, [request], max_bytes=1024)
    assert small["sources"] == []
    assert small["references"][0]["status"] == "omitted"
    assert len(json.dumps(small, ensure_ascii=False).encode()) < 1024
    full = native_bundle(tmp_path, [request])
    assert full["sources"][0]["source"] == (tmp_path / request["path"]).read_text().rstrip("\n")
    assert full["sources"][0]["hash"] == sha256(full["sources"][0]["source"].encode()).hexdigest()


def test_native_bundle_deduplicates_spans_and_rejects_escape(tmp_path):
    request = fixture(tmp_path)
    result = native_bundle(tmp_path, [request, request, {"path": "../outside.py"}])
    assert len(result["sources"]) == 1
    assert [r["status"] for r in result["references"]] == ["fresh", "duplicate", "error"]


@pytest.mark.parametrize("count", [1, 8])
def test_native_bundle_final_envelope_bound_includes_complete_and_duplicate_labels(tmp_path, count):
    """Final labels grow after the candidate-size check; omit the whole span."""
    path = tmp_path / "x.py"
    request = {"path": "x.py", "start": 1, "limit": 1}
    path.write_text("x" * 100 + "\n")
    initial = native_bundle(tmp_path, [request] * count, max_bytes=1024)
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    assert initial["status"] == "complete"
    # The accepted candidate would fit before final labels, but its final JSON
    # would be 1025 bytes (one beyond the advertised inclusive envelope bound).
    path.write_text("x" * (100 + 1025 - size(initial)) + "\n")
    result = native_bundle(tmp_path, [request] * count, max_bytes=1024)
    assert size(result) <= 1024
    assert result["sources"] == []
    assert [item["status"] for item in result["references"]] == ["omitted"] * count
    assert result["omitted"]["sources"] == count


@pytest.mark.parametrize("failure", ["stale", "changed", "missing"])
@pytest.mark.parametrize("identity", ["id", "requestedId", "saved_index"])
def test_failed_hydration_invalidates_old_source_and_aliases(tmp_path, failure, identity):
    source = {**read_span(tmp_path, fixture(tmp_path)), "id": "symbol:pydantic/example.py:example"}
    state = WorkingState(tmp_path / "archive", "s")
    state.observe("source", {"node": "example"}, json.dumps(source))
    state.assistant({"action": "hydrate"})
    reference = {"index": 0, "status": failure}
    inputs = {}
    if identity == "saved_index":
        inputs["references"] = [{"id": source["id"], "path": source["path"]}]
    else:
        reference[identity] = source["id"]
    state.observe("hydrate", inputs, json.dumps({"sources": [], "references": [reference]}))
    assert not state.resident_hashes()
    assert "return broken" not in json.dumps(state.render())


def test_failed_native_hydration_invalidates_span_via_saved_reference_index(tmp_path):
    source = read_span(tmp_path, fixture(tmp_path))
    state = WorkingState(tmp_path / "archive", "s")
    state.observe("batch_read", {}, json.dumps({"sources": [source]}))
    state.assistant({"action": "hydrate"})
    state.observe("hydrate", {"references": [{"path": source["path"], "lines": source["lines"]}]},
                  json.dumps({"sources": [], "references": [{"index": 0, "status": "changed"}]}))
    assert state.context_stats()["evidence_count"] == 0
    assert "return broken" not in json.dumps(state.render())


@pytest.mark.parametrize("failure", ["stale", "deleted"])
@pytest.mark.parametrize("target", ["root", "callee"])
def test_failed_inspection_invalidates_target_file_but_preserves_other_evidence(tmp_path, failure, target):
    state = WorkingState(tmp_path / "archive", "s")
    delivered = []
    for name in ("broken", "unrelated"):
        body = f"return {name}_sentinel"
        item = {"id": f"symbol:pydantic/{name}.py:{name}", "path": f"pydantic/{name}.py",
                "status": "fresh", "mode": "full", "hash": sha256(body.encode()).hexdigest(), "source": body}
        state.observe("source", {"node": name}, json.dumps(item))
        delivered.append(item)
    state.assistant({"action": "inspect"})
    failed = delivered[0]["id"]
    result = {"sources": [], "rootId": failed, "status": failure} if target == "root" else {
        "sources": [], "rootId": delivered[1]["id"], "status": "fresh",
        "calls": [{"targetId": failed, "status": failure}],
    }
    state.observe("inspect", {"node": failed}, json.dumps(result))
    rendered = json.dumps(state.render())
    assert "broken_sentinel" not in rendered
    assert "unrelated_sentinel" in rendered
    assert state.resident_hashes() == {
        (delivered[1]["id"], "full", delivered[1]["hash"]),
        ("unrelated", "full", delivered[1]["hash"]),
    }


def test_native_hydration_requires_same_file_and_span_hash(tmp_path):
    request = fixture(tmp_path)
    source = read_span(tmp_path, request)
    ref = span_reference(tmp_path, source)
    assert ref is not None
    assert native_bundle(tmp_path, [ref], hydrate=True)["sources"][0] == source
    changed_span = {**ref, "hash": "invalid"}
    assert native_bundle(tmp_path, [changed_span], hydrate=True)["references"][0]["status"] == "changed"
    (tmp_path / request["path"]).write_text(source["source"] + "\n# unrelated revision\n")
    result = native_bundle(tmp_path, [ref], hydrate=True)
    assert result["references"][0]["status"] == "changed" and result["sources"] == []


@pytest.mark.parametrize("hydrate", [False, True])
def test_native_bundle_rechecks_earlier_files_after_later_reads(tmp_path, hydrate):
    first = fixture(tmp_path)
    second_path = tmp_path / "other.py"
    second_path.write_text("return second\n")
    second = {"path": "other.py", "start": 1, "limit": 1}
    requests = [first, first, second]
    if hydrate:
        requests = [span_reference(tmp_path, read_span(tmp_path, item)) for item in requests]
    original = source_bundles.read_span

    def racing_read(repo, request):
        value = original(repo, request)
        if request["path"] == "other.py":
            (tmp_path / first["path"]).write_text("def example():\n    return changed\n")
        return value

    with patch.object(source_bundles, "read_span", side_effect=racing_read):
        result = native_bundle(tmp_path, requests, hydrate=hydrate)
    assert [item["status"] for item in result["references"]] == ["stale", "stale", "fresh"]
    assert [item["path"] for item in result["sources"]] == ["other.py"]
    assert result["status"] == "partial"


@pytest.mark.parametrize("mode", ["preview", "auto"])
def test_saved_omitted_view_requires_proof_of_the_delivered_whole_body(tmp_path, mode):
    full = read_span(tmp_path, fixture(tmp_path))
    preview = "def example():\n    # body omitted"
    delivered = {**full, "id": "symbol:pydantic/example.py:example", "mode": mode,
                 "source": preview, "hash": sha256(preview.encode()).hexdigest(),
                 "omittedRanges": [[2, 2]]}
    assert runner.source_reference(tmp_path, json.dumps(delivered)) is None
    delivered["bodyHash"] = full["hash"]
    saved = runner.source_reference(tmp_path, json.dumps(delivered))
    assert saved is not None and saved["file_hash"] == full["fileHash"]
    # Omitted lines changed after delivery; the visible preview still hashes
    # correctly, but must not acquire the newer file's revision as provenance.
    (tmp_path / full["path"]).write_text("def example():\n    return changed\n")
    delivered.pop("fileHash")
    assert runner.source_reference(tmp_path, json.dumps(delivered)) is None


def test_saved_unomitted_preview_still_requires_exact_live_span(tmp_path):
    source = {**read_span(tmp_path, fixture(tmp_path)), "mode": "preview",
              "id": "symbol:pydantic/example.py:example"}
    assert runner.source_reference(tmp_path, json.dumps(source)) is not None
    (tmp_path / source["path"]).write_text("def example():\n    return changed\n")
    source.pop("fileHash")
    assert runner.source_reference(tmp_path, json.dumps(source)) is None


def test_repeat_metrics_connect_numbered_reads_to_bundled_and_hydrated_spans(tmp_path):
    request = fixture(tmp_path)
    source = read_span(tmp_path, request)
    observation = runner.native_read(request["path"], 1, 2, tmp_path)
    numbered = {"action": "read", "input": request, "observation": observation,
                "observation_bytes": len(observation.encode())}
    bundled = {"action": "batch_read", "input": {}, "observation": json.dumps({"sources": [source]}),
               "observation_bytes": 0, "source_blocks_resident_before": [True]}
    evicted = {**bundled, "source_blocks_resident_before": [False]}
    hydrated = {**bundled, "action": "hydrate", "source_blocks_resident_before": [False]}
    counts = runner.repeated_reads([
        {"phase": 0, "context_epoch": 0, "actions": [numbered, bundled, evicted]},
        {"phase": 2, "context_epoch": 1, "actions": [hydrated]},
    ])
    for kind in ("same_context", "evicted_context", "cross_context"):
        assert counts[f"{kind}_repeat_read_calls"] == 1
        assert counts[f"{kind}_repeat_source_blocks"] == 1
        assert counts[f"{kind}_repeated_bytes"] == len(source["source"].encode())


def test_repeat_metrics_count_one_bundle_call_and_separate_mixed_repeat_blocks(tmp_path):
    base = read_span(tmp_path, fixture(tmp_path))
    sources = [{**base, "id": f"symbol:pydantic/example.py:{name}"} for name in ("one", "two", "three")]

    def bundle(blocks, residents=None):
        return {"action": "inspect", "input": {}, "observation": json.dumps({"sources": blocks}),
                "observation_bytes": 0, "source_blocks_resident_before": residents or []}

    # The final call repeats two resident blocks and one evicted block. Category
    # call counts overlap, while the aggregate counts that call exactly once.
    counts = runner.repeated_reads([
        {"phase": 0, "actions": [bundle(sources)]},
        {"phase": 1, "actions": [bundle(sources, [True, True, False])]},
        {"phase": 2, "actions": [{**bundle(sources), "action": "hydrate"}]},
    ])
    assert counts["repeat_read_calls"] == 2
    assert counts["repeat_source_blocks"] == 6
    assert counts["same_context_repeat_read_calls"] == 1
    assert counts["same_context_repeat_source_blocks"] == 2
    assert counts["evicted_context_repeat_read_calls"] == 1
    assert counts["evicted_context_repeat_source_blocks"] == 1
    assert counts["cross_context_repeat_read_calls"] == 1
    assert counts["cross_context_repeat_source_blocks"] == 3
    assert counts["repeated_observation_bytes"] == 6 * len(base["source"].encode())
    row = {"mode": "codemesh", "status": "complete", "totals": {"cost_usd": None}, **counts}
    summary = runner.summarize([row], ["codemesh"])["codemesh"]
    for key, value in counts.items():
        assert summary[key] == value


def test_numbered_read_reference_rejects_mismatched_delivery(tmp_path):
    request = fixture(tmp_path)
    observation = runner.native_read(request["path"], 1, 2, tmp_path)
    assert read_reference(tmp_path, request, observation)
    assert read_reference(tmp_path, request, observation + " fabricated") is None


def test_bundle_flags_are_independent_and_off_by_default(tmp_path):
    for mode in runner.MODES:
        assert "batch_read" not in json.loads(runner.schema(mode))["properties"]["action"]["enum"]
        enabled = json.loads(runner.schema(mode, bundle_reads=True))["properties"]["action"]["enum"]
        assert "batch_read" in enabled
        assert ("inspect" in enabled) == (mode == "codemesh")
        with pytest.raises(ValueError, match="Unsupported"):
            runner.tool({"action": "batch_read", "reads": []}, mode, tmp_path, {}, Path("/unused"), "s", 0, set(), 0)


def test_bundle_evidence_survives_next_turn_without_duplicate_latest_or_alias_leak(tmp_path):
    source = read_span(tmp_path, fixture(tmp_path))
    symbols = [{**source, "id": f"symbol:pydantic/example.py:{name}"} for name in ("root", "callee")]
    state = WorkingState(tmp_path / "archive", "s")
    state.task("Repair")
    observation = json.dumps({"sources": symbols, "status": "complete"})
    handle = state.observe("inspect", {"node": "root"}, observation)
    assert json.dumps(state.render()).count("return broken") == 2  # One copy per actual source block.
    state.assistant({"action": "answer", "answer": "Reviewing"})
    assert state.context_stats()["evidence_count"] == 2
    assert ("root", "full", source["hash"]) not in state.resident_hashes()
    assert (symbols[1]["id"], "full", source["hash"]) in state.resident_hashes()
    with pytest.raises(ValueError, match="live read"):
        state.restore(handle)
    state.observe("edit", {"path": source["path"], "old": "broken", "new": "fixed"}, "Edited")
    assert not state.resident_hashes()
    assert "return broken" not in json.dumps(state.render())


def test_oversized_hydration_is_delivered_once_without_hash_authority(tmp_path):
    source = {**read_span(tmp_path, fixture(tmp_path)), "id": "symbol:pydantic/example.py:example"}
    state = WorkingState(tmp_path / "archive", "s", evidence_bytes=1)
    state.task("Repair")
    state.observe("hydrate", {}, json.dumps({"sources": [source]}))
    assert "return broken" in json.dumps(state.render())
    assert not state.resident_hashes()
    state.assistant({"action": "answer", "answer": "Reviewed"})
    assert "return broken" not in json.dumps(state.render())


@pytest.mark.parametrize("mode", runner.MODES)
@pytest.mark.parametrize("policy", ["full", "working-set", "focused"])
def test_hydration_enters_reset_context_and_allows_patch_without_another_read(tmp_path, mode, policy):
    snapshots, saved_notes, cli_requests = [], [], []
    symbol = "symbol:pydantic/example.py:example"
    decisions = iter([
        {"action": "source", "node": symbol, "mode": "full", "answer": ""} if mode == "codemesh" else
        {"action": "read", "path": "pydantic/example.py", "start": 1, "limit": 2, "answer": ""},
        {"action": "answer", "answer": "Found it."},
        {"action": "answer", "answer": "Checked impact."},
        "Repair the returned value.",
        {"action": "edit", "path": "pydantic/example.py", "old": "return broken", "new": "return fixed", "answer": ""},
        {"action": "answer", "answer": "Repaired."},
    ])

    def provider(transcript, **kwargs):
        snapshots.append(copy.deepcopy(transcript))
        result = next(decisions)
        return {"decision": result if isinstance(result, dict) else None,
                "text": json.dumps(result) if isinstance(result, dict) else result, "cost_usd": None}

    def worktree(base, path, task, index):
        fixture(path)

    def cli(repo, *args):
        cli_requests.append(args)
        source = {**read_span(repo, {"path": "pydantic/example.py", "start": 1, "limit": 2}), "id": symbol}
        if args[:2] == ("explore", "source"):
            return source, 1
        if args[0] == "remember":
            saved_notes.append({"summary": args[args.index("--summary") + 1], "status": "hypothesis"})
            return {"success": True}, 1
        if args[:2] == ("explore", "recall"):
            return {"notes": saved_notes}, 1
        if args[:2] == ("explore", "hydrate"):
            refs = json.loads(args[args.index("--references") + 1])
            assert refs[0]["fileHash"] == source["fileHash"]
            return {"status": "complete", "sources": [source], "references": [{"index": 0, "status": "fresh"}]}, 1
        raise AssertionError(args)

    task = {"id": "hydration", "path": "pydantic/example.py", "symptom": "Wrong return.",
            "impact_question": "Preserve callers.", "hidden_test": "unused.py"}
    with patch.dict("sys.modules", {"codex_runner": SimpleNamespace(model_call=provider)}), \
            patch.object(runner, "worktree", side_effect=worktree), \
            patch.object(runner, "remove_worktree"), \
            patch.object(runner, "pytest", return_value={"passed": True}), \
            patch.object(runner, "cli", side_effect=cli):
        result = runner.trajectory(tmp_path, task, mode, 1, tmp_path, tmp_path / "unused.db",
            Path("/unused/python"), "gpt-6-luna", None, 4, provider="codex",
            context_policy=policy, hydrate_on_resume=True)
    assert result["status"] == "complete" and result["final_patch_pass"]
    assert result["harness_hydration_calls"] == 1 and result["handoff_reference_count"] == 1
    assert result["source_calls"] == (1 if mode == "codemesh" else 0)
    assert "return broken" in json.dumps(snapshots[4])  # First decision after reset has exact live source.
    assert result["phases"][2]["actions"][0]["action"] == "hydrate"
    if policy in ("working-set", "focused"):
        after_edit = json.loads(snapshots[5][0]["content"].split("\n", 1)[1])
        assert after_edit["retained_evidence"] == []  # The edit invalidated hydrated code.
