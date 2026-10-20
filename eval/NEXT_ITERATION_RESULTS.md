# Controlled next iteration: live results snapshot

Status as of **2026-10-20**. The pilot is complete. The separate 120-trajectory
main comparison is incomplete after repeated connection failures and machine
sleep interruptions. No treatment is promoted from these results.

## Experiment

The requested model is **Codex CLI + `gpt-5.6-terra`, low effort**. Each cell uses
working-set retention, bundled reads, a 12-call limit per phase, and matched
filesystem and CodeMesh arms. The two pilot tasks each require repairs in two
files in Pydantic at commit `5adfeaaaea947f81f0f01868604f219084ec151a`.

| Cell | Contract | Exact-name ties | Handoff hydration |
|---|---|---|---|
| A | legacy | PageRank | off |
| B | lean | PageRank | off |
| C | lean | path depth | off |
| D | lean | path depth | on |

The main comparison adds the three earlier repair tasks and uses three repeat
blocks. The pilot is excluded from main-comparison statistics. Controls, source,
task definitions and run settings were frozen before execution. See
[the implementation and protocol](NEXT_ITERATION.md).

## Completed pilot

**16/16 trajectories graded; 13/16 passed hidden acceptance and completed the
workflow; 274 model calls.** The three failed trajectories reached their turn
limits and remain in the results. There were no infrastructure-error attempts
in this pilot. The completed input-integrity audit passed.

| Cell | Filesystem accepted | CodeMesh accepted | Combined |
|---|---:|---:|---:|
| A | 2/2 | 2/2 | 4/4 (100%) |
| B | 2/2 | 2/2 | 4/4 (100%) |
| C | 1/2 | 1/2 | 2/4 (50%) |
| D | 2/2 | 1/2 | 3/4 (75%) |

These are one-repeat observations across only two distinct tasks:

- In A, CodeMesh used **24.6% fewer total input tokens** than filesystem, with
  equal acceptance, but **9.2% more summed model time**. Input includes cached
  tokens; model time is not wall-clock duration.
- The lean CodeMesh B used **21.9% more input than CodeMesh A**, with equal
  acceptance. Smaller static instructions did not ensure fewer total tokens:
  the B default-factory run separately retrieved related source and repeated
  five unchanged source blocks during editing. A retrieved the implementation
  and resolver together with `inspect`.
- CodeMesh C and D exhausted impact-investigation turns on the alias task
  without reaching editing. They repeatedly used symbol search for body text.
  Their last inputs still contained the defect-bearing source, original task
  and current phase request. This does not support a claim that compaction
  discarded the required evidence. D never reached hydration on that task.
- The path-depth option changed exact-match ordering as intended. Every
  CodeMesh cell still chose the same default-factory implementation, so this
  pilot does not demonstrate an avoided navigation detour.
- C's filesystem failure was a separate phase-gate failure: repeated edit
  attempts during investigation exhausted the turn budget.
- D used **11.4% fewer editing-phase input tokens** than C on the CodeMesh
  default-factory task, but **1.7% more whole-task input**. Filesystem hydration
  sometimes spent its four-source cap on overlapping or earlier broad reads,
  omitting later references to the actual repair code.
- Only A used `inspect`; no pilot run used `map`. The pilot therefore does not
  establish adoption of the intended map → branch → symbol workflow.

The lower token counts of unfinished trajectories are not successful savings.
The pilot motivates scoped literal-search fallback, detection of repeated
unchanged reads, and deduplication/prioritization of handoff references. Those
would be separate treatments after this frozen comparison.

## Incomplete main comparison

| Measure | Current snapshot |
|---|---:|
| Planned trajectories | 120 |
| Graded trajectories | 20 |
| Accepted and workflow complete | 20 |
| Additional ungraded infrastructure-failure attempts | 24 |
| Journaled model calls, including failed attempts | 481 |

Only part of the first repeat block has completed; blocks two and three have
not run. The 20/20 observed acceptance is **not** a completed-suite acceptance
estimate. Coverage is uneven, and no main-comparison token or quality conclusion
is reported.

Failures include connection refusal, DNS failure and subprocess timeouts. Saved
system logs show repeated sleep cycles during execution; this is consistent
with disrupted connectivity but does not prove the cause of every failure.
Attempts and completed trajectories are retained for resume under the same
frozen settings. Calls without usage remain unknown rather than zero.

## Evidence and limits

The CLI does not expose billed USD, so **there is no measured dollar-cost
result**. Concurrent cache warming and model sampling limit causal claims about
cache or latency differences. Captured arguments and catalogs request Terra;
provider-resolved model IDs are unavailable. A recurring startup warning about
Luna metadata is preserved in the audit and does not by itself establish a
model swap.

Raw evidence is local under the gitignored directory
`eval/results/controlled-next-live-2026-10-19/`: `matrix.json`, `REPORT.md`,
`summary.json`, `VALIDATION-pilot.json`, `pilot/FINDINGS.md`, `INCIDENTS.md`, exact
call inputs, observations and attempt journals. These files are **not included
in the Git commit**. This document preserves the concise status and findings
without publishing those traces.

The frozen runner's patch filenames can be overwritten by a retry of the same
task/arm/repeat. Attempt-specific edit journals remain available; the audit can
reconstruct older patches and labels them as reconstructed rather than original
artifacts.
