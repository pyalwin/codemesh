# CodeGraph Benchmark Iterations: v1 → v5

**Date:** 2026-04-11
**Benchmark:** Alamofire (107 Swift files, 2,686 symbols)
**Query:** "Trace how a request flows from Session.request() through to the URLSession layer"
**Model:** Claude Opus 4.6 (1M context)
**Competitor:** [CodeGraph](https://github.com/colbymchenry/codegraph) by Colby McHenry

## Context

CodeGraph is a pre-indexed code knowledge graph for Claude Code that achieves 92% fewer tool calls and 71% faster performance across 6 codebases. On Alamofire specifically, CodeGraph achieves **3 tool calls in 22 seconds** with zero file reads — the agent trusts the graph completely.

We attempted to match these numbers through 5 iterations of architectural changes. This document records what we tried, what worked, what didn't, and why we settled on v1.

---

## Results Summary

| Version | Graph-only calls | Time | Cost | What changed |
|---|---:|---:|---:|---|
| **v1** | 17 | 77s | — | Metadata-only responses (baseline codemesh) |
| **v2** | 16 | 79s | $0.65 | Added source code to tool responses |
| **v3** | 11 | 80s | $0.57 | Added `codemesh_trace` tool + "trust the graph" prompt |
| **v4** | 10 | 85s | $0.74 | Added `codemesh_explore` mega-tool |
| **v5** | 11 | 94s | $0.70 | Fuzzy symbol matching + absolute paths in responses |
| **CodeGraph** | **3** | **22s** | N/A | Their published result |

---

## v1: Metadata-Only Responses (settled)

**What it is:** The original 6 tools — `codemesh_query`, `codemesh_context`, `codemesh_enrich`, `codemesh_workflow`, `codemesh_impact`, `codemesh_status`. All responses return metadata: symbol names, file paths, signatures, edge lists. No source code.

**How the agent uses it:**
1. `codemesh_query("request flow")` → gets file paths + symbol names
2. `codemesh_context("Source/Core/Session.swift")` → gets symbol list + edges
3. `Read("Source/Core/Session.swift")` → gets actual code
4. Repeat for each relevant file

**Results:** 17 graph-only calls, 77s. ~50% faster than baseline (no codemesh, 163s).

**Why we settled here:** Despite not matching CodeGraph's 3 calls, v1 delivers real value:
- 19% cheaper, 41% faster on Opus across 5 pydantic tasks
- 44% cheaper, 57% faster on Sonnet
- Dramatically improves quality for smaller models (Haiku 3.6 → 8.2 quality score)
- Clean, simple architecture — 6 tools, each does one thing well
- The agent naturally combines graph queries with targeted reads, which produces higher quality answers than graph-only

---

## v2: Source Code in Tool Responses

**What we changed:**
- Created `src/tools/source-reader.ts` — utility to read source lines from disk given file path + line range
- Modified `codemesh_context` to include `source_code` field for each symbol (reads the actual lines from disk on every tool call)
- Modified `codemesh_query` to include source snippets for symbol results (first 30 lines) and contained symbol lists for file results
- `createServer()` now receives `projectRoot` and passes it to handlers

**Hypothesis:** If the graph returns actual source code, the agent won't need to call `Read` separately, reducing tool calls.

**Results:** 16 calls (down from 17). Marginal improvement.

**Why it didn't help much:** The agent still made nearly the same number of calls. Even with source in the response, it called `codemesh_context` once per file rather than trusting a single comprehensive response. The source code was there but the agent treated each tool call as "get one file's context" rather than "get everything I need."

---

## v3: Trace Tool + Trust Prompt

**What we changed:**
- Created `src/tools/trace.ts` — `codemesh_trace` follows a call chain from a starting symbol via BFS, returning source code at each step
- Updated system prompt to mention `codemesh_trace` and explicitly say: "The codemesh tools return ACTUAL SOURCE CODE, not just metadata. You do NOT need to Read files — the source is in the response. Trust the graph results."

**Hypothesis:** A dedicated call-chain-tracing tool should collapse the "query → context → read" loop for each file into a single trace call. The stronger prompt should prevent unnecessary Read fallbacks.

**Results:** 11 calls (down from 16). Meaningful improvement.

**What worked:** The prompt change was more impactful than the trace tool itself. Telling the agent to "trust the graph" reduced Read fallbacks.

**What didn't work:** The trace tool had a symbol matching problem. The agent would call `codemesh_trace({ symbol: "Session.request" })` but the graph stores it as just `request` (method names without class prefix). So trace found nothing, and the agent fell back to other tools.

---

## v4: Explore Mega-Tool

**What we changed:**
- Created `src/tools/explore.ts` — `codemesh_explore` takes a natural language task description, searches the graph for entry points, then does a **complete BFS traversal** through all connected nodes (calls, callers, imports, contains) until leaf or visited. Returns full source for every symbol in the subgraph.
- Updated system prompt to make `codemesh_explore` the PRIMARY tool
- Updated `--allowedTools` to include `codemesh_explore`

**Hypothesis:** One mega-call that returns the entire connected subgraph should match CodeGraph's `codegraph_context` — which also takes a task description and returns everything.

**Results:** 10 calls (down from 11). Marginal improvement over v3.

**Why it didn't close the gap:** We captured a stream-json trace of the agent's actual tool calls and found:

```
Call  1: ToolSearch            → discovering MCP tool schemas (overhead)
Call  2: codemesh_explore      → the right call! Got results.
Call  3: codemesh_trace        → tried tracing "Session.request" (symbol mismatch)
Call  4: codemesh_trace        → tried "Session.perform" (same issue)
Call  5: codemesh_trace        → tried "Session.performSetupOperations" (same)
Call  6: Read                  → fell back to reading files (wrong CWD path)
Call  7: Bash                  → tried to find files (wrong CWD)
Call  8: Bash                  → listed directory (still confused)
Call  9: codemesh_explore      → second explore with more specific task
Call 10: ToolSearch            → re-loaded tool schemas (redundant)
Call 11: codemesh_context      → drilled into specific file
Call 12: codemesh_context      → drilled into another file
Call 13: codemesh_query        → searched for specific symbol
```

Three distinct problems emerged:
1. **Symbol name mismatch** in trace (calls 3-5): "Session.request" not found because graph stores "request"
2. **Wrong CWD** for Read/Bash (calls 6-8): claude process runs in codemesh project dir, not /tmp/alamofire
3. **ToolSearch overhead** (calls 1, 10): Claude Code loads MCP tool schemas lazily, costing 2 round trips

---

## v5: Fuzzy Symbol Matching + Absolute Paths

**What we changed:**
- Added `findSymbol()` multi-strategy matcher to `trace.ts`: tries exact name → Class.method splitting → FTS search
- Added `absolutePath` field to explore and context responses so Read calls work regardless of CWD
- Updated explore prompt to emphasize "start with codemesh_explore"

**Hypothesis:** Fixing the two identified bugs (symbol mismatch, CWD paths) should eliminate calls 3-8 from the trace above, leaving ~4-5 calls.

**Results:** 11 calls, 94s. No improvement — slightly worse.

**Why it didn't help:** The agent's behavior is non-deterministic. Even with better symbol matching, it chose different exploration patterns each run. The absolute paths didn't get exercised because the graph-only mode disables Read entirely. The fundamental issue is that the agent iterates exploratively rather than trusting one comprehensive response.

---

## Why CodeGraph Gets 3 Calls

Based on analysis of CodeGraph's README, Medium article, and tool design:

1. **`codegraph_context` returns EVERYTHING in one call.** It takes a task description and returns all relevant source code, not just metadata. The response is complete and self-contained. Our explore tool returns similar data but the agent doesn't trust it the same way.

2. **CodeGraph's system prompt is enforceable.** Their instructions say: "Do NOT re-read files that codegraph_explore already returned source code for. The source sections are complete and authoritative." This works because CodeGraph tools are tightly integrated into Claude Code's permission system, not via `--append-system-prompt`.

3. **Lower per-call overhead.** CodeGraph runs as a native Claude Code tool (not MCP), so each call has less protocol overhead. Our MCP round-trips take ~8s each; theirs appear to take ~7s.

4. **Richer per-call responses.** CodeGraph stores and returns source code directly from the graph DB. Our tools read source from disk on each call, which is slower but keeps the index smaller.

---

## Decision: Why v1

We settled on v1 (metadata-only) because:

1. **Complexity vs. value.** v2-v5 added 600+ lines of code and 3 new tools but only reduced calls from 17 → 10 (41%). The agent behavior is the bottleneck, not the tool design. More tools = more for the agent to consider = sometimes more calls, not fewer.

2. **Quality tradeoff.** When the agent reads actual code (v1 with Read), it produces more accurate, detailed answers than when it reasons purely from graph summaries (v4-v5 graph-only). The pydantic eval showed this: v1 codemesh+read responses scored 7.6/10 on Opus, comparable to baseline's 8.0/10.

3. **The real value is elsewhere.** Codemesh's biggest impact isn't matching CodeGraph's call count — it's the cost and quality improvement for smaller models. Haiku + Codemesh (8.2/10, $1.79) beats Opus baseline (8.0/10, $3.35). That's the publishable result, and v1 delivers it.

4. **Clean architecture.** 6 tools, each with a single purpose, well-tested (102 tests). Easy to understand, maintain, and extend. v4 with the explore mega-tool was 200+ lines of complex graph traversal that the agent used suboptimally.

5. **Future path is agent-side, not tool-side.** The remaining gap with CodeGraph is about agent behavior (how many follow-up calls it makes) and MCP overhead (protocol latency per call). Neither is solved by adding more tools. The right next steps are:
   - Better skill/prompt integration (not `--append-system-prompt` which is weak)
   - Reducing MCP round-trip overhead
   - Or: building a Claude Code plugin (not MCP) to match CodeGraph's integration model

---

## Key Learnings

1. **`--allowedTools` is mandatory for MCP tools in headless mode.** Without explicitly listing every MCP tool name, they're silently blocked. This was our biggest debugging hurdle.

2. **`--append-system-prompt` is weak for behavior change.** Even with aggressive prompts ("CRITICAL INSTRUCTION", "DO NOT use Grep"), agents fall back to familiar tools. `--disallowedTools` is the effective forcing mechanism.

3. **Agent behavior is non-deterministic.** Same prompt, same tools, same codebase — call counts vary by 30-50% between runs. Benchmarking requires multiple runs for meaningful comparisons.

4. **MCP round-trip overhead dominates.** At ~8s per MCP tool call, even 3 calls takes 24s. The protocol overhead (JSON serialization, stdio transport, tool schema validation) is a significant fraction of total time.

5. **More tools can mean more calls, not fewer.** When we added trace + explore on top of query + context, the agent sometimes used all four in sequence instead of trusting any single one. Tool proliferation hurts when the agent doesn't know which to prefer.

6. **Source code in responses helps marginally.** Including source code reduced calls from 17→16 (6%). The agent still made per-file calls regardless of response richness. The bottleneck is the agent's iterative exploration pattern, not information availability.

7. **The biggest wins come from eliminating bad calls, not adding good tools.** Disabling Grep/Glob (which forced graph usage) and adding `--allowedTools` for MCP tools (which unblocked them) had more impact than any tool optimization.

---

## Appendix: Git Commits

```
c9e728b feat: include source code in tool responses and add codemesh_trace tool     (v2/v3)
a07ad4b feat: add codemesh_explore mega-tool — full subgraph traversal              (v4)
c7be997 fix: fuzzy symbol matching in trace, absolute paths in responses            (v5)
1d4cfba revert: remove source-enriched responses — settle on v1 metadata-only       (back to v1)
```

All v2-v5 changes are preserved in git history and can be restored if needed.
