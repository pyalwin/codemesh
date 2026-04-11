---
name: codemesh
description: Query the code knowledge graph before reading code. Use the 3-phase workflow (Map → Trace → Verify) for complete, accurate code exploration.
---

# Codemesh: Code Knowledge Graph

You have access to a persistent code knowledge graph via `codemesh_*` MCP tools. The graph contains structural data (files, symbols, imports, call chains) and semantic data (agent-written summaries, workflow paths) that accumulate across sessions.

## Tools

### codemesh_explore (primary)

Omni-tool with 3 actions:
- `action='search'` — find files and symbols by text query
  - `codemesh_explore({ action: "search", query: "validation pipeline" })`
- `action='context'` — get symbols, edges, and relationships for a file or symbol
  - `codemesh_explore({ action: "context", path: "src/auth.py", symbol: "login" })`
- `action='impact'` — find reverse dependencies (what would break if you change this?)
  - `codemesh_explore({ action: "impact", path: "src/models.py" })`

Every response includes `projectRoot` — the absolute path to the project root. Use it for Read calls.

### codemesh_trace

Follows a call chain from a symbol through the graph to leaf nodes. Returns every function in the path with source code. Use this to trace execution flows to completion.
- `codemesh_trace({ symbol: "Session.request", depth: 5 })`
- Supports fuzzy matching — `Session.request`, `request`, or `perform` all work.

### codemesh_enrich

Write back what you learned about a file or symbol. Saves a summary for future sessions.
- `codemesh_enrich({ path: "src/auth.py", summary: "Handles JWT validation and refresh...", sessionId: "session-1" })`

### codemesh_workflow

Record a multi-file workflow path you traced.
- `codemesh_workflow({ name: "login flow", description: "...", files: ["src/routes.py", "src/auth.py", "src/models.py"] })`

## 3-Phase Workflow

**Always follow this workflow for code exploration tasks.**

### Phase 1 — MAP

Find the relevant code and understand the structure.

1. `codemesh_explore({ action: "search", query: "..." })` to find entry points
2. `codemesh_explore({ action: "context", path: "..." })` on each key file to see symbols and relationships

### Phase 2 — TRACE

Follow execution flows to completion. Do NOT stop mid-chain.

3. `codemesh_trace({ symbol: "...", depth: 5 })` to follow call chains from entry points
4. If the trace doesn't reach the final destination (the system call, the delegate callback, the leaf function), trace again from the LAST symbol in the chain, or Read the file to find where it continues
5. Keep going until you've reached the END of the flow — not "most of it", ALL of it

### Phase 3 — VERIFY

Before writing your answer, check completeness:

6. Have I identified EVERY file involved in this flow?
7. Have I traced from the entry point ALL THE WAY to the final system-level call or leaf node?
8. Are there any gaps in the middle of my trace where I assumed a connection without verifying it?

**If the answer to any of these is "no", go back to Phase 2 and keep tracing.** Your answer must cover the complete flow from start to finish with no gaps.

### After exploration — ENRICH

9. `codemesh_enrich(...)` to save what you learned for future sessions
10. `codemesh_workflow(...)` to record the multi-file path you traced

## When to fall back to Grep/Read

- `codemesh_explore(action='search')` returns nothing relevant (new or unindexed area)
- Graph data is marked stale and you need fresh code
- Line-level debugging where summaries aren't sufficient
- The trace ends at a symbol boundary and you need to see what's inside

## When to skip codemesh

- Simple one-file edits where you already know the exact file
- User explicitly asks to read a specific file
- The file was just created and hasn't been indexed yet

## Key principle

Codemesh replaces the **discovery phase**, not code reading. Instead of 10+ exploratory greps to figure out what's relevant, one search tells you where to look. Then trace tells you the full execution path. Then you read only the specific files that matter, with full context of how they connect.
