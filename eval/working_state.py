"""Deterministic, bounded model context with an immutable observation archive.

The model sees active task instructions, a small navigation ledger, exact
resident evidence, and one recent decision/observation. Nothing here asks a
model to summarize history. Archive restoration is navigation-only: evicted
source must be read live again before it can authorize a conditional read.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

from source_bundles import source_blocks


HANDLE = re.compile(r"obs-[0-9a-f]{16}-[0-9]{8}-[0-9a-f]{16}\Z")
NAVIGATION = frozenset(("map", "search", "grep"))
SOURCE_MODES = frozenset(("full", "auto", "preview"))
BUNDLES = frozenset(("inspect", "hydrate", "batch_read"))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _without_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    return value


def _excerpt(text: str, limit: int) -> dict[str, Any]:
    raw = text.encode()
    return {"text": raw[:limit].decode("utf-8", errors="ignore"), "truncated": len(raw) > limit}


def _path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return str(path)


def _object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


class WorkingState:
    def __init__(self, archive_dir: Path, session_id: str, *, evidence_bytes: int = 16 * 1024,
                 ledger_entries: int = 8, ledger_bytes: int = 8 * 1024) -> None:
        if any(type(value) is not int or value < 0 for value in (evidence_bytes, ledger_entries, ledger_bytes)):
            raise ValueError("Context limits must be nonnegative integers")
        self.archive_dir = Path(archive_dir).resolve()
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_budget = evidence_bytes
        self.ledger_limit = ledger_entries
        self.ledger_budget = ledger_bytes
        self._sequence = max((int(path.name.split("-")[2]) for path in self.archive_dir.glob("obs-*.json")
                              if HANDLE.fullmatch(path.stem)), default=0)
        self._archive_count = sum(HANDLE.fullmatch(path.stem) is not None
                                  for path in self.archive_dir.glob("obs-*.json"))
        self._archive_bytes = sum(path.stat().st_size for path in self.archive_dir.glob("obs-*.json")
                                  if HANDLE.fullmatch(path.stem) and not path.is_symlink())
        self.reset(session_id)

    def reset(self, session_id: str, task_message: str = "") -> None:
        """Start a fresh model context; historical observations remain on disk."""
        if not isinstance(session_id, str) or not session_id or len(session_id.encode()) > 512:
            raise ValueError("A nonempty session ID of at most 512 bytes is required")
        self.session_id = session_id
        self._revision = 0
        self._phase = 0
        self._initial_task = ""
        self._current_phase = ""
        self._assistant: str | None = None
        self._hypotheses: list[dict[str, Any]] = []
        self._evidence: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._ledger: list[dict[str, Any]] = []
        self._latest: dict[str, Any] | None = None
        self._last_error: dict[str, Any] | None = None
        self._last_test: dict[str, Any] | None = None
        self._edits: list[dict[str, Any]] = []
        self._edit_count = 0
        if task_message:
            self.task(task_message)

    def task(self, message: str) -> None:
        """Keep the initial constraints and replace only the current phase."""
        if not isinstance(message, str):
            raise TypeError("Task messages must be strings")
        if not self._initial_task:
            self._initial_task = message
        elif self._current_phase != message:
            self._phase += 1
        self._current_phase = message

    def assistant(self, decision: dict[str, Any] | str) -> None:
        """Consume the latest observation after a model decision, not on render."""
        self._latest = None
        if isinstance(decision, dict):
            canonical = _without_nulls(deepcopy(decision))
            self._assistant = _json(canonical)
            answer = canonical.get("answer")
            if canonical.get("action") == "answer" and isinstance(answer, str) and answer:
                self._hypotheses.append({"phase": self._phase, "revision": self._revision,
                                         "model_authored_hypothesis": True, **_excerpt(answer, 2000)})
                self._hypotheses = self._hypotheses[-2:]
        elif isinstance(decision, str):
            self._assistant = decision
        else:
            raise TypeError("Assistant output must be a decision object or text")

    def _archive(self, action: str, inputs: dict[str, Any], observation: str,
                 error: str | None, route: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        self._sequence += 1
        record = {"format": "codemesh-observation-v1", "session_id": self.session_id,
                  "revision": self._revision, "sequence": self._sequence, "action": action,
                  "input": deepcopy(inputs), "observation": observation, "error": error,
                  "route": deepcopy(route)}
        data = _json(record).encode()
        session_key = sha256(self.session_id.encode()).hexdigest()[:16]
        handle = f"obs-{session_key}-{self._sequence:08d}-{sha256(data).hexdigest()[:16]}"
        path = self.archive_dir / (handle + ".json")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        self._archive_count += 1
        self._archive_bytes += len(data)
        return handle, record

    def _load(self, handle: str) -> dict[str, Any]:
        if not isinstance(handle, str) or HANDLE.fullmatch(handle) is None:
            raise ValueError("Invalid observation handle")
        path = self.archive_dir / (handle + ".json")
        if path.is_symlink() or path.resolve().parent != self.archive_dir:
            raise ValueError("Observation handle must refer to an archive file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            data = source.read()
        if sha256(data).hexdigest()[:16] != handle.rsplit("-", 1)[1]:
            raise ValueError("Observation archive integrity check failed")
        record = json.loads(data)
        if record.get("format") != "codemesh-observation-v1":
            raise ValueError("Unsupported observation archive format")
        return record

    def invalidate(self, path: str) -> None:
        """An edit invalidates all retained views of that file, including aliases."""
        normalized = _path(path)
        if normalized is None:
            raise ValueError("Invalid repository-relative path")
        self._revision += 1
        for key, evidence in list(self._evidence.items()):
            if evidence["path"] == normalized:
                del self._evidence[key]
        for entry in self._ledger:
            if (normalized in entry.get("paths", []) or entry.get("path") == normalized
                    or any(candidate == f"file:{normalized}" or candidate.startswith(f"symbol:{normalized}:")
                           for candidate in entry.get("candidate_ids", []))):
                entry["stale"] = True
        # A pending raw source is also evidence; never send it after an edit.
        if self._latest and self._latest["action"] in ("source", "read"):
            result = _object(self._latest["observation"])
            latest_path = _path(result.get("path") or result.get("filePath") or self._latest["input"].get("path"))
            if latest_path == normalized:
                self._latest = None
        elif self._latest and self._latest["action"] in BUNDLES:
            if any(block.get("path") == normalized for block in source_blocks(
                    self._latest["action"], self._latest["observation"])):
                self._latest = None

    def _evidence_size(self) -> int:
        return sum(entry["bytes"] for entry in self._evidence.values())

    def _retain(self, handle: str, record: dict[str, Any]) -> None:
        action, inputs = record["action"], record["input"]
        result = _object(record["observation"])
        hashes: set[tuple[str, str, str]] = set()
        if action in BUNDLES:
            failed_ids = set()
            failed_paths = set()
            for item in result.get("references", []):
                if not isinstance(item, dict) or item.get("status") not in ("stale", "changed", "missing"):
                    continue
                failed_ids.update(value for value in (item.get("id"), item.get("requestedId")) if isinstance(value, str))
                saved = inputs.get("references", [])
                index = item.get("index")
                if isinstance(saved, list) and type(index) is int and 0 <= index < len(saved) and isinstance(saved[index], dict):
                    if isinstance(saved[index].get("id"), str):
                        failed_ids.add(saved[index]["id"])
                    if _path(saved[index].get("path")):
                        failed_paths.add(_path(saved[index]["path"]))
            for call in result.get("calls", []):
                if isinstance(call, dict) and call.get("status") in ("stale", "deleted") and isinstance(call.get("targetId"), str):
                    failed_ids.add(call["targetId"])
            if result.get("status") in ("stale", "deleted") and isinstance(result.get("rootId"), str):
                failed_ids.add(result["rootId"])
            for evidence in self._evidence.values():
                if any(node in failed_ids for node, _, _ in evidence["hashes"]):
                    failed_paths.add(evidence["path"])
            for path in failed_paths:
                self.invalidate(path)
            for block in source_blocks(action, record["observation"]):
                child_action = "source" if str(block.get("id", "")).startswith("symbol:") else "read_span"
                # Do not grant the bundle root's alias to every callee.
                self._retain(handle, {**record, "action": child_action, "input": {},
                                      "observation": _json(block)})
            return
        if action == "source":
            path = _path(result.get("path") or result.get("filePath"))
            node, mode = result.get("id"), result.get("mode")
            source_hash = result.get("hash") or result.get("sourceHash")
            source = result.get("source")
            if result.get("status") in ("stale", "deleted") and path:
                self.invalidate(path)
                return
            key = f"source:{node}:{mode}"
            if result.get("status") == "unchanged":
                prior = self._evidence.get(key)
                if prior and (node, mode, source_hash) in prior["hashes"]:
                    self._evidence.move_to_end(key)
                return
            if (result.get("status") != "fresh" or not path or not isinstance(node, str)
                    or not node.startswith("symbol:") or not isinstance(mode, str) or mode not in SOURCE_MODES
                    or not isinstance(source, str) or sha256(source.encode()).hexdigest() != source_hash):
                return
            prior = self._evidence.get(key)
            if prior and (node, mode, source_hash) not in prior["hashes"]:
                self.invalidate(path)
            hashes.add((node, mode, source_hash))
            requested = inputs.get("node") or inputs.get("id")
            if isinstance(requested, str) and requested:
                hashes.add((requested, mode, source_hash))
            if prior and (node, mode, source_hash) in prior["hashes"]:
                hashes.update(prior["hashes"])
        elif action == "read":
            path = _path(inputs.get("path"))
            if not path:
                return
            key = f"read:{path}:{inputs.get('start') or 1}:{inputs.get('limit') or 60}"
        elif action == "read_span":
            path = _path(result.get("path"))
            source, lines = result.get("source"), result.get("lines")
            if (result.get("status") != "fresh" or not path or not isinstance(source, str)
                    or sha256(source.encode()).hexdigest() != result.get("hash")
                    or not isinstance(lines, list) or len(lines) != 2
                    or any(type(line) is not int for line in lines) or not 1 <= lines[0] <= lines[1]):
                return
            key = f"span:{path}:{lines[0]}:{lines[1]}"
        else:
            return
        rendered = {"handle": handle, "action": action, "path": path,
                    "observation": record["observation"]}
        size = len(_json(rendered).encode())
        self._evidence.pop(key, None)
        if size > self.evidence_budget:
            return  # Latest raw observation still gets delivered once.
        self._evidence[key] = {"rendered": rendered, "path": path, "hashes": hashes,
                               "bytes": size, "handle": handle}
        while self._evidence_size() > self.evidence_budget:
            self._evidence.popitem(last=False)

    def _navigation(self, handle: str, record: dict[str, Any], *, replayed: bool = False) -> None:
        action, inputs = record["action"], record["input"]
        if action not in NAVIGATION and action not in ("source", "read") and action not in BUNDLES:
            return
        result = _object(record["observation"])
        entry: dict[str, Any] = {"handle": handle, "action": action, "session_id": record["session_id"],
                                 "revision": record["revision"]}
        for key in ("query", "node", "path", "start", "limit", "relation"):
            if inputs.get(key) is not None:
                value = inputs[key]
                if not isinstance(value, str) or len(value.encode()) <= 512:
                    entry[key] = value
        candidates = result.get("results", result.get("entries", result.get("sources", [])))
        if isinstance(candidates, list):
            ids = [item["id"] for item in candidates if isinstance(item, dict) and isinstance(item.get("id"), str)]
            if ids:
                entry["candidate_ids"] = [node for node in ids[:8] if len(node.encode()) <= 512]
                entry["candidate_count"] = len(ids)
        cursor = result.get("nextCursor")
        if isinstance(cursor, str) and cursor:
            if len(cursor.encode()) <= 1024:
                entry["pending_cursor"] = cursor
            else:
                entry["pending_cursor_archived"] = True
        if action == "grep":
            paths = list(dict.fromkeys(match.group(1) for match in re.finditer(r"^(?:\./)?([^\n:]+):\d+:", record["observation"], re.MULTILINE)))
            if paths:
                entry["paths"] = [path for path in paths[:8] if len(path.encode()) <= 512]
        if action == "source":
            for key in ("id", "path", "lines", "status"):
                if key in result and (not isinstance(result[key], str) or len(result[key].encode()) <= 512):
                    entry[key] = result[key]
            entry["restore"] = "live_source_required"
        elif action == "read" or action in BUNDLES:
            entry["restore"] = "live_read_required"
            if action in BUNDLES:
                entry["paths"] = list(dict.fromkeys(block["path"] for block in source_blocks(action, record["observation"])
                                                     if _path(block.get("path"))))[:8]
        else:
            entry["restore"] = "navigation_only"
        if record["error"]:
            entry["failed"] = True
        if replayed:
            entry["replayed"] = True
            entry["stale"] = record["session_id"] != self.session_id or record["revision"] != self._revision
        self._ledger = [previous for previous in self._ledger if previous["handle"] != handle]
        self._ledger.append(entry)
        while self._ledger and (len(self._ledger) > self.ledger_limit or len(_json(self._ledger).encode()) > self.ledger_budget):
            self._ledger.pop(0)

    def observe(self, action: str, input: dict[str, Any], observation: str,
                error: str | None = None, route: dict[str, Any] | None = None) -> str:
        """Archive a complete observation, then update deterministic active state."""
        if not isinstance(action, str) or not isinstance(input, dict) or not isinstance(observation, str):
            raise TypeError("Observation requires action, input object, and text")
        route = route or {}
        if action == "edit" and error is None:
            path = _path(input.get("path"))
            if path:
                self.invalidate(path)
        handle, record = self._archive(action, input, observation, error, route)
        if error is None:
            self._retain(handle, record)
            if self._last_error and self._last_error["action"] == action:
                self._last_error = None
        else:
            self._last_error = {"handle": handle, "action": action, "revision": self._revision,
                                **_excerpt(observation, 4096)}
        if action == "test":
            self._last_test = {"handle": handle, "action": action, "revision": self._revision,
                               "passed": route.get("passed"), **_excerpt(observation, 8192)}
        if action == "edit" and error is None:
            self._edit_count += 1
            edit: dict[str, Any] = {"handle": handle, "path": input.get("path"), "revision": self._revision}
            old, new = input.get("old"), input.get("new")
            if isinstance(old, str) and isinstance(new, str):
                if len(old.encode()) + len(new.encode()) <= 4096:
                    edit.update(old=old, new=new, truncated=False)
                else:
                    edit.update(old_excerpt=_excerpt(old, 1024), new_excerpt=_excerpt(new, 1024),
                                old_hash=sha256(old.encode()).hexdigest(), new_hash=sha256(new.encode()).hexdigest(),
                                truncated=True)
            self._edits.append(edit)
            self._edits = self._edits[-4:]
        self._navigation(handle, record)
        self._latest = {"handle": handle, **record, "replayed": False}
        return handle

    def restore(self, handle: str) -> str:
        """Redisplay archived navigation with provenance; source needs a live read."""
        if isinstance(handle, str) and re.fullmatch(r"archive:(0|[1-9][0-9]{0,12})", handle):
            offset = int(handle.split(":", 1)[1])
            paths = sorted((path for path in self.archive_dir.glob("obs-*.json") if HANDLE.fullmatch(path.stem)),
                           key=lambda path: int(path.stem.split("-")[2]))
            entries = []
            for path in paths[offset:offset + 8]:
                record = self._load(path.stem)
                result = _object(record["observation"])
                entry = {"handle": path.stem, "action": record["action"], "session_id": record["session_id"],
                         "revision": record["revision"], "failed": record["error"] is not None,
                         "restore": "navigation_only" if record["action"] in NAVIGATION else "live_action_required"}
                for key in ("query", "node", "path"):
                    value = record["input"].get(key)
                    if isinstance(value, str) and len(value.encode()) <= 512:
                        entry[key] = value
                if record["action"] == "source":
                    entry["restore"] = "live_read_required"
                    for key in ("id", "path"):
                        value = result.get(key)
                        if isinstance(value, str) and len(value.encode()) <= 512:
                            entry[key] = value
                elif record["action"] == "read" or record["action"] in BUNDLES:
                    entry["restore"] = "live_read_required"
                if record["action"] in NAVIGATION:
                    candidates = result.get("results", result.get("entries"))
                    entry["empty_result"] = candidates == [] or record["observation"].strip() == "No matches"
                    if isinstance(result.get("nextCursor"), str) and result["nextCursor"]:
                        entry["pending_branch"] = True
                if len(_json(entries + [entry]).encode()) > 7 * 1024:
                    break
                entries.append(entry)
            output: dict[str, Any] = {"entries": entries, "total": len(paths), "offset": offset,
                                      "historical_navigation_only": True}
            if offset + len(entries) < len(paths):
                output["next"] = f"archive:{offset + len(entries)}"
            observation = _json(output)
            self._latest = {"handle": handle, "action": "archive_index", "input": {"handle": handle},
                            "observation": observation, "session_id": self.session_id, "revision": self._revision,
                            "error": None, "replayed": True}
            return observation
        record = self._load(handle)
        if record["action"] not in NAVIGATION:
            raise ValueError("Only map/search/grep observations can be restored; source/read require a live read")
        self._navigation(handle, record, replayed=True)
        self._latest = {"handle": handle, **record, "replayed": True}
        return record["observation"]

    def resident_hashes(self) -> set[tuple[str, str, str]]:
        return set().union(*(entry["hashes"] for entry in self._evidence.values())) if self._evidence else set()

    def observation_resident(self, action: str, input: dict[str, Any], observation: str) -> bool:
        """Whether newly delivered bytes were already present, before observe()."""
        if action == "source":
            result = _object(observation)
            node, mode = result.get("id"), result.get("mode")
            source_hash, source = result.get("hash") or result.get("sourceHash"), result.get("source")
            if (result.get("status") != "fresh" or not isinstance(node, str) or not isinstance(mode, str)
                    or mode not in SOURCE_MODES or not isinstance(source, str)
                    or sha256(source.encode()).hexdigest() != source_hash):
                return False
            return (node, mode, source_hash) in self.resident_hashes()
        if action == "read":
            path = _path(input.get("path"))
            key = f"read:{path}:{input.get('start') or 1}:{input.get('limit') or 60}"
            previous = self._evidence.get(key)
            return previous is not None and previous["rendered"]["observation"] == observation
        if action == "read_span":
            result = _object(observation)
            lines = result.get("lines")
            if not isinstance(lines, list) or len(lines) != 2:
                return False
            previous = self._evidence.get(f"span:{_path(result.get('path'))}:{lines[0]}:{lines[1]}")
            return previous is not None and previous["rendered"]["observation"] == _json(result)
        return False

    def render(self) -> list[dict[str, str]]:
        """Build a repeatable prompt; only assistant() consumes the latest result."""
        latest_handle = self._latest["handle"] if self._latest else None
        state: dict[str, Any] = {"initial_task": self._initial_task, "revision": self._revision,
                                 "current_phase": self._current_phase,
                                 "archive": {"root": "archive:0", "observations": self._archive_count,
                                             "restoration": "historical_navigation_only"},
                                 "navigation_ledger": self._ledger,
                                 "retained_evidence": [entry["rendered"] for entry in self._evidence.values()
                                                       if entry["handle"] != latest_handle]}
        if self._current_phase == self._initial_task:
            state.pop("current_phase")
        if self._hypotheses:
            state["prior_model_hypotheses"] = self._hypotheses
        if self._edits:
            state["successful_edits"] = {"total": self._edit_count, "omitted_older": self._edit_count - len(self._edits),
                                          "recent": self._edits}
        if self._last_error and self._last_error["handle"] != latest_handle:
            state["latest_tool_error"] = self._last_error
        if self._last_test and self._last_test["handle"] != latest_handle:
            state["latest_test"] = {**self._last_test, "current_revision": self._last_test["revision"] == self._revision}
        content = ("Deterministic harness working state. Retained evidence preserves tool output exactly. "
                   "Model-authored hypotheses require source verification. Ledger entries are navigation pointers; "
                   "restore(handle) recovers historical map/search/grep output only. Evicted source needs a live read.\n"
                   + _json(state))
        messages = [{"role": "user", "content": content}]
        if self._assistant is not None:
            messages.append({"role": "assistant", "content": self._assistant})
        if self._latest:
            provenance = {key: self._latest[key] for key in ("handle", "action", "session_id", "revision", "replayed")}
            provenance["error"] = self._latest["error"]
            if self._latest["replayed"]:
                provenance["historical_navigation_only"] = True
            latest = "Latest tool observation " + _json(provenance) + ":\n" + self._latest["observation"]
            messages.append({"role": "user", "content": latest + "\nChoose the next harness action or answer from observed evidence."})
        elif self._assistant is not None:
            messages.append({"role": "user", "content": "Continue the current phase using retained evidence and the navigation ledger."})
        return messages

    def context_stats(self) -> dict[str, int]:
        return {"rendered_bytes": len(_json(self.render()).encode()), "archive_count": self._archive_count,
                "archive_bytes": self._archive_bytes, "evidence_count": len(self._evidence),
                "evidence_bytes": self._evidence_size(), "evidence_budget": self.evidence_budget,
                "ledger_count": len(self._ledger), "ledger_bytes": len(_json(self._ledger).encode()),
                "resident_hash_count": len(self.resident_hashes())}
