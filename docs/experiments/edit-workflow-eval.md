# Held-out repair workflow pilot on Pydantic

## Question and protocol

Can transparent routing of familiar `grep` and `read` actions, source-backed
capsules, or both help a small model repair code with less work and equal
quality across an investigation and fresh-session handoff?

The target was open-source Pydantic at commit
`5adfeaaaea947f81f0f01868604f219084ec151a`. Claude Haiku
(`claude-haiku-4-5-20251001`) repaired three seeded regressions, with two
repeats per arm: kebab-case alias conversion, negative `ByteSize` formatting,
and a default factory that requires validated data. The agent saw a symptom
and one visible regression test. Independent hidden tests covered nearby
behavior and were never returned to the agent. Each run had its own worktree
with the same one-line seeded bug.
Fixture validation confirmed each hidden test passed on the pristine checkout
and both visible and hidden tests failed on its seeded mutant.

Each trajectory had two source-investigation phases, a model-written handoff
of at most 120 words, then a fresh session to patch and optionally run the
visible test. The harness checked hidden tests after the first edit and at
the end. There were at most 12 decisions per phase and a $1.00 limit per
`claude` invocation. Modes ran in shuffled order. All agent file access was
through a structured *simulated* action protocol; Claude Code's built-in
tools, MCP dispatch, and production hooks were not used in these runs.

| Arm | Search and read behavior |
| --- | --- |
| Filesystem | Literal native `rg` and numbered source slices |
| Capsule v2-style | Same search; targeted reads can return live AST/docstring capsules, with `read_raw` fallback |
| Routed | Familiar `grep` and `read` actions routed through SHA-checked Codemesh evidence where applicable, native fallback otherwise |
| Combined | Routed search plus capsule reads and routed raw fallback |

The capsule overview in this pilot came from the live docstring, not the
earlier cached model-authored v2 overview. Routed grep preserved native match
lines and required index freshness and search parity. The router still ran
native search as a shadow check, so no search CPU saving is claimed.

## Results: all 24 attempted trajectories

"Patch pass" counts a hidden-test-passing final worktree, including runs that
later hit the turn cap before writing a final response. "Complete" requires
all three phases and a final answer. Every submitted first edit passed the
hidden tests; the failures below came from no edit, an unfinished final phase,
or a CLI response with no structured output.

| Metric (six attempts per arm) | Filesystem | Capsule | Routed | Combined |
| --- | ---: | ---: | ---: | ---: |
| Hidden patch pass | 4/6 | 5/6 | 5/6 | 6/6 |
| Complete trajectory | 4/6 | 3/6 | 5/6 | 6/6 |
| Nonempty handoff memory | 3/6 | 2/6 | 4/6 | 5/6 |
| Tool calls | 82 | 120 | 80 | 106 |
| Source reads | 47 | 72 | 44 | 65 |
| Read observation bytes | 88,377 | 108,248 | 71,823 | 85,222 |
| Exact repeated source lines | 112 | 541 | 59 | 252 |
| Total input tokens, including cache reads | 1,896,987 | 2,204,556 | 2,086,160 | 2,914,078 |
| Reported model cost | $0.9429 | $1.1599 | $1.0573 | $1.2143 |
| Summed model-call wall time | 579.5 s | 715.8 s | 618.3 s | 686.1 s |

Relative to filesystem, routed used **6.4% fewer source reads** and returned
**18.7% fewer read bytes**, but used **10.0% more input tokens** and cost
**12.1% more**, with 6.7% more model-call wall time. Combined passed all six
repairs and completed all six runs, but used **38.3% more reads**, **53.6%
more input tokens**, and cost **28.8% more**. Capsule-only used **53.2% more
reads** and cost **23.0% more** while completing half its trajectories. These
are descriptive totals from six
attempts per arm, not stable estimates of a treatment effect.

| Task (two attempts per arm) | Filesystem | Capsule | Routed | Combined |
| --- | ---: | ---: | ---: | ---: |
| Kebab-case alias: hidden patch pass | 1/2 | 2/2 | 2/2 | 2/2 |
| Negative `ByteSize`: hidden patch pass | 2/2 | 2/2 | 2/2 | 2/2 |
| Validated-data factory: hidden patch pass | 1/2 | 1/2 | 1/2 | 2/2 |

