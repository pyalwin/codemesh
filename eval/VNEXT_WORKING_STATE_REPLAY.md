# Working-state context replay

Date: 2026-10-18. Offline replay of both trajectories from the clean
`factory_validated_data` Luna reference pilot. No models, repository tools,
edits, or acceptance tests were executed by this replay.

## Measured result

| Saved trajectory | Model calls replayed | Append-only transcript bytes | Working-state transcript bytes | Reduction | Peak before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Filesystem | 15 | 168,931 | 133,856 | 20.76% | 24,875 → 19,400 |
| Codemesh | 17 | 163,429 | 111,385 | 31.85% | 21,061 → 11,287 |

These are actual UTF-8 byte lengths of the serialized transcript that would be
passed to Codex on stdin, summed across the saved calls. The fixed transcript
prefix is included. Static model instructions, the response schema, and hidden
provider framing are excluded. **These percentages are not token, cost,
latency, or quality improvements.**

Both arms benefit from context management. A future comparison must distinguish
that shared benefit from any additional benefit of Codemesh retrieval. The
model's decisions were held fixed here; a live model could choose different
actions after receiving a changed context.

## Instruction and schema overhead

The current working-state policy adds **631 instruction bytes per call** and
**82 schema bytes per structured decision** in either arm. The handoff call has
no response schema.

| Current local input file | Filesystem full → working-state | Codemesh full → working-state |
| --- | ---: | ---: |
| Instructions, bytes per call | 1,499 → 2,130 | 2,693 → 3,324 |
| Schema, bytes per structured decision | 1,043 → 1,125 | 1,092 → 1,174 |
| Additional bytes summed across saved calls | 10,613 | 12,039 |

Adding these current local file sizes to the replayed transcript sizes gives
**206,018 → 181,556 bytes (−11.87%)** for Filesystem and
**226,682 → 186,677 bytes (−17.65%)** for Codemesh. This accounts for the measured
policy overhead, but still does not measure the provider's actual prompt,
tokens, caching, or cost. The historical instruction/schema files were not
archived; these sizes compare the current full and working-state policies.
Their exact texts and fingerprints are saved under `protocol-inputs/`.

## What changed in the replay

The append-only side reconstructs the exact recorded user messages and provider
assistant text, including nullable structured-output fields. It clears history
at the original fresh-session boundary.

The working-state side receives the same tasks, decisions, and complete tool
observations. It keeps initial and current task instructions, bounded navigation
pointers, exact resident source/read evidence, recent model-authored hypotheses,
the last decision, the latest observation, and bounded successful patch records.
An archive index makes evicted navigation and source pointers discoverable.
A disk archive retains the
original observations. No model-generated compression is introduced.

The replay uses the live integration's lifecycle: update the task at phase
boundaries; append the handoff request to the rendered context; consume an
observation after the saved model decision; observe each recorded tool result;
and reset active state before the fresh editing phase. Rejected early actions
are preserved as harness feedback. Edit observations invalidate source residency
for the edited file.

The largest reductions occur after navigation has accumulated. Codemesh's
handoff request shrinks from 21,061 to 11,287 bytes. Its final request after the
post-edit grep and test shrinks from 11,237 to 6,149 bytes.

Short contexts can grow. Codemesh's first three editing-phase requests, through
the correct edit decision, total **10,611 → 13,416 bytes (+26.4%)**. Working-state
formatting and navigation metadata add overhead before much history exists.
This is visible in the full per-call table below.

## Safety validation

All 32 saved call snapshots passed checks that the initial task remains present,
every navigation-ledger handle has an archive record, and every authorized
source hash corresponds to exact source bytes actually present in the rendered
context. Both session resets cleared source residency; both edit observations
invalidated the edited file; both final states retained the observed passing
test result and the exact successful patch.

Ten additional offline probes passed:

- Initial task and current phase remain distinct and available.
- Evicting source removes its conditional-read authorization.
- Restoring archived source is rejected; a fresh read is required.
- Editing invalidates source and marks affected navigation pointers stale.
- Navigation cursors survive consumption, and archived navigation restores exactly.
- Cross-session restoration is marked as historical navigation and grants no source residency.
- Test results survive consumption but become outdated after another edit.
- An oversized source result displays once without acquiring persistent hash authorization.
- The paginated archive index exposes source pointers without code or hash authorization.
- The last four patch records and omitted-edit count survive later test and answer observations.

