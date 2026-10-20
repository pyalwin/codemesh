---
name: codemesh
description: Navigate the persistent code graph one level at a time, follow exact IDs, and read only the source needed for the task.
---

# Codemesh: navigate before reading

Codemesh is a persistent index of files, symbols, and relationships. Use it as
a map of the current repository. Keep your own memory of the task and use the
index to recover code facts after a long session or compaction.

## Working loop

1. For an unfamiliar area, call `codemesh_map({})`. It returns one level of
   directories and files with stable IDs. If you already know an ID, start
   there with `codemesh_map({ node: id })`.
2. Follow the branch that fits the task. Repeated `codemesh_map({ node: id })`
   calls expand a directory, file, or symbol one level at a time. Use `limit`
   and `cursor` when the result is paginated. If the task names a symbol or
   file directly, `codemesh_explore({ action: "search", query: name })` can
   locate its ID before you continue with exact-ID navigation.
3. From a symbol, request one relationship at a time when useful:
   `codemesh_map({ node: id, relation: "calls" })` or
   `codemesh_map({ node: id, relation: "called_by" })`.
4. Read exact code only for selected symbols with
   `codemesh_source({ id: "symbol:...", mode: "full" })`. Use `fold` or
   `preview` while navigating. A response marked `stale` requires
   `codemesh index` before trusting its line range. Inspect related source when a
   static edge is uncertain or the task needs precise behavior.
5. Edit and test with your usual tools. An index is a navigation aid; source
   and test results remain the evidence for a change.
6. Save a useful explanation with `codemesh_enrich`, and retrieve prior notes
   with `codemesh_recall`. Treat a `stale` note as a clue to verify in source.

If you still have a previously delivered source view in the active context,
pass its `sourceHash` back as `ifHash` to receive an `unchanged` reference.
After compaction or in a new session, omit `ifHash` or set `force: true` to
rehydrate the exact source. Do not assume a delivery from an earlier session
is still visible to the model.

`codemesh_answer({ question })` remains available as a broad shortcut. It
assembles ranked context and may include source snippets. For deliberate
navigation, prefer `codemesh_map`; choose the path and depth yourself.

## What the map contains

- `repo` → immediate directories and files.
- `dir:<path>` → immediate directories and files below that path.
- `file:<path>` → top-level symbols in the file.
- `symbol:<path>:<qualifiedName>` → immediately nested symbols, if any.
- `relation` → one hop along calls, callers, imports, or other indexed edges.

Every returned entry has an ID you can use in the next call. Results are
bounded and paginated; expand only branches relevant to the task. Static call
links can be incomplete or ambiguous in dynamic code, so confirm behavior in
source when it matters.

## CLI mode

If MCP tools are unavailable and `codemesh` is on PATH:

```bash
codemesh explore map
codemesh explore map --node 'dir:src/tools'
codemesh explore map --node 'symbol:src/server.ts:createServer' --relation calls
codemesh explore source 'symbol:src/server.ts:createServer'
codemesh explore source 'symbol:src/server.ts:createServer' --mode preview
codemesh explore recall --path src/server.ts
codemesh remember --path src/server.ts --summary 'Server tool registration' --session task-id
```

These commands return JSON. `CODEMESH_PROJECT_ROOT` selects the repository.

## Notes and fallbacks

Use `codemesh_enrich` for a non-obvious observation that another session may
benefit from. Keep it separate from facts extracted directly from code. The
graph can be behind newly edited files until re-indexed; source responses
report this explicitly. Search and grep remain useful when a symbol is not
indexed, for dynamic references, and for exact text questions.
