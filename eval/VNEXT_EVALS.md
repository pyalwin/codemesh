# vNext evaluations

The [controlled next iteration](NEXT_ITERATION.md) adds opt-in lean instructions,
exact-match path-depth ranking, and two validated multi-file workflows for the
next Terra comparison. Its offline checks are separate from live model results.

The latest [Terra Graphify follow-up](results/graphify-terra-2026-10-19/REPORT.md)
completed 36 trajectories comparing filesystem/CodeMesh and working-set/focused
retention. See the [findings](results/graphify-terra-2026-10-19/FINDINGS.md) and
[input-integrity audit](results/graphify-terra-2026-10-19/VALIDATION.json).

The completed 90-trajectory working-state, hydration, and bundled-read comparison
is in [VNEXT_ABLATION_RESULTS.md](VNEXT_ABLATION_RESULTS.md).

These are new evaluations for the SQLite-only, client-agnostic CLI. Historical
capsule, map, and routed-hook results remain in `docs/experiments/`; they are not
release targets for this version.

The historical Claude/Haiku run is in [`VNEXT_RESULTS.md`](VNEXT_RESULTS.md).
The current runner defaults to **Codex CLI + `gpt-5.6-terra`**, with a fresh filesystem
baseline. The [retrieval reset](../docs/experiments/vnext-retrieval-reset.md) explains
the corrected diagnosis and acceptance gates. Historical runs are preserved.
The first completed paired Luna pilot is in [VNEXT_LUNA_PILOT.md](VNEXT_LUNA_PILOT.md).
That pilot is now marked diagnostic because stderr exposed blocked internal
router calls missed by its original parser. See the [router audit](../docs/experiments/luna-router-audit.md).
The corrected paired rerun is in [VNEXT_REFERENCE_PILOT.md](VNEXT_REFERENCE_PILOT.md).
The complete [interaction review](VNEXT_REFERENCE_TRACE.md) shows why source savings
did not translate into whole-task token savings. New Codex runs default to a
deterministic working state; `--context-policy focused` selects the new optional
stable-prefix policy. Historical runs remain unchanged. See the
[Graphify follow-up](../docs/experiments/graphify-deterministic.md) for the current
retrieval and context changes. The decision-model pilot is parked.

## Target and setup

Use the standard open-source Pydantic checkout pinned at
`5adfeaaaea947f81f0f01868604f219084ec151a`. The scripts check the commit and
create disposable Git worktrees; they leave the supplied checkout alone.

```sh
git clone https://github.com/pydantic/pydantic.git /tmp/codemesh-pydantic-benchmark
git -C /tmp/codemesh-pydantic-benchmark checkout --detach 5adfeaaaea947f81f0f01868604f219084ec151a
npm run build
python3 eval/vnext_contract.py --repo /tmp/codemesh-pydantic-benchmark
```

For the repair tasks, create a Python 3.11 environment with Pydantic and pytest:

```sh
python3.11 -m venv /tmp/codemesh-edit-eval-venv
/tmp/codemesh-edit-eval-venv/bin/pip install -e /tmp/codemesh-pydantic-benchmark pytest
```

The contract report is `eval/results/vnext-contract.json` by default. It checks
four-level map navigation, stable pagination, exact-ID search, byte-exact source,
fold/preview/full output, conditional reads, cross-session rehydration, persistent
search-cache reuse and invalidation, durable note recall, stale-source refusal,
and note survival through reindexing. It also
records index and call latency, delivered/avoided source bytes, and delivery
records. A passed contract is **correctness evidence**, not a model quality or
token-saving claim.

## Agent workflow comparison

The agent eval uses the existing three Pydantic repair fixtures in
`eval/edit_workflow_tasks.json`. Each has a visible test and a hidden acceptance
test. Preparation proves the pristine checkout passes and the seeded bug fails,
then indexes that exact mutant. It does not call a model until `--run` is passed.

```sh
python3 eval/vnext_agent.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --python /tmp/codemesh-edit-eval-venv/bin/python \
  --out eval/results/vnext-agent-working-state

python3 eval/vnext_agent.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --python /tmp/codemesh-edit-eval-venv/bin/python \
  --out eval/results/vnext-agent-working-state \
  --run --provider codex --model gpt-5.6-terra --repeats 3 --resume
```

