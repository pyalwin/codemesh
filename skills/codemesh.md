---
name: codemesh
description: Query the code knowledge graph before reading code. One-call context assembly via codemesh_answer returns inline source snippets; default tool habits (Read after search) waste the savings. Follow the rules below.
---

# Codemesh: three rules that override default tool habits

Codemesh returns a knowledge graph with inline source snippets. Treating it like a search index and Reading files after every query is the main way to lose the 30-60% time savings it provides — the snippets already contain the code you'd otherwise Read.

1. **Start with `codemesh_answer` for any code question.** Not explore, not trace, not Grep or Read.

2. **When `suggestedReads[i].truncated: false`, the snippet contains the full symbol body.** Cite `file:lineStart-lineEnd` from the snippet and answer. Read is for the `truncated: true` case (use a targeted `offset`/`limit`), or when you need imports/types not in the snippet.

3. **Each follow-up tool call needs a specific gap to fill.** "Verifying" a complete snippet by reading the file again isn't a gap. Broad thoroughness isn't a gap. If you can't name what's missing from the `codemesh_answer` response, you don't need another tool.

---

# How it works

Codemesh runs in two modes — detect which is available:

- **MCP mode** — tools like `codemesh_answer`, `codemesh_explore`, etc. are callable directly.
- **CLI mode** — no MCP tools, but `codemesh` is on PATH:
  ```bash
  codemesh explore answer "question"
  codemesh explore search "query"
  codemesh explore trace symbolName --depth 5
  codemesh explore context path/to/file.ts
  codemesh explore impact path/to/file.ts
  ```

Both return the same JSON.

---

# What `codemesh_answer` returns

- `_usage` — **read this first**; it tells you whether Read is needed
- `relevantFiles` — ranked files with top symbols (name, kind, signature, lineStart/End), hotspot/co-change info, PageRank
- `suggestedReads` — top 5 symbols with **inline source snippets up to 30 lines**, plus `signature` and `truncated` flag
- `concepts` — agent-written summaries from prior sessions (file- and symbol-level)
- `workflows` — multi-file flows previously walked
- `symbolMap` — call graph rooted at matched symbols, with `children` (callees) and `calledBy` (callers)

---

# When to reach for other tools (only after `codemesh_answer` with a specific gap)

| Gap | Tool |
|---|---|
| Need more than 30 lines of a truncated symbol | `Read(file, offset=lineStart, limit=N)` — targeted range |
| Need a reverse-caller list deeper than 1 hop | `codemesh_trace({ symbol, depth })` |
| Need impact analysis ("what breaks if I change X") | `codemesh_explore({ action: "impact", path })` |
| `codemesh_answer` returned `_usage: No suggestedReads` | `codemesh_explore({ action: "search", query })` with different keywords |
| Need full symbol inventory of one file | `codemesh_explore({ action: "context", path })` |
| Need where a specific call resolves (overloaded name) | LSP `go-to-definition` |
| Need imports/types not shown in the snippet | `Read` with a targeted range (not whole file) |

---

# Writing back (enrich, workflow)

Enrich when you've learned something non-obvious that survives the session and helps future agents:
- "This function silently has a side-effect: ..." (hidden behavior)
- "The real entry point is Y, despite the naming suggesting Z" (misleading names)
- "This module is load-bearing for the X flow because ..." (hidden invariant)

NOT enrich candidates (skip):
- Anything derivable from reading the code
- Restating what a function name already says
- Task-specific notes ("fixed a bug here" belongs in the commit)

```
codemesh_enrich({
  path: "src/auth/session.ts",
  summary: "Session tokens are stored in Redis with 24h TTL but re-issued on every request — effectively sliding expiry. Load-bearing for the mobile flow.",
})
```

For multi-file flows you walked end-to-end, `codemesh_workflow` records the sequence.

---

# Complex questions (optional decomposition)

For broad comprehension questions ("explain the entire architecture", "walk me through feature X"), a single `codemesh_answer` may miss sub-topics. Then:

1. List the sub-topics
2. Call `codemesh_answer` per sub-topic with a targeted question
3. Aggregate responses

Do NOT decompose targeted questions. "How does X work?" is targeted — one call.

---

# Fall back to Grep/Read when

- `codemesh_answer` returned empty or unrelated results on multiple rewordings
- The file was just created and isn't indexed yet
- You're doing line-level debugging and know exactly what to look for
- The user asked you to read a specific file

---

# Writing your final answer

Cite files and line ranges from `suggestedReads` directly — `file:lineStart-lineEnd`. If `codemesh_answer` gave you enough, say so; don't invent extra exploration you didn't do.