The 24 trajectories yielded 18 complete runs, five turn-cap runs, and one
CLI structured-output error. Of the five turn-cap runs, two already had
hidden-test-passing patches. The single structured-output error occurred in
the filesystem kebab-case patch phase before an edit. All 20 runs that made an
edit passed the hidden tests on the first edit. Several negative-byte patches
changed more lines than the one-line repair, although their tested behavior
passed.

## Handoff and token findings

The fresh-session handoff was unreliable in every arm. Seven attempted
handoffs returned no visible text from `claude`, and three trajectories did
not reach the handoff. A source spot-check found three concrete inaccuracies
among the nonempty memories: one routed memory said a patch had already been
applied before the edit phase, and two kebab-case memories described the
existing 24-case parametrized test as 25 or 26 cases. Therefore, nonempty
memory is a delivery metric, not an accuracy score.

The patch prompt repeated the full symptom, and the new session retained
search/read access. Some runs repaired the bug despite an empty handoff. This
pilot consequently tests routing and repair under a handoff workflow, but it
does **not** prove that persistent memory improved long-running work. The
three seeded defects are also small and localized; they do not represent a
broad production issue across a large codebase.

Read-output reduction did not translate into total-token or cost reduction.
The provider reported different mixes of uncached, cache-creation, and
cache-read tokens by run. For example, routed used 71,823 read bytes versus
88,377 for filesystem, while its cache-read input was 1,740,387 tokens
versus 1,591,508. Model calls and session behavior dominate the measured
cost here. The prebuilt Pydantic index was about 171 MB. Initial indexing and
pytest CPU cost were excluded from the USD figures; index cost would need
amortization across repeated tasks.

## Integration finding raised during this experiment

The production `hooks/pre-read.sh` and `hooks/post-read.sh` originally echoed
plain stdout, while the README showed lowercase hook event names and an
invalid settings shape. Anthropic's current
[Hooks reference](https://code.claude.com/docs/en/hooks) says plain stdout
from `PreToolUse`/`PostToolUse` is not supplied as model context; JSON
`hookSpecificOutput.additionalContext` is. The scripts and install example
were corrected separately from this benchmark. A local SQLite fixture
verified valid JSON output from both scripts and suppression of a summary marked stale.
A live CLI smoke invoked the hook but exhausted its budget without
a model answer, so model uptake of the hook remains unverified.

The same reference supports `PreToolUse.updatedInput` and
`PostToolUse.updatedToolOutput`, but built-in tool output replacements must
match the tool's structured result shape. A `permissionDecision: "deny"`
reason reaches Claude, but an answer in that reason has not been measured
here as a zero-retry replacement for Grep. Hook interception needs its own
live arm with actual Claude Code tools and a recorded activation rate.

## Interpretation and next test

This batch does not establish a cost saving. Routed grep/read reduced the
amount of source returned but did not reduce total paid tokens or cost.
Combined had the best observed completion and patch acceptance, but it was
also the most expensive arm. The 6-attempt quality differences are too small
and confounded by turn limits and one CLI failure to claim a reliable win.

The next test should target the product hypothesis more directly: use
multi-file issues where the post-handoff prompt omits the earlier source
findings, require the fresh agent to recover them from its own memory or a
shared Codemesh store, and run a separate live-hook arm that intercepts
actual native `Grep`/`Read`. Record hook activation, fallback/retry behavior,
memory accuracy, hidden patch pass, total model cost, and index amortization.

Raw trajectories, model usage, hidden-test results, handoff text, and diffs
are in `eval/results/edit-workflow-v2/`. Reproduction requires the pinned
Pydantic checkout with a prebuilt Codemesh index and a Python 3.11 virtual
environment containing pytest and Pydantic's runtime dependencies. The
runner's default venv path is `/tmp/codemesh-edit-eval-venv`; override it with
`--python` if needed. Then run:

```bash
python3.11 eval/edit_workflow_eval.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --out eval/results/edit-workflow-v2 \
  --modes filesystem,capsule_v2,routed,combined \
  --repeats 2 --budget-usd 1.0 --resume
```
