# vNext CLI agent eval: pinned Pydantic

**Run:** 2026-10-18  
**Target:** Pydantic `5adfeaaaea947f81f0f01868604f219084ec151a`  
**Model:** `claude-haiku-4-5-20251001`  
**Artifacts:** [`results/vnext-contract.json`](results/vnext-contract.json), [`results/vnext-agent-full/report.json`](results/vnext-agent-full/report.json), [`results/vnext-agent-full/runs.jsonl`](results/vnext-agent-full/runs.jsonl)

## Method

Three seeded Pydantic regressions, three repeats each, paired across a filesystem
arm and a Codemesh CLI arm. Each trajectory has two investigation phases, a
model-written handoff, a fresh editing session, exact-string edits, a visible
test, and a hidden acceptance test. The filesystem arm can grep and read. The
Codemesh arm is instructed to navigate with map/search/source and can fall back
to the same grep/read tools. Its handoff is stored as a source-bound note; the
agent can recall it in the fresh session. Both arms use the same model, prompts
for the repair, turn cap, and per-call budget. See [`VNEXT_EVALS.md`](VNEXT_EVALS.md)
for setup and interpretation rules.

The separate CLI correctness suite passed **11/11** checks on the same pinned
repository: exact and progressive source, conditional rehydration, durable
recall, stale refusal, deterministic navigation, and persistent search-cache
reuse/invalidation.

## Paired results

| Metric, 9 matched task/repeat pairs | Filesystem | Codemesh | Difference |
|---|---:|---:|---:|
| Hidden-test passes | 8/9 (88.9%) | 7/9 (77.8%) | **−11.1 percentage points** |
| Finished agent trajectories | 8/9 | 5/9 | −33.3 percentage points |
| Billed model cost | $1.958 | $2.112 | **+7.8%** |
| Model input tokens, including cached input | 2.897M | 5.961M | **+105.8%** |
| Cache-read input tokens | 2.181M | 5.388M | +147.0% |
| All tool-observation bytes | 205 KB | 391 KB | +90.4% |
| Native source reads | 100 | 65 | −35.0% |
| Byte-identical repeated read observations | 7.7 KB | 49.9 KB | +551.3% |

An agent can leave a passing patch but fail to finish. One Codemesh trajectory
hit the model context limit after its patch passed; another hit the turn cap
after a passing patch. Hidden-test pass and agent completion are therefore
reported separately. The remaining unfinished trajectories had no passing
patch.

| Task | Filesystem hidden passes | Codemesh hidden passes | Filesystem cost | Codemesh cost |
|---|---:|---:|---:|---:|
| `kebab_alias` | 3/3 | 2/3 | $0.439 | $0.508 |
| `negative_bytesize` | 3/3 | 3/3 | $0.552 | $0.844 |
| `factory_validated_data` | 2/3 | 2/3 | $0.967 | $0.760 |

## What happened

- Codemesh replaced some native reads, but added 19 searches, 7 map calls, and
  31 source calls. It still made 65 native reads and 31 native greps. Total tool
  calls rose from 163 to 174.
- The 19 search results alone delivered **88.7 KB** of JSON observations.
  Source calls delivered **180.9 KB**, on top of **90.9 KB** of native reads.
  The filesystem arm's native reads delivered **163.2 KB**. Search result breadth
  and source wrappers erased the benefit of fewer native reads.
- The harness saved 7 Codemesh handoff notes, but agents requested recall only
  once. They never supplied `ifHash`, so conditional source reads saved no bytes
  in these trajectories. The feature passed its deterministic contract, but the
  agent did not use it in the live workflow.
- Codemesh used fewer uncached input tokens (59.4K versus 112.7K), but much
  more cache-read input. This kept the billed cost increase to 7.8% even though
  total input-token use doubled. Prompt caching helped price, not context size
  or completion.

**Conclusion:** This run does not support a cost or quality improvement for the
current CLI-first agent protocol. The correctness layer works, but the agent
still performs broad searches, repeats source retrieval, falls back to native
tools, and rarely recalls stored knowledge. The next experiment should make
search/source responses materially smaller and make the saved handoff a
deterministic input at the fresh-session boundary. Conditional omission must
remain limited to source bytes known to be in the active context.

## Limits and accounting

These are 9 pairs across only 3 synthetic regressions, so the percentages are
descriptive, not statistically conclusive. The Codemesh arm's note is stored
outside the prompt and retrieved only if the agent chooses to ask; that is part
of the behavior measured here. Indexing and pytest CPU are excluded from model
cost. The report's model totals exclude two ungraded harness handoff attempts
that consumed **$0.302**; those remain in `runs.jsonl`. Interrupted in-flight
attempts before per-call journaling was added have unmeasured spend. The runner
now journals individual model calls for future runs.