Both arms get the same symptom, investigation questions, edit protocol, visible
test, hidden judge, model, and turn limit. The filesystem arm can use literal
grep and numbered reads. The Codemesh arm is told to use map/search/source and
can fall back to the same grep/read. Two investigation phases precede a fresh
editing session. A model writes a short handoff: the filesystem arm receives it
as text, while the Codemesh arm stores it as a revision-bound note and the
harness restores it automatically with freshness status at the session boundary.
Successful source observations also contribute up to eight exact references
(`id`, `path`, `lines`, view `hash`, and `mode`). These are persisted with the
narrative in a versioned JSON payload in the existing SQLite note. Recorded file
hashes are checked again at restoration. Changed/unavailable files lose their
saved spans and view hashes. The new context must read source again; old hashes
never authorize conditional source suppression. `--no-handoff-references` provides
a narrative-only ablation while retaining the same runner and retrieval code.
The Codemesh arm requests compact CLI map/search/source/recall responses. Source
defaults to the explicit bounded `auto` mode; the model can request exact `full`
source for edits. Use `--source-mode full` for a separate navigation ablation. This tests
whether durable retrieval pays for its extra calls while
preserving a comparable memory signal. Tool actions are simulated by the runner,
so CLI use is deterministic; this does not measure whether an unprompted agent
would choose the custom tool. Hidden test results are never sent to the model.

Codex calls use ephemeral sessions with harness-owned context supplied on each
decision. `--context-policy working-set` is the default for both arms;
`--context-policy focused` selects the new compact navigation policy and
`--context-policy full` retains the earlier complete-history policy. Phases 0 and
1 share active state; phase 2 resets it to the saved handoff and patch request.
Native tools and user integrations are
disabled by the adapter; any unexpected native tool event or runtime router
error in stderr invalidates the call. Both specific code-mode rejections and
other router errors are counted per call and in all-attempt report totals.
Counts are observed rejection diagnostics, not inferred retries or token deductions.
The model operates in an empty temporary directory; only the harness executes
the selected JSON action. Both arms use this same protocol. This is a controlled
CLI retrieval experiment, not a measurement of default Codex tool adoption.

### Deterministic working state

`focused_state.py` extends the original `working_state.py` lifecycle with stable
task/evidence ordering, duplicate navigation suppression, compact root orientation,
and recoverable pending-page pointers. It never interprets choosing a branch as
proof that other branches are irrelevant. It uses no decision model. The older
`working-set` implementation remains available for controlled comparisons.

The provider-independent `working_state.py` keeps initial task constraints,
current phase, a recent navigation ledger, exact resident source/read evidence,
model-authored phase conclusions marked as hypotheses, patch records, and test/error
status. It uses no summarization model. The latest tool observation is delivered
verbatim once; subsequent calls keep pointers and active evidence rather than
the entire navigation transcript. Previous decisions omit irrelevant null fields.

`--evidence-bytes` defaults to 16 KiB and `--ledger-entries` to eight. The ledger
also has an 8 KiB byte bound. Exact source is evicted as a whole, never silently
truncated into purported complete code. The latest raw response can exceed the
retained-evidence budget so explicit full-source requests remain usable; these
are retention limits, not a hard cap on the complete prompt.

All observations are archived under `observations/<attempt>/`. The additional
harness action `restore(handle)` retrieves historical map/search/grep output;
`restore(handle="archive:0")` pages through older handles and source pointers.
Restored navigation has session/revision provenance. Archived source/read bodies
cannot be restored as current evidence: request live source/read instead. A map
cursor from an earlier revision requires fresh navigation. Eviction, editing,
and session reset revoke the affected source's conditional-read eligibility.
Same-session rereads after eviction have their own metrics and are not counted
as repeats of evidence still resident in the prompt.

This first integration controls the evaluation harness's prompt construction.
The CodeMesh CLI/MCP retrieval surface remains available as before. Product
clients must cooperate with context replacement; a tool response cannot remove
prior messages from a client's history by itself. Claude's current resumptive
adapter supports only `full` and rejects explicit `working-set` requests.

