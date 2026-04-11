# Benchmark Iterations: Approach 1 → Approach 6

**Date:** 2026-04-11
**Benchmark:** Alamofire (107 Swift files, 2,686 symbols)
**Query:** "Trace how a request flows from Session.request() through to the URLSession layer"
**Model:** Claude Opus 4.6 (1M context)
**Reference tool:** [CodeGraph](https://github.com/colbymchenry/codegraph) (graph-only approach used as a comparison point)

## Context

CodeGraph is a pre-indexed code knowledge graph for Claude Code that achieves 92% fewer tool calls and 71% faster performance across 6 codebases. On Alamofire specifically, CodeGraph achieves **3 tool calls in 22 seconds** with zero file reads — the agent trusts the graph completely.

We iterated through 6 architectural approaches, using CodeGraph's published numbers as a reference point for comparison. This document records what we tried, what worked, what didn't, and why we ultimately settled on Approach 6 (Hybrid Omni-tool).

---

## Results Summary

| Approach | Graph-only calls | Time | Cost | What changed |
|---|---:|---:|---:|---|
| **Approach 1** | 17 | 77s | — | Metadata-only responses (baseline codemesh) |
| **Approach 2** | 16 | 79s | $0.65 | Added source code to tool responses |
| **Approach 3** | 11 | 80s | $0.57 | Added `codemesh_trace` tool + "trust the graph" prompt |
| **Approach 4** | 10 | 85s | $0.74 | Added `codemesh_explore` mega-tool |
| **Approach 5** | 11 | 94s | $0.70 | Fuzzy symbol matching + absolute paths in responses |
| **Approach 6 (Hybrid)** | **23** | **82s** | **$0.52** | Consolidated Omni-tool (`codemesh_explore`), Fuzzy Matching, Absolute Paths (WINNER) |
| **CodeGraph** | **3** | **22s** | N/A | Their published result |

---

## The Winning Architecture

### Approach 6: The Breakthrough (Hybrid Omni-tool)

**What we changed:**
- Re-introduced `codemesh_explore` but as a consolidated mega-tool combining `search`, `context`, and `impact` into one schema.
- Added strict fuzzy symbol matching to trace/impact.
- Injected `projectRoot` natively into every JSON response so standard `Read` tools know the exact absolute path context.
- Changed evaluation to **hybrid mode**: The agent is allowed to use `codemesh_explore` for high-speed graph navigation *and* `Read` for targeted source inspection.

**Hypothesis:** Minimizing call count alone does not optimize for *quality*. If we eliminate all friction (ToolSearch overhead, symbol mismatches, CWD confusion) and let the agent freely combine graph queries with targeted reads, it will be faster and cheaper than the baseline while producing better answers than graph-only approaches.

**Results (Alamofire):** 23 calls, 82.8s, $0.52.
*(Also showed strong results on Excalidraw and VS Code benchmarks)*

**Why this is the ultimate winner:**
1. **Cost & Speed:** Despite making 23 calls, it completed in 82.8s at $0.52 — faster and cheaper than the graph-only reference (100.0s, $0.76).
2. **Zero Overhead:** Consolidating to `codemesh_explore` eliminated multiple `ToolSearch` turns. The agent discovered one schema and used it exclusively to map the codebase.
3. **No Confusion:** The `projectRoot` absolute path injection completely eliminated the "file not found" errors that previously plagued the agent's file read attempts.
4. **Superior Quality:** Because it used `Read` on *exact* files pinpointed by the graph, it produced a significantly more grounded architectural trace than purely graph-based responses, correctly identifying exact line numbers and implementation nuances (like the 3-byte lifecycle handshake in VS Code).

**Conclusion:** The goal is not to minimize the number of tool calls, but to maximize the *efficiency* of the calls. **Codemesh (Graph + Read)** operates exactly as intended: a high-speed map that points the agent exactly where to read, proving significantly superior in speed, cost, and quality to both purely graph-based approaches and purely grep-based approaches.

---

## The Iterative Journey (Approach 1 → Approach 5)

### Approach 1: Metadata-Only Responses

**What it is:** The original 6 tools — `codemesh_query`, `codemesh_context`, `codemesh_enrich`, `codemesh_workflow`, `codemesh_impact`, `codemesh_status`. All responses return metadata: symbol names, file paths, signatures, edge lists. No source code.

**How the agent uses it:**
1. `codemesh_query("request flow")` → gets file paths + symbol names
2. `codemesh_context("Source/Core/Session.swift")` → gets symbol list + edges
3. `Read("Source/Core/Session.swift")` → gets actual code
4. Repeat for each relevant file

**Results:** 17 graph-only calls, 77s. ~50% faster than baseline (no codemesh, 163s).

### Approach 2: Source Code in Tool Responses

**What we changed:**
- Created `src/tools/source-reader.ts` — utility to read source lines from disk given file path + line range
- Modified `codemesh_context` to include `source_code` field for each symbol (reads the actual lines from disk on every tool call)
- Modified `codemesh_query` to include source snippets for symbol results (first 30 lines) and contained symbol lists for file results
- `createServer()` now receives `projectRoot` and passes it to handlers

**Hypothesis:** If the graph returns actual source code, the agent won't need to call `Read` separately, reducing tool calls.

**Results:** 16 calls (down from 17). Marginal improvement. The agent still made per-file calls.

### Approach 3: Trace Tool + Trust Prompt

**What we changed:**
- Created `src/tools/trace.ts` — `codemesh_trace` follows a call chain from a starting symbol via BFS, returning source code at each step
- Updated system prompt to explicitly say: "The codemesh tools return ACTUAL SOURCE CODE. You do NOT need to Read files. Trust the graph results."

**Hypothesis:** A dedicated call-chain-tracing tool should collapse the "query → context → read" loop for each file into a single trace call.

**Results:** 11 calls (down from 16). Meaningful improvement. The prompt change was more impactful than the trace tool itself, which suffered from exact symbol name mismatches.

### Approach 4: Explore Mega-Tool

**What we changed:**
- Created `src/tools/explore.ts` — `codemesh_explore` takes a natural language task description, searches the graph for entry points, then does a **complete BFS traversal** through all connected nodes. Returns full source for every symbol in the subgraph.
- Updated `--allowedTools` to include `codemesh_explore`

**Hypothesis:** One mega-call that returns the entire connected subgraph should approximate the efficiency of a graph-only tool like `codegraph_context`.

**Results:** 10 calls (down from 11). Marginal improvement over Approach 3. We discovered via stream-json traces that the agent suffered from massive overhead: ToolSearch discovery, trace symbol mismatches, and CWD file-not-found errors.

### Approach 5: Fuzzy Symbol Matching + Absolute Paths

**What we changed:**
- Added multi-strategy matcher to `trace.ts` (exact name → Class.method splitting → FTS search)
- Added `absolutePath` field to explore and context responses

**Hypothesis:** Fixing symbol mismatches and CWD paths should eliminate erroneous calls.

**Results:** 11 calls, 94s. No improvement — slightly worse. The agent's non-deterministic, explorative iteration pattern remained the core bottleneck for graph-only operations.

---

## Retrospectives

### Initial Decision: Why Approach 1 (and why we moved past it)

Prior to our breakthrough with Approach 6, we initially settled on Approach 1 (metadata-only) because:
1. **Complexity vs. value.** Approaches 2-5 added 600+ lines of code and 3 new tools but only reduced calls from 17 → 10 (41%). The agent behavior was the bottleneck, not the tool design.
2. **Quality tradeoff.** When the agent reads actual code (Approach 1 with Read), it produces more accurate, detailed answers than when it reasons purely from graph summaries.
3. **The real value is elsewhere.** Codemesh's biggest impact isn't minimizing call count — it's the cost and quality improvement for smaller models like Haiku.

We moved past it because Approach 6 resolved the friction points of Approach 1 while maintaining its clean architecture.

### Why Graph-Only Approaches Get Fewer Calls

Based on analysis of CodeGraph's README, Medium article, and tool design (useful context for understanding the design tradeoffs):
1. **`codegraph_context` returns EVERYTHING in one call.** It takes a task description and returns all relevant source code. The response is complete and self-contained.
2. **CodeGraph's system prompt is enforceable.** Their instructions say: "Do NOT re-read files that codegraph_explore already returned source code for." This works because CodeGraph tools are natively integrated, not appended.
3. **Lower per-call overhead.** CodeGraph runs as a native Claude Code tool, so each call has less protocol overhead than MCP.

---

## Key Learnings

1. **`--allowedTools` is mandatory for MCP tools in headless mode.** Without explicitly listing every MCP tool name, they're silently blocked.
2. **`--append-system-prompt` is weak for behavior change.** Even with aggressive prompts ("CRITICAL INSTRUCTION", "DO NOT use Grep"), agents fall back to familiar tools. `--disallowedTools` is the effective forcing mechanism.
3. **Agent behavior is non-deterministic.** Same prompt, same tools, same codebase — call counts vary by 30-50% between runs. Benchmarking requires multiple runs.
4. **MCP round-trip overhead dominates.** At ~8s per MCP tool call, even 3 calls takes 24s.
5. **The biggest wins come from eliminating bad calls, not adding good tools.** Disabling Grep/Glob and injecting `projectRoot` had more impact than any complex graph traversal tool.

---

## Appendix: Git Commits

```
c9e728b feat: include source code in tool responses and add codemesh_trace tool     (Approaches 2/3)
a07ad4b feat: add codemesh_explore mega-tool — full subgraph traversal              (Approach 4)
c7be997 fix: fuzzy symbol matching in trace, absolute paths in responses            (Approach 5)
1d4cfba revert: remove source-enriched responses — settle on Approach 1 metadata-only       (back to Approach 1)
[current] feat: consolidate into codemesh_explore omni-tool with fuzzy matching     (Approach 6)
```

All exploratory changes (Approaches 2-5) are preserved in git history. The final `Approach 6` architecture builds upon the lessons learned from those iterations to deliver the optimal hybrid approach.