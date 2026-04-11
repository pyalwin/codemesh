# Codemesh v2 Roadmap

**Date:** 2026-04-11
**Based on:** Benchmark results from CodeGraph head-to-head + Gemini architecture analysis

## Current State (v1)

- 102 tests, metadata-first architecture
- MCP + CLI modes, 3-phase workflow (Decompose → Explore → Verify & Enrich)
- Sonnet + Codemesh: 55% cheaper, 61% faster than baseline, 9/10 quality
- Tied with CodeGraph on pairwise quality (2-2 across 4 benchmarks)
- Multi-session enrichment working (0 → 11 concepts across 3 sessions)

## Action Items (prioritized by impact)

### 1. Speed Foundation: Stat Indexing + Batch Transactions

**Impact:** 10x indexing speed on large repos
**Effort:** Low
**Status:** Implementing now

#### 1a. Stat-based incremental indexing
Replace file hash comparison with `fs.stat` (mtime + size). Avoids reading file content for unchanged files.

```typescript
// Current: reads every file to compute SHA256 hash
const content = readFileSync(absPath, "utf-8");
const hash = createHash("sha256").update(content).digest("hex").slice(0, 16);

// New: check mtime + size first, only hash if changed
const stat = statSync(absPath);
const fastKey = `${stat.mtimeMs}:${stat.size}`;
// Only read + hash if fastKey differs from stored value
```

#### 1b. SQLite batch transactions
Wrap all inserts for a file (or batch of 10 files) in a single `BEGIN TRANSACTION`.

```typescript
// Current: one INSERT per node (50 writes/sec)
await storage.upsertNode(fileNode);
await storage.upsertNode(symbolNode1);
await storage.upsertNode(symbolNode2);

// New: batch in transaction (50,000 writes/sec)
await storage.beginTransaction();
await storage.upsertNode(fileNode);
await storage.upsertNode(symbolNode1);
await storage.upsertNode(symbolNode2);
await storage.commitTransaction();
```

### 2. Embed LSP Inside Codemesh Tools

**Impact:** Eliminates symbol ambiguity without agent needing to use LSP directly
**Effort:** Medium
**Status:** Next after Phase 1

The agent won't use LSP even when prompted. Solution: embed LSP calls inside codemesh tools.

When `codemesh_explore(action='context', path='file.ts', symbol='request')` is called:
1. Find the symbol in our graph (current behavior)
2. If multiple matches, spawn a language server and resolve via go-to-definition
3. Return the resolved result with exact file:line

Implementation:
- `src/tools/lsp-client.ts` — lightweight LSP client that spawns language servers on demand
- Language server detection: check for `typescript-language-server`, `pyright`, `sourcekit-lsp` on PATH
- Cache the language server process for the session
- Fallback gracefully: if no LSP available, return graph-only results (current behavior)

### 3. Trigram Search (Zoekt-style)

**Impact:** Fuzzy symbol search — find `_internal_process` when searching "process"
**Effort:** Medium
**Status:** After LSP

Current FTS5 uses word-based tokenization. Misses partial matches, underscore-separated names, camelCase fragments.

Implementation:
- Add a `trigrams` table: for each symbol name, generate all 3-char substrings
- On search: generate trigrams from query, intersect with stored trigrams, rank by match count
- Keep FTS5 for natural language queries, use trigram for symbol fragments
- SQLite implementation: `CREATE TABLE trigrams (trigram TEXT, node_id TEXT, INDEX idx_tri ON trigrams(trigram))`

### 4. Stack Graphs (v2.0)

**Impact:** 100% accurate go-to-definition without LSP
**Effort:** High (multi-week)
**Status:** v2.0 roadmap

Replace name-based symbol matching with GitHub's Stack Graph algorithm:

- **Push nodes**: created at definitions (`function save()`)
- **Pop nodes**: created at call sites (`db.save()`)
- **Scope nodes**: created for blocks, classes, modules
- **Resolution**: path-finding from Pop to matching Push across the graph

This eliminates the ambiguity problem entirely: when 5 classes have a `save()` method, the stack graph knows which one a specific `db.save()` call resolves to.

Implementation:
- `src/graph/stack-graph.ts` — Stack graph node types and path-finding algorithm
- Update `src/indexer/parser.ts` — emit Push/Pop/Scope nodes instead of flat symbols
- New tool: `codemesh_resolve` — given a call site, return the exact definition
- Reference: [GitHub Stack Graphs](https://github.blog/2021-12-09-introducing-stack-graphs/)

### Future: Fact Stacking (Glean-style)

Instead of purge + replace on re-index:
- Write new facts to a "hot layer" table
- Query merges hot + cold layers (newest shadows oldest)
- Periodic vacuum merges layers
- Enables "instant refresh" — agent's own edits visible immediately

---

## Benchmark Targets

| Metric | Current (v1) | Target (v2) |
|---|---|---|
| Index time (1000 files) | ~6s | <1s |
| Index time (25K files) | ~60s | <5s |
| Symbol resolution accuracy | ~70% (name match) | ~95% (LSP) / 100% (Stack Graphs) |
| Agent tool calls (trace task) | 15-25 | 5-10 |
| Search: partial symbol match | ❌ | ✅ (trigram) |
| Incremental refresh | Seconds | Milliseconds |
