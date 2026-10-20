# Controlled next iteration: contract, exact-match ranking, and multi-file handoffs

Implemented 2026-10-19. **The live pilot is complete; the main comparison is
incomplete.** As of 2026-10-20, the pilot graded 16 trajectories with 13 accepted;
the main comparison graded 20 of 120, with 20 accepted and 24 additional ungraded
infrastructure failures. See [the current results](NEXT_ITERATION_RESULTS.md).
Historical experiments remain evidence for hypotheses, not the control group
for this changed runner.

## Changes and controls

| Mechanism | Control | Opt-in treatment |
|---|---|---|
| Eval instructions/schema | `--tool-contract legacy` | `--tool-contract lean`: concise instructions; remove six unused filesystem schema fields |
| Exact-name search ties | `--search-tie-break pagerank` | `--search-tie-break path-depth`: shallower paths before popularity, only within equally ranked exact-name/leaf matches |
| Fresh-session source delivery | Hydration off | Existing `--hydrate-handoff`: verify saved references and reopen bounded live source in both arms |
| Workload | Three original single-file repair tasks | Two additional two-file workflows in `graph_workflow_tasks.json` |

The defaults remain legacy contract, PageRank ties, working-set context, and
hydration off. The decision-model pilot stays parked. This iteration uses one
reasoning model and deterministic retrieval/context rules.

The lean contract preserves every applicable action and schema constraint. The
CodeMesh schema was already minified and all its fields are used, so it remains
unchanged. The filesystem schema drops `node`, `relation`, `cursor`, `mode`,
`if_hash`, and `summary`. Both arms retain exact source checks, edit/test limits,
historical archive warnings, and current-source requirements. Some limits become
more explicit in the prose, so the treatment measures a revised contract, not
only byte serialization. The fixed provider protocol is unchanged.

### Client-agnostic ranking

The shared query handler exposes `exactTieBreak`. CLI:

```sh
codemesh explore search ByteSize --exact-tie-break path-depth --compact
```

MCP:

```json
{"action":"search","query":"ByteSize","exactTieBreak":"path-depth"}
```

Use that argument with `codemesh_explore`. Explicit IDs and `path` filters still
select deeper implementations. No directory name is blacklisted. Path depth is
a weak structural hint: a shallow module is not necessarily the intended one.
The eval fixes this option outside the model's action schema, so the model does
not spend a turn selecting the ranking policy.

Lean instructions and working-state replacement belong to the **eval harness**.
An MCP server cannot remove a client's earlier messages. Search ranking is
available through the product CLI and MCP; hydration was already available on
both surfaces.

## New workloads

Both fixtures use Pydantic at commit
`5adfeaaaea947f81f0f01868604f219084ec151a`.

| Task | Required repairs | Behavior checked |
|---|---|---|
| `alias_roundtrip_flow` | `aliases.py` and `_internal/_fields.py` | Separate validation/serialization aliases, precedence, common fallback, explicit aliases |
| `trusted_default_factory_flow` | `fields.py` and `_internal/_fields.py` | Trusted construction and explicit field-default resolution; supplied values, chained defaults, aliases, zero-argument factories, static defaults |

Prompts describe public behavior without revealing defect locations. Each
fixture passes pristine and fails with both defects, with only the first defect,
and with only the second defect. A one-file repair cannot pass hidden acceptance.
These are small connected workflows, not evidence about arbitrary large tasks.

The runner now seeds all mutation files, fingerprints all their contents for
index reuse, records mutation paths, and exports every edited file in the patch.
Original one-mutation manifests still work. The original three-phase workflow
remains: investigation → impact investigation → handoff → fresh editing session.

**Quality metric:** final hidden acceptance plus workflow completion are primary.
`first_patch_pass` retains its historical meaning: acceptance after the first
individual edit. It will normally be false on a two-file repair. The recorded
`first_correct_edit.edit_count` reports how many edits were needed to reach a
correct combined patch. Hidden test feedback is never sent to the model.

## Offline verification

For the planned bundled-read/working-set configuration, serialized static CLI
contract bytes are:

| Arm | Legacy | Lean | Reduction |
|---|---:|---:|---:|
| Filesystem | 3,781 | 3,073 | 18.7% |
| CodeMesh | 5,892 | 4,998 | 15.2% |

CodeMesh's additional overhead relative to filesystem shrinks from 2,111 to
1,925 bytes (8.8%). This does not establish the research's proposed 300–400-token
per-call saving. The completed pilot measures task-level tokens, but does not
establish a reliable improvement from the lean contract.

- [Fixture validation](results/controlled-next-2026-10-19/FIXTURE_VALIDATION.json):
  eight pristine/mutant configurations, 16 visible/hidden test runs; all expected
  outcomes. Base checkout unchanged.
- [CLI contract](results/controlled-next-2026-10-19/CLI_CONTRACT.json): real
  indexing, default/opt-in rank reversal, candidate-cache reuse across policies,
  explicit deeper path/ID selection, and invalid option rejection.
- [Static contract measurement](results/controlled-next-2026-10-19/CONTRACT_BYTES.json):
  captures serialized instruction/schema inputs through the Codex adapter with
  subprocess execution blocked. No model calls. These are CLI input bytes,
  not provider wire requests or measured token savings.
- The normal preparation path indexes and checks both multi-file mutants under
  `results/controlled-next-2026-10-19/prepare/indexes/`.
