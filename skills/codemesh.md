---
name: codemesh
description: Query the code knowledge graph before reading code. Use structured workflows for complete, accurate code exploration — decompose first, then trace or explore breadth-first depending on question type.
---

# Codemesh: Code Knowledge Graph

You have access to a persistent code knowledge graph via `codemesh_*` MCP tools, plus LSP for exact navigation. Use them together as a two-tier system.

## Two-Tier Navigation

### Tier 1 — Codemesh (global discovery: "where is everything?")

**codemesh_explore** — omni-tool with 3 actions:
- `action='search'` — find files and symbols across the entire codebase
- `action='context'` — get symbol metadata: signatures, call chains (full depth-5 paths), imports, concepts, workflows
- `action='impact'` — find reverse dependencies

**codemesh_trace** — follow a call chain from a symbol to leaf nodes with source code. Supports fuzzy matching.

**codemesh_enrich / codemesh_workflow** — write back what you learned for future sessions.

### Tier 2 — LSP (exact navigation: "what exactly is this?")

Use LSP when codemesh gives you a symbol but you need surgical precision:
- **Go to definition** — when you know a function name but need its exact file and line
- **Find references** — see every caller of a specific function across the codebase
- **Resolve ambiguity** — if codemesh returns 5 `save()` methods, LSP tells you which one a specific call resolves to

### How they work together

Codemesh gives you the **map** — the big picture of what exists, how files connect, what workflows have been traced before. LSP gives you the **GPS** — exact navigation within the territory codemesh mapped.

```
codemesh_explore(search) → "auth logic is in src/auth/, src/middleware/, src/models/user.ts"
LSP(go-to-definition) → "this specific validateToken() call resolves to src/auth/jwt.ts:45"
Read(src/auth/jwt.ts, lines 45-80) → the actual code you need
```

---

## Before You Explore: DECOMPOSE

**This step is mandatory for every exploration task.**

Before making any tool calls, break the question into sub-topics. Write them out explicitly.

Example — "How does collaborative editing work?":
```
Sub-topics I need to cover:
1. Transport layer — how are messages sent? (WebSocket, HTTP polling?)
2. State synchronization — how are changes shared between clients?
3. Conflict resolution — what happens with concurrent edits?
4. Session management — joining, leaving, reconnecting
5. Data model — what is the shared state structure?
```

Example — "Trace request flow from Session.request() to URLSession":
```
Sub-topics I need to cover:
1. Entry point — Session.request() public API
2. Request construction — how is the URLRequest built?
3. Task creation — how is URLSessionTask created?
4. Delegation — how does URLSession report results back?
5. Response handling — how does the response get back to the caller?
```

**This list is your checklist. You are not done until every sub-topic is covered.**

---

## Detect Question Type, Then Follow the Right Workflow

### Type A: TRACE questions

Questions that ask you to follow a specific execution path from A to B.

Signals: "trace", "flow", "how does X call Y", "what happens when", "step by step"

**Workflow: Decompose → Search → Trace → Verify**

1. **DECOMPOSE** — list the steps you expect in the path
2. **SEARCH** — `codemesh_explore(action='search')` to find the entry point
3. **TRACE** — `codemesh_trace(symbol, depth=5)` to follow the call chain
4. If trace doesn't reach the end, trace again from the last symbol, or Read the file
5. **Keep tracing** until you reach the leaf node (the system call, the final callback)
6. **VERIFY** — check your decomposition list. Did you cover every step? If not, go back.

### Type B: COMPREHENSION questions

Questions that ask you to understand how a feature/system/module works.

Signals: "how does X work", "explain the architecture", "what are the key components"

**Workflow: Decompose → Broad Search → Deep Dive Each → Cross-Reference → Verify**

1. **DECOMPOSE** — list all aspects/sub-systems the answer needs to cover
2. **BROAD SEARCH** — search for EACH sub-topic separately:
   ```
   codemesh_explore(action='search', query='collab transport websocket')
   codemesh_explore(action='search', query='conflict resolution reconcile')
   codemesh_explore(action='search', query='session management portal')
   ```
   Do NOT stop at one search. Search for every sub-topic in your decomposition.
3. **DEEP DIVE** — for each relevant file found, get context:
   ```
   codemesh_explore(action='context', path='src/collab/Portal.tsx')
   ```
4. **CROSS-REFERENCE** — check how the modules connect. Use `action='impact'` or `action='context'` to see imports/dependencies between the files you found.
5. **VERIFY** — go through your decomposition checklist:
   - [ ] Transport layer — covered? Which files?
   - [ ] Conflict resolution — covered? Which mechanism?
   - [ ] Session management — covered? Which files?
   
   **If any sub-topic is unchecked, search for it specifically and explore those files.** Do NOT write your answer until every sub-topic has at least one file/mechanism identified.

---

## Phase: VERIFY & ENRICH (applies to both types)

Before writing your answer, do BOTH of these:

### Verify completeness

1. **Decomposition coverage** — is every sub-topic from your initial decomposition covered?
2. **File coverage** — have you identified every key file involved?
3. **Completeness** — for traces: did you reach the leaf node? For comprehension: did you cover all aspects?
4. **Gaps** — are there any connections you assumed without verifying?

**If any check fails, go back and explore more.**

### Enrich the graph (MANDATORY)

**This is not optional.** For each key file you explored, write back what you learned:

```
codemesh_enrich({
  path: "src/collab/Portal.tsx",
  summary: "WebSocket connection manager for real-time collab. Handles room join/leave, broadcasts scene updates via Socket.IO. Reconnects on disconnect.",
  sessionId: "session-1"
})
```

For multi-file flows you traced, record the workflow:

```
codemesh_workflow({
  name: "collaborative editing sync",
  description: "Real-time sync flow from user action to other clients",
  files: ["src/collab/Portal.tsx", "src/collab/reconciliation.ts", "src/scene/Scene.ts"]
})
```

**Why this matters:** Your enrichments make the NEXT session faster. The graph starts with structural data only — your summaries add the semantic understanding that helps future agents skip the exploration you just did.

---

## Writing Your Answer

Structure your response with:

1. **Overview** — 2-3 sentence summary
2. **One section per sub-topic** from your decomposition — each with file names and key functions/classes
3. **File reference table** — list EVERY file involved and its role

Your answer must be **COMPLETE**. Cover every sub-topic from your decomposition fully. Do not abbreviate, truncate, or skip sections.

---

## When to Fall Back to Grep/Read

- `codemesh_explore(action='search')` returns nothing relevant
- Graph data is stale
- Line-level debugging
- Trace ends at a boundary and you need to see what's inside

## When to Skip Codemesh

- Simple one-file edits where you know the file
- User explicitly asks to read a specific file
- File was just created and hasn't been indexed