The evidence budget bounds retained evidence, not every byte of a model request.
Task text, navigation metadata, and the latest complete observation also occupy
context. The checks establish these state-management properties; they do not
establish that the model will preserve repair quality.

## Per-call serialized transcript bytes

Call numbers follow each original trajectory. The handoff request runs in the
investigation context even though its recorded phase is 2. The following source
or grep call begins the fresh editing context.

| Codemesh call | Phase | Requested action | Append-only bytes | Working-state bytes |
| ---: | ---: | --- | ---: | ---: |
| 1 | 0 | map | 386 | 859 |
| 2 | 0 | map | 1,455 | 2,335 |
| 3 | 0 | search | 4,930 | 5,283 |
| 4 | 0 | search | 7,211 | 4,944 |
| 5 | 0 | source | 10,195 | 6,585 |
| 6 | 0 | answer | 11,975 | 5,800 |
| 7 | 1 | search | 12,888 | 6,993 |
| 8 | 1 | search | 16,038 | 10,513 |
| 9 | 1 | source | 18,458 | 10,559 |
| 10 | 1 | answer | 20,274 | 10,351 |
| 11 | 2 | handoff | 21,061 | 11,287 |
| 12 | 2 | source | 1,733 | 2,298 |
| 13 | 2 | source | 3,549 | 4,544 |
| 14 | 2 | edit | 5,329 | 6,574 |
| 15 | 2 | grep | 5,962 | 5,864 |
| 16 | 2 | test | 10,748 | 10,447 |
| 17 | 2 | answer | 11,237 | 6,149 |

| Filesystem call | Phase | Requested action | Append-only bytes | Working-state bytes |
| ---: | ---: | --- | ---: | ---: |
| 1 | 0 | grep | 386 | 859 |
| 2 | 0 | read | 5,247 | 6,209 |
| 3 | 0 | read | 5,685 | 2,122 |
| 4 | 0 | answer | 13,952 | 10,156 |
| 5 | 1 | read | 14,870 | 11,430 |
| 6 | 1 | grep | 18,476 | 14,738 |
| 7 | 1 | read | 20,641 | 17,258 |
| 8 | 1 | edit (rejected before patch phase) | 23,381 | 18,097 |
| 9 | 1 | answer | 23,991 | 18,678 |
| 10 | 2 | handoff | 24,875 | 19,400 |
| 11 | 2 | grep | 973 | 1,448 |
| 12 | 2 | read | 1,954 | 2,828 |
| 13 | 2 | edit | 4,199 | 4,356 |
| 14 | 2 | test | 4,916 | 3,291 |
| 15 | 2 | answer | 5,385 | 2,986 |

## Reproduction and artifacts

```sh
/tmp/codemesh-edit-eval-venv/bin/python eval/replay_working_state.py \
  --report eval/results/vnext-luna-reference-pilot/report.json \
  --out eval/results/vnext-working-state-replay
```

The command refuses to overwrite existing replay artifacts. Use a new output
directory to repeat it.

- [Replay implementation](replay_working_state.py)
- [Machine-readable result and implementation fingerprint](results/vnext-working-state-replay/report.json)
- [Codemesh complete before/after prompt snapshots](results/vnext-working-state-replay/factory_validated_data-codemesh-1/snapshots.jsonl)
- [Filesystem complete before/after prompt snapshots](results/vnext-working-state-replay/factory_validated_data-filesystem-1/snapshots.jsonl)
- [Original clean pilot](results/vnext-luna-reference-pilot/report.json)

## Separate retrieval follow-up

Search behavior was left unchanged to isolate context policy. The clean trace
still exposes two retrieval gaps: whole-name exact matching prioritizes test
helpers named `get_default` over qualified methods, and OR matching admits
generic wrapper candidates for a multiword query. Directory navigation does not
scope subsequent global searches.

A separate bounded experiment can evaluate leaf-identifier matching and explicit
path scope against held-out standalone-function, test, qualified-method, and
legacy-code queries. Avoid blanket penalties for tests or legacy directories.
Do not combine ranking changes with the first live context-policy comparison.
