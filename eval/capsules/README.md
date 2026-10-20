# Versioned code capsule experiment

This experiment compares three representations of the **same selected Pydantic code units**:

1. `raw`: complete source of each selected function.
2. `capsule`: source version, location, signature, short purpose, and observed calls/raises.
3. `compact`: the same capsule facts in a terse notation with a legend.

The questions and code units are fixed in `pydantic_tasks.json`. This isolates representation quality from retrieval quality. Two questions reuse the same model-initialization functions to expose repeated-context cost. No Codemesh index or retrieval result is used to construct these prompts.

`pydantic_task_matrix.json` extends this to nine questions: three independent paths, a three-step JSON Schema path, and three phrasings of one initialization path. Each task has a rubric and simple lexical flags. The flags only identify answers for review; they are not correctness scores.

## Reproduce

Python 3.11+ is required. Pin the open-source target:

```bash
git clone --depth 1 https://github.com/pydantic/pydantic.git /tmp/codemesh-pydantic-benchmark
git -C /tmp/codemesh-pydantic-benchmark fetch --depth 1 origin 5adfeaaaea947f81f0f01868604f219084ec151a
git -C /tmp/codemesh-pydantic-benchmark checkout --detach FETCH_HEAD
```

Prepare prompts and byte counts:

```bash
python3.11 eval/capsules/experiment.py --repo /tmp/codemesh-pydantic-benchmark
```

Install optional `tiktoken` to also get an `o200k_base` token **proxy**. It is not the Claude tokenizer. For actual model usage and answers, sign in to the Claude CLI, then run:

```bash
claude auth login
python3.11 eval/capsules/experiment.py --repo /tmp/codemesh-pydantic-benchmark --summarize --run --model haiku
```

`--summarize` makes one model call for each unique changed code unit and caches the result in `eval/results/capsules-pydantic/summaries.json` using the source SHA-256. Later runs reuse unchanged summaries. Without that flag, capsules use the first substantive sentence of the source docstring; these are **docstring capsules**, not model-generated understanding. `--run` makes one stateless, tool-free model call per task and representation. The default per-call cost cap is `$0.20`; override with `--budget-usd`.

Run the repeated question matrix with the same cached summaries:

```bash
python3.11 eval/capsules/experiment.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --tasks eval/capsules/pydantic_task_matrix.json \
  --out eval/results/capsules-pydantic-matrix \
  --summary-file eval/results/capsules-pydantic/summaries.json \
  --summarize --run --model haiku --repeats 3 --seed 42
```

Calls run in seeded shuffled order. `runs.jsonl` checkpoints each response; `report.json` and `review.md` collect the completed run. `MATRIX_FINDINGS.md` records the audited result. The report separates the cost spent generating new summaries in this run from the total cost of summaries present in the cache.

## Source-backed v2 capsules

`capsule_v2.py` extracts source documentation with physical line numbers, call edges with argument bindings and branch guards, and guarded assignment targets. The harness selects evidence relevant to each question. The cached model summary remains an overview and is labeled as such. A v2 answer can request source with `NEED_SOURCE:`; the harness then makes a second stateless call with numbered source lines and adds both calls to usage and cost. The fixed matrix did not need a fallback, so `V2_FINDINGS.md` also records a separate held-out fallback probe.

Reproduce the v2 matrix using the same frozen questions and summary cache:

```bash
python3.11 eval/capsules/experiment.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --tasks eval/capsules/pydantic_task_matrix.json \
  --out eval/results/capsules-pydantic-v2b \
  --summary-file eval/results/capsules-pydantic/summaries.json \
  --modes v2 --run --model haiku --repeats 3 --seed 42
```

`--modes` can include `raw`, `capsule`, `compact`, `v2`, and `v2_ids`. The default keeps the original three modes for compatibility.

## ID-addressed v2 capsules

`v2_ids` preserves the v2 declaration, overview, documentation, call evidence, and assignment evidence. It replaces unit headings with prompt-local `U` aliases and source references with `E` aliases. The prompt omits source hashes and spans; a separate `{task}.v2_ids.map.json` keeps the full unit IDs, file locations, and source hashes. The harness expands cited IDs back to source locations in recorded answers and retains `answer_with_ids` for audit. `NEED_SOURCE: U1` still triggers the numbered-source fallback.

To compare the three current representations on the frozen matrix:

```bash
python3.11 eval/capsules/experiment.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --tasks eval/capsules/pydantic_task_matrix.json \
  --out eval/results/capsules-pydantic-id-rerun \
  --summary-file eval/results/capsules-pydantic/summaries.json \
  --modes capsule,v2,v2_ids --run --model haiku --repeats 3 --seed 42
```

