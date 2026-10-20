# vNext retrieval reset

Date: 2026-10-18

## Decision

Keep the current SQLite index, CLI, and source freshness contract. The next experiment tests whether better ranking and bounded navigation let an agent reach the correct edit with fewer observations. The recent compact formatter and automatic handoff restoration are part of the starting point. We do not need an architecture rewrite to test this.

Keep previous reports and raw runs as historical evidence. They describe the implementation and harness that produced them; they are not results for the new implementation.

## What the existing traces establish

The source of these counts is the nine graded Codemesh trajectories in [the full agent report](../../eval/results/vnext-agent-full/report.json), paired with nine filesystem trajectories. There were also two ungraded Codemesh runner-error attempts.

| Finding | Evidence | Implication |
| --- | --- | --- |
| Large class reads dominate source output | Three full `FieldInfo` reads delivered 43,110 bytes each: 129,330 bytes, or 71.5% of successful source observation bytes. The class spans lines 99–1046, or 948 lines. Three `ByteSize` class reads add 19,791 bytes. Together these six calls account for 82.4% of source bytes. | Start with a bounded class view that exposes member IDs and ranges. |
| Exact repeats happen after context reset | All seven repeated observations, totaling 49,919 bytes, cross into fresh phase 2. Three source repeats account for 46,950 bytes; four native read repeats account for 2,969 bytes. There are no same-session exact duplicate observations in these graded Codemesh runs. | Automatic same-session dedup would save none of these measured bytes. Delivery in an earlier session cannot justify withholding source in a fresh context. |
| Saved handoffs were not successfully retrieved | The harness saved seven notes. The single recall attempt used `path="pydantic"` and returned `notes: []`: zero successful recalls out of seven saved handoffs. Filesystem agents received their handoff directly. | The original comparison had an asymmetric restart. Restore the saved handoff deterministically, as the updated harness now does. |
| Search relevance needs improvement | `ByteSize byte quantity` ranked `pydantic/v1/parse.py:load_str_bytes` first. `FieldInfo get_default` ranked the legacy `FieldInfo` class first and did not return the requested method. | Measure retrieval relevance directly before paying for another agent run. |
| Tool semantics cause recovery calls | Four source calls failed: three used file paths where symbol IDs were required; one requested `FieldInfo.get_default`, which was not found. Kebab agents also explicitly reread the module for wider context after reading a symbol. | Returned navigation must expose usable IDs and enough surrounding structure. Missing line numbers alone do not explain rereads. |

Codemesh passed seven of nine hidden tests against filesystem's eight of nine. It had three turn limits and one context limit. Two turn limits occurred during initial investigation before a patch; the remaining turn limit and context limit occurred after correct patches. The traces support investigating excessive observations, but do not prove that context bloat caused every unfinished run.

## Accounting limits

The graded comparison reports cost of $2.112 versus $1.958, an increase of 7.8%. Including the two excluded Codemesh runner-error attempts brings its spend to $2.413, an increase of 23.2%. Both comparisons should remain visible.

The 47.3% reduction in uncached input is not a standalone savings result. Uncached input plus cache creation fell 20.0%, while cache reads rose 147.0% and output tokens rose 7.5%.

Large token totals also appear before a new session receives any tool observations. Codemesh first decisions across phases 0 and 2 account for 2,486,930 reported input tokens, versus 686,764 for filesystem. For example, one first decision reports 579,816 input tokens. These CLI counters may aggregate internal requests or structured-output work. The existing records do not identify the mechanism, and cannot be treated as the context size of one model request. Source and search payloads therefore cannot be asserted to be the sole cause of the token increase.

Record available raw usage metadata, internal turn counts, model identity, retries, and reported cost in future runs. Report cost only when both arms have trustworthy, comparable accounting. Observation bytes remain useful regardless of provider billing details.

## Bounded implementation scope

1. Fix search ranking so lexical relevance is not overridden by global popularity. Preserve exact-name handling and use a small default result count with explicit expansion. Avoid blanket penalties for tests or legacy paths: a query can legitimately request either.
2. Add an explicit bounded source navigation view. The implemented `auto` mode returns a bounded preview with omitted ranges and a hint to map the class; map exposes member IDs for exact follow-up reads. Preserve exact source retrieval for selected members and explicit full reads. Source used for an edit must remain available as live-checked evidence.
3. Use a symmetric restart: filesystem receives its written handoff; Codemesh receives its persisted, freshness-checked handoff automatically. In both arms the agent verifies live source before editing.
4. Keep session delivery records separate from context residency. Do not automatically suppress source across fresh sessions or compaction. The existing exact-repeat metric also misses overlapping symbol and native reads; measure that overlap separately where possible.

## Experiment and gates

First run a deterministic retrieval check on pinned source. Replay the observed queries for diagnosis, then evaluate held-out queries before another ranking change. Include exact symbols, multiword symptoms, overloaded methods, large classes, and requests specifically about tests or legacy code. Record expected target rank, target presence within the returned limit, usable IDs, and output bytes. Record retrieval misses explicitly; the bounded pilot can test whether map expansion and native fallback recover them. Widening the agent comparison requires inspecting remaining misses.

Then run a small paired agent pilot with identical tasks, model, provider, session boundaries, turn caps, and edit/test capabilities across filesystem and Codemesh arms. If moving from Claude CLI with Haiku to Codex CLI with Luna, establish a fresh filesystem baseline with that same Codex/Luna configuration. Historical Haiku runs remain references; mixing their baseline with new Luna results would confound the comparison.

The primary acceptance gate is preserved hidden-test acceptance. The efficiency gate is fewer observation bytes and fewer tool calls before the first correct edit. Also report completion, recovery calls, navigation expansion, end-to-end observations, latency, and all-attempt spend. A small pilot screens for regressions; it does not establish broad quality equivalence.

If acceptance falls, inspect the failing evidence path before widening the run. If payloads shrink but calls or time to the correct edit do not improve, stop adding compression and address navigation or tool semantics. Expand the paired sample only after the pilot meets the quality and retrieval-efficiency gates. Make a cost-improvement claim only after comparable accounting supports it.