To isolate context management, run both policies with the same current build,
tasks, model, seed, and repeats, using separate output directories:

```sh
python3 eval/vnext_agent.py --repo /tmp/codemesh-pydantic-benchmark \
  --context-policy full --out eval/results/vnext-context-full --run
python3 eval/vnext_agent.py --repo /tmp/codemesh-pydantic-benchmark \
  --context-policy working-set --out eval/results/vnext-context-working --run
```

Each command includes both filesystem and CodeMesh arms. Do not use an old pilot
as the full-history control for the new protocol. The offline
[working-state replay](VNEXT_WORKING_STATE_REPLAY.md) measures serialized transcript
bytes under the same recorded decisions; it cannot establish token savings,
changed agent behavior, cache economics, or acceptance quality. Replacement can
change cached prefixes, so compare total/cached/uncached usage in the live runs.
See [official OpenAI prompt-caching documentation](https://developers.openai.com/api/docs/guides/prompt-caching).

The controlled runner uses the checked-in `codex_luna_catalog.json` through a
per-invocation `model_catalog_json` override: same `gpt-6-luna` model, `direct`
tool mode, disabled shell/patch capabilities, and no experimental tools. This
removes the model metadata's `code_mode_only` selection from the decision-only
protocol. The adapter records the effective catalog hash and still rejects any
observed native tool or router activity. Other Codex model names require an
explicitly reviewed catalog entry before use. This configuration uses the
[documented startup catalog override](https://developers.openai.com/codex/config-schema.json).

The output directory's `runs.jsonl` is an append-only trajectory checkpoint;
`calls.jsonl` journals raw usage and model events so interrupted attempts retain diagnostics. `report.json`
contains paired aggregates; `patches/` holds the diffs. Primary quality metrics
are first-patch and final hidden-test pass rates. Efficiency metrics include
provider-reported USD when available, input/cache/output tokens, source-observation bytes, native and Codemesh
tool calls, and elapsed model/tool time. Report pass rates and cost **per arm and
per task**; calculate percentage differences only after paired runs complete.
Codex CLI does not report billed USD: its cost fields remain `null`, never zero.
Cached input and reported cache writes are subsets of total input and are not
counted twice. If a CLI version omits cache writes, creation and uncached breakdowns
remain unknown. Raw counters are retained. Both within-context
repeats and reads repeated after a fresh session are reported separately.
`first_correct_edit` records calls and bytes delivered before the first patch that
passes hidden tests; those judge results are never shown to the model.
Every Codex decision archives exact stdin, instructions, schema, scoped catalog,
and invocation metadata under `inputs/<attempt>/call-NNN/`. Paths and hashes are
recorded in `calls.jsonl`. Conflicting or unwritable input artifacts fail before
the model is invoked. These are harness inputs, not the final provider HTTP request.
`sent_transcript_bytes` measures delivered transcript text; the accompanying
`full_history_transcript_bytes` is a same-actions serialization comparison, not
a separate baseline trajectory or a token estimate.

Index and pytest CPU time are separate from model spend. Use `--task ID` for a
pilot, `--repeats 3` or more for a comparison, and `--resume` after interruption.
Runner or provider errors remain in `runs.jsonl`, but are excluded from graded
trajectories and retried by `--resume`. The runner stops on model or harness errors.
Resume verifies CLI version, provider, model, effort, task/hidden-test content,
harness code, scoped model catalog, build, and protocol settings before preparing fixtures.

Claude remains available via `--provider claude --model haiku` in a separate output
directory. Its `--budget-usd` is a per-call ceiling (default $1). Codex has no CLI
USD ceiling; it uses the same turn cap and a 180-second per-call timeout. Do not
compare a Luna treatment with the archived Haiku baseline.

For a no-model SQLite roundtrip check of symbol fallback and structured handoffs:

```sh
/tmp/codemesh-edit-eval-venv/bin/python eval/vnext_handoff_contract.py --help
```

The latest [handoff contract report](results/vnext-handoff-contract.json) has nine
checks, including overload candidates, canonical IDs, exact source, persisted
references, and independently changed/deleted referenced files. Observed source
errors have their own byte counter; they are excluded from successful source bytes.

## Independent hydration and bundled-read experiments

Two additional features are **off by default**, independently selectable with
either context policy:

- `--hydrate-handoff`: after reset, read live source for saved references before
  the first patch decision. CodeMesh retrieves its persisted SQLite checkpoint
  and calls the public `explore hydrate` command. The filesystem arm saves and
  reloads exact span references from a JSON checkpoint and verifies their whole
  file and span hashes. Both receive at most four source blocks within a 24 KiB
  JSON budget. Changed, missing, stale, and oversized references supply no old
  source bytes. Hydration is recorded as an automatic tool action, including
  tool time, bytes, and cross-context repeated-source counts.
- `--bundle-reads`: expose public `explore inspect` in the CodeMesh arm and
  `batch_read(reads=[{path,start,limit},...])` in both arms. This gives the
  filesystem control a way to fetch several known spans in one model turn.
  Both operations have the same four-block/24 KiB response limit; filesystem
  spans keep the existing 160-line limit. Inspect includes exact root source
  and one hop of conservatively resolved local Python calls. Unresolved and
  omitted calls remain explicit. Other-language graph edges are unverified
  pointers, and overloads require an exact ID.

Hydrated and inspected code enters the same bounded evidence store as ordinary
source reads. It does not become permanent task text. Editing a file invalidates
its active evidence. A returned source hash authorizes conditional reads only
while the corresponding bytes remain resident. Archived bundles require a new
live read. Previews without a full-body freshness proof are not saved as source
references. Standalone `hydrate` and `inspect` are available through MCP and CLI;
automatic resume injection and transcript replacement remain client duties.

Use the same current build, model, tasks, repeats, and seed across these runs:

| Run | Context policy | Extra flags |
| --- | --- | --- |
| Control | `full` | none |
| Compact state only | `working-set` | none |
| Hydration only above compact state | `working-set` | `--hydrate-handoff` |
| Bundled reads only above compact state | `working-set` | `--bundle-reads` |
| Combined | `working-set` | `--hydrate-handoff --bundle-reads` |

Each command runs both arms; use separate output directories. For example:

```sh
/tmp/codemesh-edit-eval-venv/bin/python eval/vnext_agent.py \
  --repo /tmp/codemesh-pydantic-benchmark --context-policy working-set \
  --hydrate-handoff --bundle-reads --task factory_validated_data --repeats 1 \
  --out eval/results/vnext-bundled-pilot --run
```

Without `--run`, the runner only prepares fixtures. Run the CLI contract without
models using `eval/vnext_bundle_contract.py --repo /tmp/codemesh-pydantic-benchmark`.
The scripted integration tests verify actual context contents and invalidation;
they do not establish live token, cost, latency, or quality improvements.

Repeat-read counters include bundle actions; separate repeated-source-block
counters count each matching block. Byte counts use exact source text where
parseable, rather than JSON envelopes.
Compare freshly generated runs under this protocol; older archived repeat
counters used whole-observation bytes.

## Interpretation rules

- Do not call fewer source bytes a token reduction; read actual model usage.
- Do not claim better quality from a correct source excerpt; use hidden tests.
- Treat a note as a clue. The stale-source and stale-note contract must pass.
- Report model/session errors and fallback reads; never discard failed runs.
- Keep the repository revision, model, task order seed, turn cap, and budget in
the report. Avoid comparing these numbers directly to archived experiments,
  which used different retrieval contracts.

## Delegated retrieval and selective retention pilot

The [selected-context pilot](SELECTED_CONTEXT_PILOT.md) is a separate,
single-session experiment using Luna for navigation/retention and Terra for
reasoning/editing, with Terra controls. Twelve recorded trajectories include
both model roles and failed outcomes. The measured versions did not show token
savings; the report distinguishes patch acceptance from workflow completion and
documents the descriptor-contract issue found in the traces. Do not pool this
pilot with the earlier three-phase matrix.
