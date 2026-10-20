#!/usr/bin/env python3
"""Run the frozen controlled matrix. Initialization/preparation never call models."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "eval/results/controlled-next-live-2026-10-19"
PYTHON = Path("/tmp/codemesh-edit-eval-venv/bin/python")
REPO = Path("/tmp/codemesh-pydantic-benchmark")
VARIANTS = {
    "A": {"tool_contract": "legacy", "search_tie_break": "pagerank", "hydrate_on_resume": False},
    "B": {"tool_contract": "lean", "search_tie_break": "pagerank", "hydrate_on_resume": False},
    "C": {"tool_contract": "lean", "search_tie_break": "path-depth", "hydrate_on_resume": False},
    "D": {"tool_contract": "lean", "search_tie_break": "path-depth", "hydrate_on_resume": True},
}


def load(path):
    return json.loads(path.read_text())


def journal(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def clone(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if subprocess.run(["cp", "-c", str(source), str(destination)], capture_output=True).returncode:
        shutil.copy2(source, destination)


def initialize(out):
    if (out / "matrix.json").exists():
        raise RuntimeError("Matrix already initialized; use the frozen settings")
    frozen = out / "frozen_source"
    files = [p for p in (ROOT / "package.json", ROOT / "package-lock.json", ROOT / "bun.lock", ROOT / "pnpm-lock.yaml") if p.is_file()]
    files += [p for directory in ("src", "dist") for p in (ROOT / directory).rglob("*")
              if p.is_file() and ".test." not in p.name and p.suffix in (".ts", ".js", ".scm")]
    files += [ROOT / "eval" / name for name in (
        "vnext_agent.py", "codex_runner.py", "working_state.py", "focused_state.py", "tool_contract.py",
        "source_bundles.py", "grep_router.py", "vnext_contract.py", "codex_luna_catalog.json",
        "codex_terra_catalog.json", "edit_workflow_tasks.json", "graph_workflow_tasks.json")]
    files += list((ROOT / "eval/edit_tasks/hidden").glob("*.py"))
    for source in files:
        destination = frozen / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    old = load(frozen / "eval/edit_workflow_tasks.json")
    new = load(frozen / "eval/graph_workflow_tasks.json")
    assert old["commit"] == new["commit"] and old["repository"] == new["repository"]
    combined = {**old, "tasks": old["tasks"] + new["tasks"]}
    (frozen / "eval/controlled_tasks.json").write_text(json.dumps(combined, indent=2) + "\n")
    manifest = {str(p.relative_to(frozen)): {"bytes": p.stat().st_size, "sha256": sha256(p.read_bytes()).hexdigest()}
                for p in sorted(frozen.rglob("*")) if p.is_file()}
    (frozen / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    sys.path.insert(0, str(frozen / "eval"))
    import vnext_agent as runner
    from codex_runner import catalog_fingerprint
    version = subprocess.check_output(["codex", "--version"], text=True).strip()
    common = {"commit": runner.PIN, "build_fingerprint": runner.build_fingerprint(),
              "provider": "codex", "cli_version": version, "effort": "low",
              "model_catalog_sha256": catalog_fingerprint("gpt-5.6-terra"),
              "context_transport": "explicit_transcript", "context_policy": "working-set",
              "evidence_bytes": 16384, "ledger_entries": 8, "source_mode": "auto",
              "handoff_references": True, "bundle_reads": True,
              "bundle_limits": {"maxBytes": 24576, "maxSymbols": 4},
              "modes": ["filesystem", "codemesh"], "model": "gpt-5.6-terra",
              "repeats": 1, "max_turns": 12, "budget_usd": None}
    runs = []
    for stage, block, order in [("pilot", 0, "ABCD"), ("full", 1, "ABCD"),
                                ("full", 2, "CDAB"), ("full", 3, "BDCA")]:
        tasks = "eval/graph_workflow_tasks.json" if stage == "pilot" else "eval/controlled_tasks.json"
        task_ids = [t["id"] for t in load(frozen / tasks)["tasks"]]
        seed = 20261019 if stage == "pilot" else 41 + block
        for variant in order:
            path = f"pilot/{variant}" if stage == "pilot" else f"full/block-{block}/{variant}"
            expected = {**common, **VARIANTS[variant], "task_ids": task_ids, "seed": seed,
                        "protocol_fingerprint": runner.protocol_fingerprint(frozen / tasks)}
            runs.append({"id": path.replace("/", "-"), "stage": stage, "variant": variant,
                         "suite": "new" if stage == "pilot" else "combined", "block": block,
                         "seed": seed, "path": path, "task_ids": task_ids, "repeats": 1,
                         "tasks_manifest": tasks, "expected_config": expected})
    matrix = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "repo": str(REPO), "commit": old["commit"], "python": str(PYTHON),
              "arms": common["modes"], "variants": VARIANTS, "common_config": common,
              "concurrent_runners": 4, "runs": runs,
              "primary_quality": ["final_patch_pass", "workflow_complete"],
              "limitations": ["CLI cost in USD unavailable", "Concurrent warmed caching is observational",
                              "Pilot excluded from full comparison", "Incremental, not factorial, treatment effects"]}
    (out / "matrix.json").write_text(json.dumps(matrix, indent=2) + "\n")
    # Existing prepared new-task indexes were validated on this exact build.
    previous = ROOT / "eval/results/controlled-next-2026-10-19/prepare/indexes"
    for check in previous.glob("*.json"):
        if load(check).get("build_fingerprint") == common["build_fingerprint"]:
            clone(check, out / "prepared/indexes" / check.name)
            clone(check.with_suffix(".db"), out / "prepared/indexes" / check.with_suffix(".db").name)
    print(f"Frozen {len(manifest)} files; planned 16 pilot and 120 full trajectories", flush=True)


def verify_freeze(out):
    frozen = out / "frozen_source"
    for relative, meta in load(frozen / "manifest.json").items():
        path = frozen / relative
        if not path.is_file() or path.stat().st_size != meta["bytes"] or sha256(path.read_bytes()).hexdigest() != meta["sha256"]:
            raise RuntimeError(f"Frozen source changed: {relative}")


def arguments(out, matrix, run):
    cfg = run["expected_config"]
    result = [matrix["python"], str(out / "frozen_source/eval/vnext_agent.py"),
              "--repo", matrix["repo"], "--python", matrix["python"],
              "--tasks", str(out / "frozen_source" / run["tasks_manifest"]),
              "--out", str(out / run["path"]), "--run", "--resume",
              "--provider", "codex", "--model", cfg["model"], "--effort", cfg["effort"],
              "--context-policy", cfg["context_policy"], "--source-mode", cfg["source_mode"],
              "--evidence-bytes", str(cfg["evidence_bytes"]), "--ledger-entries", str(cfg["ledger_entries"]),
              "--modes", ",".join(cfg["modes"]), "--repeats", "1", "--seed", str(cfg["seed"]),
              "--max-turns", str(cfg["max_turns"]), "--bundle-reads",
              "--tool-contract", cfg["tool_contract"], "--search-tie-break", cfg["search_tie_break"]]
    if cfg["hydrate_on_resume"]:
        result.append("--hydrate-handoff")
    return result


def run_cell(out, matrix, run):
    destination = out / run["path"]
    destination.mkdir(parents=True, exist_ok=True)
    for task in run["task_ids"]:
        for suffix in (".db", ".json"):
            source = out / "prepared/indexes" / (task + suffix)
            if not source.exists():
                raise RuntimeError(f"Prepare missing fixture before live execution: {source}")
            target = destination / "indexes" / source.name
            if not target.exists():
                clone(source, target)
    for launch in range(1, 4):
        print(f"{run['id']}: launch {launch}", flush=True)
        with (destination / "console.log").open("a") as log:
            log.write(f"\nLaunch {launch}: {datetime.now(timezone.utc).isoformat()}\n")
            log.flush()
            result = subprocess.run(arguments(out, matrix, run), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        actual = load(destination / "run_config.json") if (destination / "run_config.json").exists() else None
        if actual != run["expected_config"]:
            raise RuntimeError(f"Configuration mismatch in {run['id']}")
        rows = journal(destination / "runs.jsonl")
        expected = {(task, mode, 1) for task in run["task_ids"] for mode in matrix["arms"]}
        graded = [r for r in rows if r["status"] in ("complete", "turn_limit", "context_limit")]
        keys = [(r["task"], r["mode"], r["repeat"]) for r in graded]
        if result.returncode == 0 and len(keys) == len(expected) and set(keys) == expected:
            print(f"{run['id']}: finished {len(keys)} trajectories, final acceptance {sum(r['final_patch_pass'] for r in graded)}/{len(keys)}", flush=True)
            return
        latest = rows[-1] if rows else {}
        errors = " ".join(str(c.get("error", "")) for c in latest.get("calls", [])).lower()
        transient = any(word in errors for word in ("timed out", "timeout", "stream disconnected", "connection reset", "429", "502", "503", "504"))
        protocol_failure = any(c.get("router_error_count", 0) for c in latest.get("calls", []))
        if not transient or protocol_failure or launch == 3:
            raise RuntimeError(f"Stopped {run['id']} (exit {result.returncode}); inspect console.log and calls.jsonl")
        print(f"{run['id']}: preserved transient failed attempt; resuming unfinished work", flush=True)


def pilot_gate(out, matrix):
    for run in (r for r in matrix["runs"] if r["stage"] == "pilot"):
        rows = journal(out / run["path"] / "runs.jsonl")
        graded = [r for r in rows if r["status"] in ("complete", "turn_limit", "context_limit")]
        if len(graded) != len(run["task_ids"]) * len(matrix["arms"]) or any(not r.get("call_protocol_valid") for r in graded):
            raise RuntimeError("Pilot coverage/protocol gate has not passed")
    audit = out / "VALIDATION-pilot.json"
    if not audit.exists() or load(audit).get("passed") is not True:
        raise RuntimeError("Pilot integrity audit must pass before the full comparison")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stage", choices=("initialize", "prepare", "pilot", "full"), required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.stage == "initialize":
        initialize(out)
        return
    verify_freeze(out)
    matrix = load(out / "matrix.json")
    if args.stage == "prepare":
        subprocess.run([matrix["python"], str(out / "frozen_source/eval/vnext_agent.py"), "--repo", matrix["repo"],
                        "--python", matrix["python"], "--tasks", str(out / "frozen_source/eval/controlled_tasks.json"),
                        "--out", str(out / "prepared")], cwd=ROOT, check=True)
        return
    if args.stage == "full":
        pilot_gate(out, matrix)
    selected = [run for run in matrix["runs"] if run["stage"] == args.stage]
    for block in sorted({r["block"] for r in selected}):
        with ThreadPoolExecutor(max_workers=matrix["concurrent_runners"]) as executor:
            futures = [executor.submit(run_cell, out, matrix, run) for run in selected if run["block"] == block]
            errors = []
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    errors.append(str(exc))
                    print(str(exc), flush=True)
            if errors:
                raise RuntimeError("Matrix block stopped; prior results preserved")
    print(f"{args.stage} execution finished", flush=True)


if __name__ == "__main__":
    main()