- [Prepared-workflow graph probe](results/controlled-next-2026-10-19/GRAPH_PROBE.json):
  `FieldInfo.get_default@L901` retrieves the shared default resolver in the same
  bounded `inspect` result. Search finds three declarations of `get_default`,
  so selecting the implementation remains a navigation step. The alias helper's
  receiver call to `AliasGenerator.generate_aliases` is unresolved by the current
  static resolver; it needs additional search/source retrieval. Keep this case
  to measure fallback overhead rather than assuming complete graph coverage.
- Build and regression gates: 274 TypeScript tests; 354 Python tests plus 34
  subtests. The final wording change was rechecked with all 39 contract and
  next-iteration integration tests.

Reproduce without a model:

```sh
npm run build
/tmp/codemesh-edit-eval-venv/bin/python eval/validate_graph_workflow.py \
  --python /tmp/codemesh-edit-eval-venv/bin/python \
  --out eval/results/controlled-next-2026-10-19/FIXTURE_VALIDATION.json
/tmp/codemesh-edit-eval-venv/bin/python eval/next_iteration_contract.py
/tmp/codemesh-edit-eval-venv/bin/python eval/measure_tool_contract.py
/tmp/codemesh-edit-eval-venv/bin/python eval/vnext_agent.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --tasks eval/graph_workflow_tasks.json \
  --python /tmp/codemesh-edit-eval-venv/bin/python \
  --out eval/results/controlled-next-2026-10-19/prepare
```

Use Node 22 with this checkout's installed native dependencies. Preparation and
static checks never start a model; the agent runner requires `--run` for that.

## Live comparison protocol

Use Codex CLI + **`gpt-5.6-terra`, low effort**, working-set retention, auto source,
16 KiB evidence, eight ledger entries, and bundled reads in every cell. Hydration
uses equal source-count/byte caps for both arms. Keep the same per-phase turn
limit across variants; start with 12, as in the previous Terra matrix.

| Cell | Contract | Exact tie-break | Hydration | Identifiable comparison |
|---|---|---|---|---|
| A | legacy | pagerank | off | Fresh baseline on the current build |
| B | lean | pagerank | off | B−A: contract effect |
| C | lean | path-depth | off | C−B: ranking effect under lean instructions |
| D | lean | path-depth | on | D−C: hydration effect under lean instructions and path-depth ranking |

Run filesystem and CodeMesh in every cell. Ranking does not apply to filesystem;
those repeated filesystem runs measure variability. This is an incremental
comparison, not a full factorial experiment; interactions with omitted
combinations cannot be separated.

Start with one repeat of both new tasks (16 trajectories across four cells and
two arms). If protocol and fixture gates hold, use three repeats on both the
original and new suites: **120 graded trajectories** in total. Keep smoke results
separate from the main analysis. Rotate cell order between repeat blocks and use
matched seeds; retain per-call input artifacts, attempts, and failed-call usage.
Report cold/first calls separately from warmed cache observations. Cache hits
from unrelated runs cannot be fully controlled through the CLI.

Example cell D command for a new-suite pilot, **only when starting live evals**:

```sh
/tmp/codemesh-edit-eval-venv/bin/python eval/vnext_agent.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --tasks eval/graph_workflow_tasks.json \
  --python /tmp/codemesh-edit-eval-venv/bin/python \
  --provider codex --model gpt-5.6-terra --effort low \
  --context-policy working-set --source-mode auto --bundle-reads \
  --tool-contract lean --search-tie-break path-depth --hydrate-handoff \
  --modes filesystem,codemesh --repeats 1 --max-turns 12 \
  --out eval/results/controlled-next-live/pilot-D --run
```

Change the three treatment flags per the table and use a distinct output
directory for every cell/block. For the continuity suite use
`--tasks eval/edit_workflow_tasks.json`. Run configurations include both new
controls; fingerprints cover the lean contract and all task definitions/tests.
Incompatible resumes are rejected before saved indexes are rebuilt.

Report final acceptance, completion, total/uncached/cached input, output, model
calls, elapsed model/tool time, source bytes, edits to first correct patch,
same-session versus cross-session rereads, hydration omissions, and observed
graph use. Inspect task/repeat pairs before aggregating. Promote a treatment only
if it preserves quality and improves measured task-level efficiency; smaller
source payloads or a passed offline contract alone are insufficient.

## Corrections to the research interpretation

The recommendations are useful hypotheses. The existing
[Terra diagnostics](results/graphify-terra-2026-10-19/DIAGNOSTICS.md) support narrower
claims than some of the pasted analysis:

- The 441-token difference was observed on initial calls. Multiplying it by all
  95 CodeMesh calls is not a measured causal overhead estimate.
- Extra investigation calls were factory +2 and ByteSize +4. Only one ByteSize
  repeat inspected legacy source; that detour does not explain all six calls.
- Of 13 repeated working-set CodeMesh source blocks in editing, 11 cross the
  session boundary and two repeat within that session. Hydration addresses the
  first category subject to freshness, reference and byte limits. It cannot
  guarantee elimination of all rereads.
- The cache discount calculations describe input-only scenarios. Working-set
  CodeMesh also generated 1,248 more output tokens. The CLI does not report USD
  cost; cache-write input was reported as zero on 381 of 382 journal calls, with
  one timed-out call's usage unknown. Do not claim a measured net dollar saving.
- Working-set alone reduced CodeMesh input by 10.6% against full history in the
  earlier Luna comparison; 23.4% required hydration too. Prompt layout evidence
  does not by itself establish that key ordering caused the cache outcome.

The pilot has tested these hypotheses once per task, cell and arm. The main
comparison remains incomplete; neither a reliable treatment improvement nor a
measured dollar-cost improvement has been established.
