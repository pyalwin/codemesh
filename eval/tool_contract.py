"""Optional concise instructions for the existing single-action eval protocol.

The legacy contract remains the control in vnext_agent.py. This module changes
the description and removes inapplicable filesystem fields, not tool behavior.
"""

from __future__ import annotations

import json

from source_bundles import BUNDLE_BYTES, BUNDLE_SYMBOLS


def lean_schema(mode: str, context_policy: str = "full", bundle_reads: bool = False) -> str:
    actions = ["grep", "read", "edit", "test", "answer"]
    fields = {name: {"type": "string"} for name in ("query", "path")}
    if mode == "codemesh":
        actions = ["grep", "read", "map", "search", "source", "recall", "remember", "edit", "test", "answer"]
        fields.update({name: {"type": "string"} for name in ("node", "relation", "cursor", "mode", "if_hash")})
    fields.update({name: {"type": "integer"} for name in ("start", "limit")})
    fields.update({name: {"type": "string"} for name in ("old", "new")})
    if mode == "codemesh":
        fields["summary"] = {"type": "string"}
    fields["answer"] = {"type": "string"}
    if context_policy in ("working-set", "focused"):
        actions.append("restore")
        fields["handle"] = {"type": "string"}
    if mode == "codemesh":
        fields["max_bytes"] = {"type": "integer", "minimum": 512, "maximum": 65536}
    if bundle_reads:
        actions.append("batch_read")
        fields["reads"] = {"type": "array", "minItems": 1, "maxItems": 8, "items": {
            "type": "object", "properties": {"path": {"type": "string"},
                "start": {"type": "integer"}, "limit": {"type": "integer"}},
            "required": ["path", "start", "limit"], "additionalProperties": False}}
        if mode == "codemesh":
            actions.append("inspect")
            fields["depth"] = {"type": "integer", "enum": [0, 1]}
            fields["direction"] = {"type": "string", "enum": ["outgoing", "incoming", "both"]}
    data = {"type": "object", "properties": {
        "action": {"type": "string", "enum": actions}, **fields},
        "required": ["action", "answer"], "additionalProperties": False}
    return json.dumps(data, separators=(",", ":"))


def lean_system_prompt(mode: str, source_mode: str = "auto", context_policy: str = "full",
                       bundle_reads: bool = False) -> str:
    parts = [
        "Repair the seeded Pydantic regression. Return one JSON action per response; the harness executes it "
        "and returns observations. Never call native tools, code_mode, or internal routers. "
        "Include answer (empty for tool actions); unused optional fields are null. Paths are checkout-relative. "
        "Read exact source before editing; signatures, notes, and model conclusions are unverified hypotheses.",
        "grep(query): literal Python-source search, 1..100 characters. "
        "read(path,start=1,limit=60): positive start, 1..160 lines. "
        "edit(path,old,new): pydantic/*.py only; old must be nonempty and occur exactly once; "
        "old/new each <=4000 characters. test: visible test, at most 3 runs; hidden tests unavailable. "
        "answer: finish with your answer text.",
    ]
    if mode == "codemesh":
        parts.append(
            "Locate with map/search; read with " + ("source/inspect. " if bundle_reads else "source. ") +
            "map(node='repo'|returned ID,relation?,cursor?,max_bytes?): one level. "
            "search(query,path?,limit?,max_bytes?): indexed matches; path=file/directory. "
            "Start at default 8 matches (limit 1..20); broaden if insufficient. "
            "map/search: 8192-byte default, max_bytes=512..65536; on omissions follow nextCursor or raise budget. "
            "source(node=exact ID,mode?,if_hash?): live-checked code/hash; mode=auto|fold|preview|full, "
            f"default {source_mode}; initially unset. auto marks omissions. "
            "Map large classes to member IDs; request full for omitted edit code. "
            "if_hash requires exact current source bytes resident in this session. "
            "remember(path,summary): source-bound handoff for a fresh session. recall(path): notes. "
            "Saved references contain no source bytes; request source by exact reference ID."
        )
    if context_policy in ("working-set", "focused"):
        parts.append(
            "Context is bounded working state, not full history. Preserve unresolved branches. "
            "restore(handle) recovers archived map/search/grep navigation; restore(handle='archive:0') pages "
            "archive navigation/source pointers. Archives are historical, never current source evidence; "
            "re-run source/read for evicted code."
        )
    if bundle_reads:
        parts.append(
            "batch_read(reads=[{path,start,limit},...]): 1..8 explicit spans, each <=160 lines; "
            f"returns at most {BUNDLE_SYMBOLS} spans within {BUNDLE_BYTES} JSON bytes. "
            "Source blocks are exact; check omissions and retrieve missing edit evidence."
        )
        if mode == "codemesh":
            parts.append(
                "inspect(node=exact ID or unique name,depth=1,direction='outgoing'): exact root and verified "
                f"direct neighbors; <={BUNDLE_SYMBOLS} symbols/{BUNDLE_BYTES} JSON bytes. "
                "depth=0|1; direction=outgoing|incoming|both. Check unresolved/ambiguous/omitted entries. "
                "Incoming indexed candidates are incomplete; absence does not prove no caller."
            )
    return "\n".join(parts)