The harness now records `uncached_input_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens` separately. It does not request explicit provider prompt caching. Short IDs reduce prompt length independently of cache hits.

`ID_FINDINGS.md` records the completed 81-call rerun, including the manual
quality audit, measured token and cost changes, and cache interpretation.

## Dependent agent workflow pilot

`agent_workflow.py` simulates a continuing code-investigation task. The model
chooses unit reads and answers during two dependent phases. A model-generated
handoff memory then replaces the prior conversation and tool observations;
the model answers a final scenario, with the option to reread. The raw arm's
read tool returns numbered Python source. The ID arm's read tool returns a
source-backed v2 capsule with stable `U` and `E` aliases for the trajectory,
plus an optional full-source fallback. Both arms use the same symbol catalog,
questions, pinned revision, model, and call limits. The harness records every
prompt, tool observation, action, usage counter, and final answer.

The two workflows in `agent_workflows.json` cover dictionary versus JSON
serialization and Python versus JSON validation. Each has a post-compaction
question requiring facts from both earlier phases. Reproduce the pilot with:

```bash
PYTHONPATH=eval/capsules python3.11 eval/capsules/agent_workflow.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --out eval/results/agent-workflows-dependent \
  --modes raw,ids --repeats 2 --seed 42
```

This simulator isolates representation and memory effects while allowing
model-chosen reads. It does not execute code edits, tests, shell commands, or
automatic repository search. The catalog is fixed, so retrieval quality is
not measured. Each phase's answer is checked separately from the final
post-compaction answer.

`agent_workflow_session.py` runs the same tasks in actual resumable Claude
sessions. Each read result and task follow-up is sent as a new turn, allowing
provider prompt caching to reuse the earlier conversation. After the
model-written handoff, the harness starts a fresh session containing only the
catalog, memory, and final question. A guarded-evidence variant (`ids_guard`)
adds source-backed conditional raises to the ID capsule; this isolates the
missing-guard failure found in the initial dependent pilot. Full-source
fallback is available only after the same capsule has been read. Run:

```bash
PYTHONPATH=eval/capsules python3.11 eval/capsules/agent_workflow_session.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --out eval/results/agent-workflows-session \
  --modes raw,ids,ids_guard --repeats 2 --seed 42
```

The two workflows are still exploratory and not independent holdouts. Their
rubrics are in `agent_workflows.json`; `agent_quality_review.py` attaches a
hash-pinned manual audit of phase answers and handoff memories after a run.
`AGENT_WORKFLOW_FINDINGS.md` records the completed resumed-session comparison,
the stateless-pilot failure, and the follow-up ablation that disables source
fallback. To reproduce that ablation:

```bash
PYTHONPATH=eval/capsules python3.11 eval/capsules/agent_workflow_session.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --out eval/results/agent-workflows-nosource \
  --modes ids_guard_nosource --repeats 2 --seed 42
```

The recorded reports contain model prompts and outputs. Run
`agent_quality_review.py` with `manual_agent_quality_labels.json` for the
three-mode comparison, or `manual_agent_quality_labels_nosource.json` for the
ablation, to regenerate the quality report. These hash-pinned labels apply
only to the recorded outputs; a new run needs a fresh audit.

## Quality audit

The run harness writes a blank `review.md` and lexical flags; those flags do **not** measure correctness. The reviewed answers in this study are pinned by SHA-256 in `manual_quality_labels.json`. Attach the audit and generate a combined quality report with:

```bash
python3.11 eval/capsules/quality_review.py \
  --labels eval/capsules/manual_quality_labels.json \
  --reports eval/results/capsules-pydantic-matrix/report.json eval/results/capsules-pydantic-v2b/report.json \
  --out eval/results/capsules-quality/quality_report.json
```

This adds `quality_summary` to each report and `quality` to each answer. It also writes a readable `quality_report.md` beside the combined JSON. The tool refuses answers that differ from the reviewed hashes. New runs need a new manual review; they cannot inherit these labels.

Generated prompts, capsule records, usage data, and a manual quality-review sheet go into `eval/results/capsules-pydantic/` (git-ignored). Judge each answer against the task rubric and note unsupported claims. Do not infer answer quality from compression ratio alone.

## What this does and does not measure

The prompt byte and proxy-token counts measure representation size. The model run records actual usage, wall time, and cost where the CLI provides them. Summary-generation cost is recorded separately and should be amortized over reuse. This first benchmark does not test automatic retrieval, graph precision, agent tool choice, compaction, or edit-task correctness. The compact mode is deliberately human-readable: arbitrary numeric IDs would need a shared dictionary before a model could use them reliably.
