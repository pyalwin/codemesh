# Selective context and delegated retrieval

**Parked on 19 October 2026.** The active direction is the
[single-agent deterministic implementation](../docs/experiments/graphify-deterministic.md).
This prototype and its exact inputs/results remain available for reference;
the main MCP/CLI and `vnext_agent.py` do not invoke its selector.

This experiment tests the user's proposed workflow: navigate a structural map,
select relevant reads, make explicit retention decisions, and send original
source evidence to a separate reasoning model.

## Responsibility split

```text
Original task + graph frontier + source descriptors
                         |
                  Luna selector
           / next read   | keep/archive/defer
          v              v
    Public CodeMesh CLI  Validated working state + archive
                              |
                   Terra reasoning and editing
                              |
                 Missing-evidence request, if needed
                              |
                       Back to selector
```

The selector chooses from concrete harness-owned actions. It cannot invent tool
arguments or rewrite code facts. Its relevance/confidence estimates are
heuristics, not proof. Traversing a branch does not automatically discard its
siblings. Explicit decisions preserve their rationale in the observation archive.

The task, compact root index, source evidence, errors, edits, and test state are
protected. Exact source remains under the existing 16 KiB budget; this first
pilot selects navigation retention, rather than delegating source eviction.
Deferred navigation and pagination pointers remain recoverable. After an edit,
stale source loses its conditional-read authority.

## Model transport

At the user's request, this pilot uses **Codex CLI**, with requested
`gpt-6-luna` for selection and **`gpt-5.6-terra`** for reasoning/editing. Terra is
also used in both controls. No Astra or Sol evaluation calls are used.

The design was informed by the
[Decisions guide](https://developers.openai.com/api/docs/guides/decisions),
but the dedicated Decisions API is **not invoked**. The CLI prototype uses
structured choices and written rationales; it does not reproduce that API's
response contract, calibrated behavior, latency, or pricing.

## Evidence

- [Combined comparison and audit](results/selected-context-2026-10-19/COMPARISON.md)
- [Original nine-run pilot report](results/selected-context-2026-10-19/pilot/REPORT.md)
- [Revised three-run selected treatment](results/selected-context-2026-10-19/pilot-v2/REPORT.md)
- [Machine-readable live results](results/selected-context-2026-10-19/pilot/report.json)
- [Frozen plan and fingerprints](results/selected-context-2026-10-19/pilot/manifest.json)
- [Reused control parity checks](results/selected-context-2026-10-19/pilot-v2/control-parity.json)
- [Offline branch-switch fixture](results/branch-compaction-2026-10-19/offline/REPORT.md)
- [Exact before/after fixture prompts](results/branch-compaction-2026-10-19/offline/scripted-journey/before-after-prompts.json)

The offline fixture proves state-management properties, not model quality or
token savings. Its small maps do not show an overall byte advantage: bookkeeping
can outweigh removed details. The live pilot counts both model roles, including
failed runs and selector overhead. Billed USD is unavailable from this CLI.

## Measured outcome — 19 October 2026

Twelve live trajectories were run: the original three-condition pilot, then a
revised selected treatment against the same six controls. Each condition covers
the same three seeded Pydantic repairs once. Both live versions and their exact
inputs are preserved; the rerun does not replace failed original outcomes.

| Condition | Total input tokens | Change vs full | Accepted final patches | Finished workflows |
| --- | ---: | ---: | ---: | ---: |
| Terra, full transcript | 69,488 | baseline | 3/3 | 3/3 |
| Terra, deterministic working state | 73,929 | +6.4% | 3/3 | 3/3 |
| Luna selector + Terra, original | 299,369 | +330.8% | 2/3 | 2/3 |
| Luna selector + Terra, revised | 350,859 | +404.9% | 2/3 | 1/3 |

The revised treatment enabled real navigation archival, but did not demonstrate
token savings. Luna consumed 266,823 input tokens (76.0% of combined input).
Metadata and repeated selection decisions outweighed removed map detail.
One accepted patch still exhausted the call budget without finishing the
workflow; acceptance and completion are therefore separate measurements.

The trace audit also found an implementation error in both measured selector
versions: a source descriptor could claim exact source was available even when
CodeMesh returned `status=stale, source=null`. The descriptor omitted the recovery
hint. This contributed to the revised ByteSize case's post-edit loop. Navigation
archival did not evict that source: the edit correctly invalidated it. The factory
case exhausted its budget retrieving the missing resolver implementation.

These are observations about this CLI prototype, with retrieval, model routing,
and context retention changed together. They do not isolate the causal effect of
compaction or establish that a decision model cannot help. No billed cost saving
is established, and the actual Decisions endpoint was not tested.

### Current code after the audit

Protocol v3 corrects the descriptor contract. Availability now requires validated
source that remains resident across the handoff. It distinguishes complete
symbols, incomplete previews, and bounded live file reads. Failed source reads
preserve their recovery hint and offer a bounded live-read candidate from the
observed path; stale line numbers remain navigation hints.

The focused gate passed **164 tests and 34 subtests**, including a mocked
post-edit recovery through handoff. An [offline check of both actual stale
observations](results/selected-context-2026-10-19/POST_AUDIT_FIX_CHECK.json) also
passes. **V3 has not been run against live models.** The table above reports the
frozen versions that were measured, and gives v3 no quality or token credit.

## Reproduction

Prepare fixtures without model calls:

```bash
/tmp/codemesh-edit-eval-venv/bin/python eval/selected_context_eval.py \
  --out eval/results/selected-context-new
```

Add `--run` to execute the nine model trajectories (three tasks × three
conditions). The run order is shuffled; the seed does not control model sampling.
Use a fresh output directory. Then generate the report:

```bash
/tmp/codemesh-edit-eval-venv/bin/python eval/report_selected_context.py \
  eval/results/selected-context-new
```

This is a bounded **single-session** protocol with a shared 20-call limit per
trajectory. The older three-phase matrix is a separate experiment and must not
be pooled with these outcomes. Any quality comparison here covers only three
seeded regressions, one repeat each, and their acceptance tests.
