# vNext live ablation: completed results

Completed **2026-10-18**: 90 graded trajectories, 88 final hidden-acceptance passes,
86 first-patch passes. Two provider timeouts were preserved and retried, for 92
attempts total. The separately recorded one-call preflight is outside this matrix.

The controlled runner requested Codex CLI `gpt-6-luna`, low reasoning, against
Pydantic commit `5adfeaaaea947f81f0f01868604f219084ec151a`. This is three distinct
seeded repair tasks, three repeats, two arms, and five configurations—not 90
independent bugs. Each hidden suite has two tests. Both arms use harness-executed
actions; CodeMesh uses its public CLI. This does not measure default MCP adoption.

## Main result

**Compact working state plus hydration was the best observed CodeMesh configuration:**
237,801 input tokens, 9/9 first-patch and final passes. That is **23.4% fewer input
tokens than CodeMesh with full history**, and **12.7% fewer than filesystem with
the same hydration configuration**.

However, the lowest-input filesystem configuration was working state alone:
236,603 tokens with 9/9 passes. Comparing those two selected configurations,
CodeMesh used **0.5% more input**, **9.9% less output**, **18.5% fewer model calls**
(97 versus 119), and **13.6% less measured model time**. This is a descriptive,
post-hoc configuration comparison; it does not isolate hydration's causal effect.
The experiment does **not establish a consistent input-token advantage over an
optimized filesystem harness**. Billed USD is unavailable.

## Matched configurations

All input totals below cover nine graded trajectories per arm. Negative deltas
mean CodeMesh used fewer input tokens than that configuration's filesystem arm.
Failed graded trajectories remain included; timeout overhead is separate.

| Configuration | Filesystem input | CodeMesh input | CodeMesh vs filesystem | Filesystem final | CodeMesh final |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full history | 398,209 | 310,252 | −22.1% | 8/9 | 9/9 |
| Working state | 236,603 | 277,355 | +17.2% | 9/9 | 9/9 |
| Working state + hydration | 272,513 | 237,801 | −12.7% | 9/9 | 9/9 |
| Working state + bundled reads | 237,812 | 300,324 | +26.3% | 9/9 | 9/9 |
| Working state + both | 292,651 | 269,779 | −7.8% | 9/9 | 8/9 |

Within CodeMesh, input changes versus full history were −10.6% for working state,
−23.4% with hydration, −3.2% with bundled reads, and −13.0% with both. The combined
variant's early investigation failure contributes to its apparent reduction;
on its eight matched successful repairs versus full history, reduction was 4.6%.

## What the traces establish

- **Hydration reduces resume roundtrips.** Editing calls fell 37→28 for CodeMesh
  and 41→25 for filesystem versus working state alone. Editing input fell only
  2.6% and 0.4%, respectively. Whole-run differences also include changed earlier
  investigation paths, before automatic hydration activates.
- **Bundling is correct but not an aggregate win here.** Only two one-hop inspect
  calls occurred across the matrix. They returned verified source; one also
  duplicated previously read evidence, and another still required caller
  discovery. CodeMesh input with bundled reads rose 8.3% versus working state.
- **Compact serialization loses prefix stability.** All working-state variants
  recorded zero cached input. In the audited full/combined comparison, median
  adjacent stdin prefixes fell from roughly 5–6 KB to 390 bytes. Sorted JSON puts
  changing archive counters before retained evidence. This is a measured layout
  issue and a cache hypothesis, not proof of server cache causality.
- **Search and workflow behavior still dominate some outcomes.** The combined
  CodeMesh failure exhausted investigation while broad searches prioritized test
  helpers and legacy methods. The full filesystem failure proposed the correct
  fix but repeatedly requested an edit during an investigation-only phase.
  Both remain graded failures. Two other first patches failed hidden tests but
  were subsequently corrected.

## Next iteration

1. Test a stable task/evidence prefix followed by volatile navigation metadata.
   Preserve exact evidence, freshness checks, eviction, and edit invalidation.
2. Improve symbol search for unqualified method names and caller discovery; use
   the recorded failed queries as regression fixtures.
3. Expose only phase-appropriate actions in the evaluation schema to remove
   repeated rejected-edit loops, then establish a fresh control for that protocol.
4. Keep hydration available. Treat bundling as an optional retrieval primitive
   until broader tasks and deliberate use show a repeatable benefit.

No source or frozen harness changes were made while the matrix ran. These next
steps have not been implemented or evaluated by this run.

## Accounting and verification

The 90 graded trajectories used 1,116 valid model calls, 2,833,299 input tokens,
and 137,215 output tokens, with zero unexpected native-tool/router events. The
two failed attempts add eight journaled calls, 11,733 known input tokens, 587
known output tokens, and 395.062 seconds of measured model time. Each timeout
has one call with unknown usage, so complete operational token totals and billed
cost remain unknown. The known input lower bound is 2,845,032, excluding preflight.

All 1,124 journaled calls and 5,532 input files passed independent integrity
checks. Task coverage has no duplicate graded keys; frozen build, protocol, and
catalog fingerprints match. The offline reporter passes 21 tests. Execution was
blocked by variant with at most two workers, so timing/cache conditions were not
randomized across configurations. The seed controls ordering, not model sampling.

## Evidence

- [Complete aggregate report](results/vnext-ablation-2026-10-18/ABLATION_REPORT.md)
- [Machine-readable aggregates](results/vnext-ablation-2026-10-18/ablation-report.json)
- [Final completion record](results/vnext-ablation-2026-10-18/completion_status.json)
- [Integrity audit](results/vnext-ablation-2026-10-18/integrity-audit.json)
- [Hydration phase audit](results/vnext-ablation-2026-10-18/HYDRATION_PHASE_AUDIT.md)
- [Bundled-source trace audit](results/vnext-ablation-2026-10-18/BUNDLE_TRACE_AUDIT.md)
- [Cache-prefix audit](results/vnext-ablation-2026-10-18/CACHE_PREFIX_AUDIT.md)

Each variant directory contains complete `runs.jsonl`, `calls.jsonl`, exact
`inputs/`, observations, handoffs, and patches. Failed attempts remain in the
journals. `completion_status.json` records final completion; the earlier
`orchestration_status.json` preserves the initial launcher stop before resumption.
