"""Explicit navigation selection over the unchanged exact-evidence working set.

The selector may retain or archive observed navigation for a reasoner. Traversal
alone never classifies another branch as irrelevant. Source retention, archive
integrity and conditional-read authority remain owned by WorkingState.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
import re
from typing import Any

from source_bundles import source_blocks
from working_state import BUNDLES, NAVIGATION, WorkingState, _json, _object, _path


PROTECTED = ["task", "root_index", "exact_source", "tests", "errors"]


def _id(value: Any) -> str | None:
    return value if isinstance(value, str) and 0 < len(value.encode()) <= 512 else None


def _items(result: dict) -> list[dict]:
    value = result.get("entries", result.get("results", []))
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _paths(record: dict) -> set[str]:
    result = _object(record["observation"])
    values = [record["input"], result, result.get("node", {})] + _items(result)
    paths = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        for key in ("path", "filePath"):
            if _path(item.get(key)):
                paths.add(_path(item[key]))
        for key in ("id", "node"):
            node = item.get(key)
            if isinstance(node, str) and node.startswith(("symbol:", "file:")):
                path = node.split(":", 2)[1]
                if _path(path):
                    paths.add(_path(path))
    if record["action"] == "grep":
        paths.update(match.group(1) for match in re.finditer(r"^(?:\./)?([^\n:]+):\d+:", record["observation"], re.MULTILINE)
                     if _path(match.group(1)))
    return paths


class SelectedWorkingState(WorkingState):
    """Same lifecycle as WorkingState, with explicit, atomic navigation choices."""

    policy = "selected"

    def reset(self, session_id: str, task_message: str = "") -> None:
        super().reset(session_id, task_message)
        self._session_start = self._sequence
        self._selected: OrderedDict[str, dict] = OrderedDict()
        self._archived_selection: set[str] = set()
        self._replayed: set[str] = set()
        self._changed_paths: dict[str, int] = {}
        self._known: OrderedDict[str, dict] = OrderedDict()
        self._parents: dict[str, tuple[str, str] | None] = {}
        self._root_entries: OrderedDict[str, dict] = OrderedDict()
        self._root_pages: list[dict] = []
        self._root_omitted = 0
        self._focus_id: str | None = None
        self._selection_history: list[dict] = []
        self._selection_count = 0
        self._stale_selected: list[str] = []
        self._pending_pages: OrderedDict[str, dict] = OrderedDict()
        self._pending_omitted = 0

    @property
    def selected_navigation_budget(self) -> int:
        # Leave room for the compact main index and recoverable frontier pointers.
        return self.ledger_budget // 2 if self.ledger_budget >= 1024 else 0

    @staticmethod
    def _root(record: dict) -> bool:
        result = _object(record["observation"])
        return (record["action"] == "map" and record["input"].get("node", "repo") == "repo"
                and record["input"].get("relation", "contains") == "contains"
                and isinstance(result.get("node"), dict) and result["node"].get("id") == "repo")

    def _invalid_reason(self, handle: str, record: dict) -> str | None:
        if record["action"] not in NAVIGATION:
            return "Only navigation observations may be selected; exact source is protected"
        if record["session_id"] != self.session_id or record["sequence"] <= self._session_start:
            return "Historical session: obtain a live observation"
        if handle in self._replayed:
            return "Replayed navigation: obtain a live observation"
        result = _object(record["observation"])
        if record["error"] or result.get("status") in ("stale", "deleted", "error", "failed"):
            return "Failed or stale navigation cannot authorize selection"
        if any(self._changed_paths.get(path, -1) > record["revision"] for path in _paths(record)):
            return "Navigation predates an edit to a referenced file"
        return None

    def _remember_node(self, node: dict, handle: str, record: dict) -> None:
        identity = _id(node.get("id"))
        if not identity:
            return
        compact = {key: node[key] for key in ("id", "name", "type", "path")
                   if isinstance(node.get(key), str) and len(node[key].encode()) <= 512}
        self._known[identity] = {"node": compact, "handle": handle, "revision": record["revision"],
                                 "paths": _paths(record)}
        self._known.move_to_end(identity)
        while len(self._known) > 256:
            evicted, _ = self._known.popitem(last=False)
            self._parents.pop(evicted, None)

    def _navigation(self, handle: str, record: dict, *, replayed: bool = False) -> None:
        super()._navigation(handle, record, replayed=replayed)
        if replayed:
            self._replayed.add(handle)
            return
        result = _object(record["observation"])
        if record["error"] or result.get("status") in ("stale", "deleted", "error", "failed"):
            return
        action = record["action"]
        if action in NAVIGATION:
            for item in _items(result):
                self._remember_node(item, handle, record)
        elif action == "source":
            if result.get("status") == "fresh":
                self._remember_node(result, handle, record)
        elif action in BUNDLES:
            for item in source_blocks(action, record["observation"]):
                if item.get("status") == "fresh":
                    self._remember_node(item, handle, record)
        if action != "map":
            return
        parent = result.get("node")
        requested = record["input"].get("node", "repo")
        # A legacy/partial map is not sufficient proof of a containment link.
        if not isinstance(parent, dict) or _id(parent.get("id")) != requested:
            return
        relation = record["input"].get("relation", "contains")
        # A successful live next-page request resolves only its exact predecessor.
        # Archiving a page's detail does not consume its outstanding cursor.
        requested_cursor = record["input"].get("cursor")
        if isinstance(requested_cursor, str):
            for previous, pending in list(self._pending_pages.items()):
                if (pending["node"] == requested and pending["relation"] == relation
                        and pending.get("pending_cursor") == requested_cursor):
                    del self._pending_pages[previous]
        cursor = result.get("nextCursor")
        if isinstance(cursor, str) and cursor:
            pending = {"handle": handle, "node": requested, "relation": relation,
                       "session_id": self.session_id, "revision": record["revision"]}
            if len(cursor.encode()) <= 512:
                pending["pending_cursor"] = cursor
            else:
                pending["pending_cursor_archived"] = True
            self._pending_pages[handle] = {**pending, "paths": _paths(record)}
            while len(self._pending_pages) > 16:
                self._pending_pages.popitem(last=False)
                self._pending_omitted += 1
        if relation != "contains":
            return
        self._remember_node(parent, handle, record)
        for item in _items(result):
            child = _id(item.get("id"))
            if child and child != requested:
                previous = self._parents.get(child)
                if child not in self._parents or (previous and previous[0] == requested):
                    self._parents[child] = (requested, handle)
                else:
                    self._parents[child] = None  # Conflicting observed parents: do not invent a tree.
        if requested == "repo":
            page = {"handle": handle}
            cursor = result.get("nextCursor")
            if isinstance(cursor, str) and cursor:
                page["pending_cursor"] = cursor if len(cursor.encode()) <= 512 else "archived"
            self._root_pages = (self._root_pages + [page])[-8:]
            for item in _items(result):
                child = _id(item.get("id"))
                if child and child in self._known:
                    self._root_entries[child] = deepcopy(self._known[child]["node"])
            while len(self._root_entries) > 32:
                self._root_entries.popitem(last=False)
                self._root_omitted += 1

    def invalidate(self, path: str) -> None:
        super().invalidate(path)
        normalized = _path(path)
        self._changed_paths[normalized] = self._revision
        for handle, value in list(self._selected.items()):
            if normalized in value["paths"]:
                self._stale_selected = (self._stale_selected + [handle])[-8:]
                del self._selected[handle]
        # The archived exact page survives, but stale navigation is not reasoner evidence.
        for item in self._root_entries.values():
            if item.get("path") == normalized:
                item["stale"] = True
        for pending in self._pending_pages.values():
            if normalized in pending["paths"]:
                pending["stale"] = True

    def _payload(self, handle: str, record: dict) -> dict:
        return {"handle": handle, "action": record["action"], "session_id": record["session_id"],
                "revision": record["revision"], "observation": record["observation"]}

    def retention_candidates(self) -> dict:
        handles = list(dict.fromkeys([entry["handle"] for entry in self._ledger if entry["action"] in NAVIGATION]
                                     + list(self._selected)))[-max(8, self.ledger_limit):]
        candidates = []
        for handle in handles:
            record = self._load(handle)
            result = _object(record["observation"])
            invalid = self._invalid_reason(handle, record)
            reason = invalid
            size = len(_json(self._payload(handle, record)).encode())
            if not reason and size + 2 > self.selected_navigation_budget:
                reason = "Observation exceeds selected navigation budget; choose a smaller map or exact source"
            entry = {"handle": handle, "action": record["action"], "selected": handle in self._selected,
                     "eligible": reason is None,
                     "archive_eligible": invalid is None and not self._root(record), "bytes": size,
                     "candidate_ids": [item["id"] for item in _items(result) if _id(item.get("id"))][:8]}
            for key in ("node", "query", "path"):
                if _id(record["input"].get(key)):
                    entry[key] = record["input"][key]
            cursor = result.get("nextCursor")
            if isinstance(cursor, str) and cursor:
                entry["pending_cursor"] = cursor if len(cursor.encode()) <= 512 else "archived"
            if reason:
                entry["ineligible_reason"] = reason
            if self._root(record):
                entry["protected"] = True
                entry["keep_behavior"] = "protected_compact_main_index"
            elif size + 2 > self.selected_navigation_budget:
                entry["keep_behavior"] = "navigation_pointer_only_budget"
            if handle in self._archived_selection:
                entry["explicitly_archived"] = True
            candidates.append(entry)
        focus = [node for node, info in self._known.items()
                 if info["handle"] not in self._replayed
                 and not any(self._changed_paths.get(path, -1) > info["revision"] for path in info["paths"])]
        return {"candidates": candidates, "focus_ids": focus[-32:], "protected": list(PROTECTED),
                "archive": "archive:0", "selected_navigation_budget": self.selected_navigation_budget}

    def apply_retention(self, decision: dict) -> dict:
        """Atomically apply an explicit navigation-only decision; never summarize code."""
        if not isinstance(decision, dict):
            raise TypeError("Retention decision must be an object")
        reason = decision.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason.encode()) > 2000:
            raise ValueError("Retention requires a nonempty reason of at most 2000 bytes")
        groups = []
        records = {}
        for key in ("retain_handles", "archive_handles"):
            values = decision.get(key, [])
            if not isinstance(values, list) or len(values) > 32 or any(not isinstance(value, str) for value in values):
                raise ValueError(f"{key} must be a list of at most 32 observation handles")
            if len(values) != len(set(values)):
                raise ValueError("Duplicate retention handles are not allowed")
            groups.append(values)
            for handle in values:
                try:
                    record = self._load(handle)
                except (OSError, ValueError) as error:
                    raise ValueError(f"Unknown or invalid observation handle: {handle}") from error
                invalid = self._invalid_reason(handle, record)
                if invalid:
                    raise ValueError(invalid)
                records[handle] = record
        retain, archive = groups
        if set(retain) & set(archive):
            raise ValueError("A handle cannot be retained and archived in the same decision")
        if any(self._root(records[handle]) for handle in archive):
            raise ValueError("The compact main index is protected and cannot be archived")
        focus = decision.get("focus_id", self._focus_id)
        if decision.get("focus_id") is not None and focus not in self.retention_candidates()["focus_ids"]:
            raise ValueError("Focus must be a currently observed, live exact ID")
        metadata = decision.get("metadata", {})
        if not isinstance(metadata, dict) or len(_json(metadata).encode()) > 2000:
            raise ValueError("Retention metadata must be an object of at most 2000 bytes")
        selected = OrderedDict((handle, deepcopy(value)) for handle, value in self._selected.items() if handle not in archive)
        pointer_keeps = []
        pointer_reasons = {}
        for handle in retain:
            record = records[handle]
            if self._root(record):
                pointer_keeps.append(handle)
                pointer_reasons[handle] = "protected_compact_main_index"
                continue
            payload = self._payload(handle, record)
            # Keep prior exact selections intact. Newly requested keeps are
            # admitted in caller order; a full budget preserves their pointers
            # without asserting that their branch is irrelevant or archived.
            proposed = [value["payload"] for previous, value in selected.items() if previous != handle] + [payload]
            if len(_json(proposed).encode()) > self.selected_navigation_budget:
                pointer_keeps.append(handle)
                pointer_reasons[handle] = "selected_navigation_budget_full"
                continue
            selected[handle] = {"payload": payload, "paths": _paths(record), "stale": False}
        report = {"reason": reason, "retained_handles": list(selected), "archived_handles": archive,
                  "protected": list(PROTECTED)}
        if pointer_keeps:
            report["pointer_only_handles"] = pointer_keeps
            report["pointer_only_reasons"] = pointer_reasons
        if focus:
            report["focus_id"] = focus
        handle, _ = self._archive("retention", decision, _json(report), None,
                                   {"backend": "harness", "policy": self.policy})
        self._selected = selected
        self._archived_selection.update(archive)
        self._archived_selection.difference_update(retain)
        self._focus_id = focus
        self._selection_count += 1
        report["decision_handle"] = handle
        self._selection_history = (self._selection_history + [{**report, "metadata": deepcopy(metadata)}])[-4:]
        return deepcopy(report)

    def select_for_handoff(self, reason: str, retain_handles: list[str], archive_handles: list[str]) -> dict:
        return self.apply_retention({"reason": reason, "retain_handles": retain_handles,
                                     "archive_handles": archive_handles, "metadata": {"kind": "handoff"}})

    def _breadcrumb(self) -> dict:
        if not self._focus_id:
            return {"ids": []}
        current, branch, seen = self._focus_id, [], set()
        stale = False
        while current and current not in seen and len(branch) < 16:
            seen.add(current)
            branch.append(current)
            info = self._known.get(current)
            if info and any(self._changed_paths.get(path, -1) > info["revision"] for path in info["paths"]):
                stale = True
                break
            parent = self._parents.get(current)
            if not parent or parent[1] in self._replayed:
                break
            current = parent[0]
        return {"ids": list(reversed(branch)), "complete": bool(branch and branch[-1] == "repo" and not stale),
                "stale": stale,
                "basis": "observed_contains_edges_only"}

    def _navigation_view(self, role: str, latest_visible: str | None) -> dict:
        view: dict[str, Any] = {
            "main_index": {"entries": list(self._root_entries.values()), "pages": self._root_pages,
                           "omitted_entries": self._root_omitted, "recovery": "archive:0"},
            "breadcrumb": self._breadcrumb(),
            "selected_observations": [value["payload"] for handle, value in self._selected.items()
                                      if not value["stale"] and handle != latest_visible],
            "selection_history": self._selection_history,
        }
        if self._pending_pages or self._pending_omitted:
            view["pending_pages"] = [{key: value for key, value in pending.items() if key != "paths"}
                                     for pending in self._pending_pages.values()]
            view["pending_pages_omitted"] = self._pending_omitted
        if self._stale_selected:
            view["stale_selected_handles"] = self._stale_selected
        # Defer/unselected is not an archive decision. Both roles retain the
        # bounded frontier pointers, including unresolved pagination.
        view["ledger"] = []
        for entry in self._ledger:
            if entry["handle"] in self._archived_selection:
                pointer = {key: value for key, value in entry.items()
                           if key in ("handle", "action", "session_id", "revision", "pending_cursor", "pending_cursor_archived", "restore")}
                pointer["explicitly_archived"] = True
                view["ledger"].append(pointer)
            else:
                view["ledger"].append(entry)
        if role == "selector":
            view["retention_candidates"] = self.retention_candidates()
        # Exact selected pages are bounded at decision time. All other details are
        # recoverable pointers and may be shortened solely to meet this byte cap.
        view = deepcopy(view)
        def size():
            return len(_json(view).encode())
        removable = []
        if role == "selector":
            removable.extend([view["retention_candidates"]["candidates"],
                              view["retention_candidates"]["focus_ids"]])
        removable.extend([view["selection_history"], view["main_index"]["entries"],
                          view["main_index"]["pages"], view["breadcrumb"]["ids"], view["ledger"]])
        if "pending_pages" in view:
            removable.append(view["pending_pages"])
        removed = 0
        for items in removable:
            while items and size() > self.ledger_budget:
                items.pop(0)
                removed += 1
        if removed:
            view["budget_omitted_pointers"] = removed
        if size() > self.ledger_budget:
            # A large stale-pointer list or the omission marker itself can cross
            # the cap. Exact selected pages must survive this metadata fallback.
            lean = {"main_index": {"recovery": "archive:0"},
                    "selected_observations": view["selected_observations"],
                    "budget_omitted_pointers": removed + len(self._ledger)}
            if len(_json(lean).encode()) <= self.ledger_budget:
                return lean
            # Tiny configured budgets cannot carry even index metadata. Exact
            # selection is disabled below 1024 bytes, so no selected page is lost.
            return {"recovery": "archive:0"} if self.ledger_budget >= 24 else {}
        return view

    def render(self, role: str = "selector") -> list[dict[str, str]]:
        if role not in ("selector", "reasoner"):
            raise ValueError("Render role must be selector or reasoner")
        messages = super().render()
        state = json.loads(messages[0]["content"].split("\n", 1)[1])
        latest = self._latest
        stale_latest = bool(latest and latest["action"] in NAVIGATION and
                            (_object(latest["observation"]).get("status") in ("stale", "deleted")
                             or any(self._changed_paths.get(path, -1) > latest["revision"] for path in _paths(latest))))
        hide_latest = bool(latest and not latest["error"] and latest["action"] in NAVIGATION
                           and (latest["handle"] in self._archived_selection
                                or stale_latest or (role == "reasoner" and latest["replayed"])))
        visible_handle = latest["handle"] if latest and not hide_latest else None
        if hide_latest:
            messages.pop()  # The baseline places the latest raw tool observation last.
        state.pop("navigation_ledger", None)
        archive = state.pop("archive")
        revision = state.pop("revision")
        # Stable prefix: task, exact evidence, hypotheses, edits/tests/errors. The
        # changing archive count/revision are deliberately after the navigation.
        ordered = {"initial_task": state.pop("initial_task")}
        if "current_phase" in state:
            ordered["current_phase"] = state.pop("current_phase")
        ordered["retained_evidence"] = state.pop("retained_evidence")
        ordered.update(state)
        ordered["navigation"] = self._navigation_view(role, visible_handle)
        ordered["policy"] = self.policy
        ordered["role"] = role
        ordered["archive"] = archive
        ordered["revision"] = revision
        ordered["selection_count"] = self._selection_count
        messages[0]["content"] = ("Deterministic selected working state. retained_evidence contains exact current-session "
                                    "source already read live; known edits invalidate affected source. Navigation choices "
                                    "are explicit model decisions, not evidence that other branches are irrelevant. "
                                    "Historical/restored NAVIGATION requires live verification; resident source does not "
                                    "need another read solely because it is retained. Restore archive:0 to recover pointers.\n"
                                    + json.dumps(ordered, ensure_ascii=False, separators=(",", ":")))
        if hide_latest:
            messages.append({"role": "user", "content": "Continue from selected navigation and exact source evidence. Request more evidence if insufficient."})
        return messages

    def context_stats(self) -> dict[str, int]:
        stats = super().context_stats()
        stats.update(selected_count=len(self._selected), selection_count=self._selection_count,
                     selected_navigation_bytes=len(_json([value["payload"] for value in self._selected.values()]).encode()),
                     selected_navigation_budget=self.selected_navigation_budget,
                     reasoner_rendered_bytes=len(_json(self.render(role="reasoner")).encode()))
        return stats


# Compatibility name for the earlier deterministic branch replay experiment.
BranchWorkingState = SelectedWorkingState
