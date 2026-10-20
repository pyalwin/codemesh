"""Deterministic compact navigation over the original exact-evidence state.

This policy changes presentation, not source authority. It does not select a
branch on the model's behalf or interpret navigation as proof of relevance.
Every original observation remains in WorkingState's immutable archive.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from working_state import NAVIGATION, WorkingState, _json, _object, _path


def _bounded(value: Any, limit: int = 512) -> bool:
    return isinstance(value, str) and bool(value) and len(value.encode()) <= limit


def _entries(result: dict) -> list[dict]:
    values = result.get("entries", result.get("results", []))
    return [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def _paths(record: dict) -> set[str]:
    result = _object(record["observation"])
    paths = set()
    for value in [record["input"], result, result.get("node", {})] + _entries(result):
        if not isinstance(value, dict):
            continue
        for key in ("path", "filePath"):
            path = _path(value.get(key))
            if path:
                paths.add(path)
        for key in ("id", "node"):
            identity = value.get(key)
            if isinstance(identity, str) and identity.startswith(("symbol:", "file:")):
                path = _path(identity.split(":", 2)[1])
                if path:
                    paths.add(path)
    return paths


def _request_key(record: dict) -> str:
    inputs = record["input"]
    # Retain every supplied tool option, including scope and output budgets.
    # Only harness narration and absent values cannot change the request.
    query = {key: value for key, value in inputs.items()
             if key not in ("action", "answer") and value is not None}
    if record["action"] == "map" and not inputs.get("query") and not inputs.get("symbol"):
        query.update(node=inputs.get("node") or "repo", relation=inputs.get("relation") or "contains",
                     cursor=inputs.get("cursor") or "0", limit=inputs.get("limit") or 30)
    return record["action"] + ":" + _json(query)


class FocusedWorkingState(WorkingState):
    """Base-compatible bounded context without a decision model or summaries."""

    policy = "focused"

    def reset(self, session_id: str, task_message: str = "") -> None:
        super().reset(session_id, task_message)
        self._requests: OrderedDict[str, dict] = OrderedDict()
        self._duplicates: OrderedDict[str, str] = OrderedDict()
        self._root_pages: OrderedDict[str, dict] = OrderedDict()
        self._pending: OrderedDict[str, dict] = OrderedDict()
        self._root_omitted_pages = 0
        self._pending_omitted = 0
        self._collapsed_requests = 0

    def _navigation(self, handle: str, record: dict, *, replayed: bool = False) -> None:
        # Remove a superseded pointer BEFORE base ledger trimming so a repeated
        # request does not evict an unrelated branch from a full ledger.
        result = _object(record["observation"])
        successful = (not replayed and record["action"] in NAVIGATION and not record["error"]
                      and result.get("status") not in ("stale", "deleted", "failed", "error"))
        key = _request_key(record) if successful else None
        previous = self._requests.get(key) if key else None
        if previous:
            self._ledger = [entry for entry in self._ledger if entry["handle"] != previous["handle"]]
        super()._navigation(handle, record, replayed=replayed)
        if not successful:
            return
        digest = sha256(record["observation"].encode()).hexdigest()
        if previous:
            self._collapsed_requests += 1
            current = next((entry for entry in self._ledger if entry["handle"] == handle), None)
            if current is not None:
                current["supersedes"] = previous["handle"]
            if previous["digest"] == digest and previous["revision"] == self._revision and not previous["stale"]:
                self._duplicates[handle] = previous["handle"]
                if current is not None:
                    current["same_result_as"] = previous["handle"]
        self._requests[key] = {"handle": handle, "digest": digest, "revision": self._revision,
                               "paths": _paths(record), "stale": False}
        self._requests.move_to_end(key)
        while len(self._requests) > 128:
            self._requests.popitem(last=False)
        while len(self._duplicates) > 128:
            self._duplicates.popitem(last=False)
        while self._ledger and len(_json(self._ledger).encode()) > self.ledger_budget:
            self._ledger.pop(0)
        if record["action"] != "map":
            return
        parent = result.get("node")
        requested = record["input"].get("node") or "repo"
        # Legacy/partial maps and cross-relation maps never establish containment.
        if not isinstance(parent, dict) or not _bounded(parent.get("id")) or parent["id"] != requested:
            return
        relation = record["input"].get("relation") or "contains"
        requested_cursor = record["input"].get("cursor")
        for pending_key, pending in list(self._pending.items()):
            if (isinstance(requested_cursor, str) and pending["node"] == requested
                    and pending["relation"] == relation and pending.get("pending_cursor") == requested_cursor):
                del self._pending[pending_key]
        cursor = result.get("nextCursor")
        if isinstance(cursor, str) and cursor:
            pending_key = _json([requested, relation, cursor])
            pending = {"handle": handle, "node": requested, "relation": relation,
                       "session_id": self.session_id, "revision": record["revision"], "paths": _paths(record)}
            if _bounded(cursor):
                pending["pending_cursor"] = cursor
            else:
                pending["pending_cursor_archived"] = True
            self._pending[pending_key] = pending
            self._pending.move_to_end(pending_key)
            while len(self._pending) > 16:
                self._pending.popitem(last=False)
                self._pending_omitted += 1
        if requested != "repo" or relation != "contains":
            return
        compact = [{key: value[key] for key in ("id", "name", "type", "path") if _bounded(value.get(key))}
                   for value in _entries(result) if _bounded(value.get("id"))]
        page = {"handle": handle, "entries": compact[:32], "omitted_entries": max(0, len(compact) - 32)}
        if isinstance(cursor, str) and cursor:
            if _bounded(cursor):
                page["pending_cursor"] = cursor
            else:
                page["pending_cursor_archived"] = True
        self._root_pages[key] = page
        self._root_pages.move_to_end(key)
        while len(self._root_pages) > 8:
            self._root_pages.popitem(last=False)
            self._root_omitted_pages += 1

    def invalidate(self, path: str) -> None:
        super().invalidate(path)
        normalized = _path(path)
        for record in list(self._requests.values()) + list(self._pending.values()):
            if normalized in record["paths"]:
                record["stale"] = True
        for page in self._root_pages.values():
            for entry in page["entries"]:
                if entry.get("path") == normalized or entry.get("id") == f"file:{normalized}":
                    entry["stale"] = True

    def _navigation_view(self) -> dict:
        entries: OrderedDict[str, dict] = OrderedDict()
        pages = []
        omitted = 0
        for page in self._root_pages.values():
            for entry in page["entries"]:
                compact = deepcopy(entry)
                identity = entry["id"]
                prefix, _, remainder = identity.partition(":")
                if compact.get("type") == prefix:
                    compact.pop("type")
                if compact.get("path") == remainder:
                    compact.pop("path")
                if compact.get("name") in (identity, remainder, remainder.rsplit("/", 1)[-1]):
                    compact.pop("name", None)
                entries[identity] = compact
            # Outstanding cursors live in pending_pages; a root page handle is
            # sufficient to recover older pagination metadata from the archive.
            pages.append({"handle": page["handle"]})
            omitted += page["omitted_entries"]

        def current(entry):
            return (entry.get("session_id") == self.session_id and entry.get("revision") == self._revision
                    and not entry.get("replayed") and not entry.get("stale"))

        def compact_provenance(entry):
            value = deepcopy(entry)
            if current(value):
                value.pop("session_id", None)
                value.pop("revision", None)
            return value

        root_handles = {page["handle"] for page in pages}
        resident_handles = {value["handle"] for value in self._evidence.values()}
        ledger = [compact_provenance(entry) for entry in self._ledger
                  if not (current(entry) and (entry["handle"] in root_handles
                                             or (entry["action"] in ("source", "read") and entry["handle"] in resident_handles)))]
        nav = {"provenance": {"session_id": self.session_id, "revision": self._revision}}
        if pages:
            main = {"entries": list(entries.values()), "pages": pages}
            if omitted:
                main["omitted_entries"] = omitted
            if self._root_omitted_pages:
                main["omitted_pages"] = self._root_omitted_pages
            nav["main_index"] = main
        if ledger:
            nav["ledger"] = ledger
        if self._pending:
            nav["pending_pages"] = [compact_provenance({key: value for key, value in pending.items() if key != "paths"})
                                    for pending in self._pending.values()]
        if self._pending_omitted:
            nav["pending_pages_omitted"] = self._pending_omitted
        if len(nav) == 1:
            return {}
        removed: dict[str, int] = {}
        # Hard budgets can evict pointers, never the independent exact evidence.
        # The archive root is always also present outside this bounded section.
        for name, values in (("ledger", nav.get("ledger", [])), ("root_entries", nav.get("main_index", {}).get("entries", [])),
                             ("root_pages", nav.get("main_index", {}).get("pages", [])), ("pending_pages", nav.get("pending_pages", []))):
            while values and len(_json(nav).encode()) > self.ledger_budget:
                values.pop(0)
                removed[name] = removed.get(name, 0) + 1
                nav["budget_omissions"] = removed
        for key in ("ledger", "pending_pages"):
            if key in nav and not nav[key]:
                nav.pop(key)
        if len(_json(nav).encode()) > self.ledger_budget:
            fallback = {"budget_omitted_pointers": True}
            return fallback if len(_json(fallback).encode()) <= self.ledger_budget else {}
        return nav

    def render(self) -> list[dict[str, str]]:
        messages = super().render()
        base = json.loads(messages[0]["content"].split("\n", 1)[1])
        # Stable task and exact evidence first, without sorted top-level keys.
        payload = {"initial_task": base["initial_task"]}
        if "current_phase" in base:
            payload["current_phase"] = base["current_phase"]
        payload["retained_evidence"] = base["retained_evidence"]
        for key in ("prior_model_hypotheses", "successful_edits", "latest_test", "latest_tool_error"):
            if key in base:
                payload[key] = base[key]
        payload["navigation"] = self._navigation_view()
        payload["archive"] = base["archive"]
        payload["policy"] = self.policy
        payload["revision"] = base["revision"]
        if self._collapsed_requests:
            payload["collapsed_requests"] = self._collapsed_requests
        messages[0]["content"] = ("Deterministic focused working state. Retained evidence preserves current-session "
                                    "tool bytes; known edits invalidate affected source. Model-authored hypotheses are "
                                    "unverified. Navigation pointers do not establish branch relevance. Superseded "
                                    "observations remain in the immutable archive; restored navigation is historical. "
                                    "Evicted source requires a live read.\n"
                                    + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        latest = self._latest
        if latest and not latest["replayed"] and latest["handle"] in self._duplicates:
            messages[-1] = {"role": "user", "content": "Repeated navigation observation: " + _json({
                "handle": latest["handle"], "action": latest["action"], "same_result_as": self._duplicates[latest["handle"]]})
                + "\nThe exact output remains recoverable with restore(handle)."}
        return messages

    def context_stats(self) -> dict[str, int]:
        stats = super().context_stats()
        stats.update(focused_navigation_bytes=len(_json(self._navigation_view()).encode()),
                     collapsed_requests=self._collapsed_requests, pending_page_count=len(self._pending),
                     root_page_count=len(self._root_pages))
        return stats
