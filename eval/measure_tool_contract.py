#!/usr/bin/env python3
"""Measure legacy/lean tool-contract CLI input bytes, with no model calls.

The real Codex adapter writes its temporary input files, but subprocess.run is
replaced with an in-process capture that raises before launching any process.
These are serialized CLI input sizes, not provider request sizes or token/cost
estimates. The identical sample transcript is excluded from contract totals.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import codex_runner
from vnext_agent import schema, system_prompt


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "eval/results/controlled-next-2026-10-19/CONTRACT_BYTES.json"
TRANSCRIPT = [{"role": "user", "content": "Static contract byte measurement; do not execute."}]


class CapturedInputs(Exception):
    """Stop the adapter before any subprocess or response parsing occurs."""


def capture_cli_inputs(system: str, response_schema: dict[str, Any]) -> dict[str, bytes]:
    captured: dict[str, bytes] = {}

    def capture(argv: list[str], **kwargs: Any) -> None:
        if captured:
            raise AssertionError("Expected exactly one intercepted CLI invocation")
        settings = [argv[index + 1] for index, value in enumerate(argv) if value == "--config"]
        instruction_setting = next(item for item in settings if item.startswith("model_instructions_file="))
        instruction_path = Path(json.loads(instruction_setting.split("=", 1)[1]))
        schema_path = Path(argv[argv.index("--output-schema") + 1])
        captured.update(instructions=instruction_path.read_bytes(), schema=schema_path.read_bytes(),
                        stdin=kwargs["input"].encode("utf-8"))
        raise CapturedInputs()

    with patch.object(codex_runner.subprocess, "run", side_effect=capture):
        try:
            codex_runner.model_call(TRANSCRIPT, system=system, schema=response_schema,
                                    model="gpt-5.6-terra", effort="low")
        except CapturedInputs:
            pass
        else:
            raise AssertionError("CLI input capture did not intercept the subprocess")

    expected_instructions = (codex_runner.PROTOCOL + "\n\n" + system).encode("utf-8")
    expected_schema = json.dumps(codex_runner.strict_schema(response_schema)).encode("utf-8")
    expected_stdin = ("Continue this explicit conversation transcript:\n" +
                      json.dumps(TRANSCRIPT, ensure_ascii=False)).encode("utf-8")
    if captured != {"instructions": expected_instructions, "schema": expected_schema, "stdin": expected_stdin}:
        raise AssertionError("Captured CLI inputs diverged from documented adapter serialization")
    return captured


def byte_record(value: bytes) -> dict[str, Any]:
    return {"bytes": len(value), "sha256": sha256(value).hexdigest()}


def measure() -> dict[str, Any]:
    rows = []
    comparisons = []
    for mode in ("filesystem", "codemesh"):
        for bundles in (False, True):
            pair = {}
            for contract in ("legacy", "lean"):
                system = system_prompt(mode, "auto", "working-set", bundles, contract)
                raw_schema = schema(mode, "working-set", bundles, contract)
                captured = capture_cli_inputs(system, json.loads(raw_schema))
                row = {
                    "mode": mode, "bundle_reads": bundles, "tool_contract": contract,
                    "raw_system_prompt": byte_record(system.encode("utf-8")),
                    "raw_compact_schema": byte_record(raw_schema.encode("utf-8")),
                    "raw_tool_contract_bytes": len(system.encode("utf-8")) + len(raw_schema.encode("utf-8")),
                    "cli_instructions_with_protocol": byte_record(captured["instructions"]),
                    "cli_strict_serialized_schema": byte_record(captured["schema"]),
                    "cli_static_contract_bytes": len(captured["instructions"]) + len(captured["schema"]),
                    "excluded_identical_sample_stdin": byte_record(captured["stdin"]),
                }
                rows.append(row)
                pair[contract] = row
            comparison = {"mode": mode, "bundle_reads": bundles}
            for name in ("raw_tool_contract_bytes", "cli_static_contract_bytes"):
                legacy, lean = pair["legacy"][name], pair["lean"][name]
                comparison[name] = {"legacy": legacy, "lean": lean, "reduction_bytes": legacy - lean,
                                    "reduction_percent": round((legacy - lean) * 100 / legacy, 4)}
            comparisons.append(comparison)

    return {
        "format": "codemesh-static-contract-bytes-v1", "context_policy": "working-set",
        "source_mode": "auto", "encoding": "utf-8", "model_calls": 0, "subprocesses_launched": 0,
        "intercepted_adapter_invocations": len(rows), "serialization_parity_checked": True,
        "method": "Capture actual codex_runner.model_call temporary instruction/schema bytes by mocking "
                  "subprocess.run and stopping before execution. Independently assert PROTOCOL + two newlines "
                  "+ system and json.dumps(strict_schema(schema)) serialization parity.",
        "scope": "Raw tool contract = system prompt plus compact schema. CLI static contract = instruction "
                 "file including protocol prefix plus strict serialized output schema. No separator bytes "
                 "are added when summing separate files.",
        "excluded": ["Identical sample transcript stdin", "model catalog and CLI configuration metadata",
                     "provider wire request and implicit provider instructions", "tokenization and cache behavior"],
        "limitation": "Static serialized CLI input byte comparison only; not provider request, token, cost, "
                      "quality, or end-to-end workflow savings.",
        "source_fingerprints": {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in (
            ROOT / "eval/tool_contract.py", ROOT / "eval/vnext_agent.py", ROOT / "eval/codex_runner.py",
            Path(__file__).resolve())},
        "rows": rows, "comparisons": comparisons,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    report = measure()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"model_calls": 0, "subprocesses_launched": 0,
                      "serialization_parity_checked": report["serialization_parity_checked"],
                      "comparisons": report["comparisons"], "report": str(args.out)}, indent=2))


if __name__ == "__main__":
    main()
