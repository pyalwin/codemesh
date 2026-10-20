# Dependent agent workflow experiment

## Question and design

The independent-query matrix measures one answer at a time. This experiment
asks whether source-backed capsules help an agent that investigates one code
path, follows a related path, and then resumes from a compressed handoff to
answer a question that depends on both. The target is quality **and** total
paid cost across the trajectory, including rereads, source fallback, and
cache behavior.

The target repository is open-source Pydantic at pinned commit
`5adfeaaaea947f81f0f01868604f219084ec151a`, not Codemesh. Both workflows
use Claude Haiku (`claude-haiku-4-5-20251001`, low effort), the same fixed
three-symbol catalog, and two repeats per representation. The model chooses
reads and answers through a small action protocol. A first task must read at
least one unit. A dependent second task must read one or two units. The model
then writes a handoff memory; the final scenario runs in a fresh session with
only that memory, catalog, and question. The first two phases use a real
resumed Claude session, so provider cache reads and writes are observed. A
read observation is sent as a new user turn; this is a simulated tool, not an
MCP tool result.

Workflows:

- **Serialization:** inspect `model_dump`, follow `model_dump_json`, then
  decide whether changing only the former's body changes the latter.
- **Validation:** inspect `model_validate`, follow `model_validate_json` and
  `complete_model_class`, then evaluate an alias/name guard scenario after
  handoff.

Representations:

- `raw`: numbered source for the requested unit.
- `ids`: ID-addressed capsule with overview, documented facts, call and
  assignment evidence. The model can request full source after seeing a
  capsule.
- `ids_guard`: adds extracted conditional-raise evidence; the same source
  fallback remains available.
- `ids_guard_nosource`: an exploratory ablation with source fallback removed.

The raw/IDs/guarded comparison was run as 12 trajectories. One raw
serialization attempt timed out during the model-written handoff and was
rerun once. The failed attempt is retained separately and excluded from the
paired table. The no-source ablation was run afterwards as four additional
trajectories. Each completed trajectory has three phase answers and one
handoff memory. All calls, observations, cache counters, cost, and outputs are
recorded in the JSON reports.

## Results

The table sums **four completed trajectories per mode**. Input includes
uncached, cache-creation, and cache-read tokens reported by the CLI. Cost is
the sum of its per-call USD figures, not an estimate from token count.

| Representation | Final core conclusion | Final fully acceptable | Handoff memory acceptable | Input tokens | Cache reads | Tool reads / repeated | Full-source fallbacks | Cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Raw source | 4/4 | 2/4 | 4/4 | 157,514 | 29,183 | 17 / 7 | 0 | $0.300613 |
| ID capsule | 4/4 | 2/4 | 3/4 | 154,235 | 37,936 | 21 / 11 | 7 | $0.269192 |
| Guarded ID capsule | 4/4 | 2/4 | 4/4 | 143,588 | 25,516 | 19 / 9 | 4 | $0.277342 |
| Guarded, no source fallback | 4/4 | 2/4 | 2/4 | 132,258 | 13,262 | 19 / 9 | 0 | $0.267593 |

Relative to raw source, guarded capsules with fallback used **8.8% fewer
input tokens** and cost **7.7% less**. With fallback disabled, they used
**16.0% fewer input tokens** and cost **11.0% less**. Plain IDs cost 10.5%
less but induced seven full-source fallbacks and one incorrect handoff memory.
The no-source ablation is not a deployment recommendation: it had the lowest
cost here, but two serialization memories omitted the direct `to_json().decode()`
detail. It may fail on questions outside these tasks when a capsule lacks a
needed fact.

The two paths behave differently. Each row below sums two repeats:

| Workflow | Raw cost | ID cost | Guarded cost | Guarded, no source cost | ID / guarded source fallbacks |
| --- | ---: | ---: | ---: | ---: | ---: |
| Serialization | $0.152637 | $0.110384 | $0.119970 | $0.130212 | 1 / 0 |
| Validation | $0.147977 | $0.158808 | $0.157372 | $0.137382 | 6 / 4 |

The capsule arms saved money on serialization, while validation became more
expensive than raw when the agent repeatedly fell back to full source. The
overall savings therefore depend on how often the capsule omits a necessary
guard and how the agent chooses to verify it.

"Core conclusion" scores the main final decision: whether the serialization
patch propagates, or whether the JSON validation delegate runs. "Fully
acceptable" requires every prespecified final rubric fact and no false
behavioral claim. All modes answered the main decisions correctly, but only
2/4 final answers per mode supplied every requested detail. The serialization
answers often omitted `.decode()`, and final answers often omitted the
`round_trip`/`indent`/`ensure_ascii` forwarding distinction. The full
phase-answer scores were raw 8/12, IDs 8/12, guarded IDs 7/12, and guarded
without fallback 7/12. These are small descriptive counts, not estimates of
general task success. One guarded and one no-source run speculated incorrectly
in phase one that the JSON method probably wraps dictionary export; each
corrected this after reading the sibling.

Citation precision was excluded from the quality rubric. Four phase answers
in the main comparison retained unresolved `E` references that looked like
source line numbers, so correct behavior must not be read as reliable citation
provenance. This needs a resolver/validation gate before using these answers
as auditable evidence.

## What changed from the stateless pilot

The first transcript-replay pilot omitted conditional `raise` facts from
plain ID capsules. Both ID validation runs therefore claimed that
`validate_json` ran for `by_alias=False, by_name=None`; both raw runs
identified the preceding `PydanticUserError`. This was a substantive drift,
not just a missing citation. The extractor now includes guarded raises, and
the resumed-session comparison explicitly measures that variant. In real
resumed sessions, plain IDs could recover by reading source, but did so seven
times. This distinguishes **capsule completeness** from **fallback policy**.

The stateless replay also showed a misleadingly large token advantage for
capsules because each call re-sent the transcript and neither arm received
cache reads. Real resumed calls did receive cache reads. Their advantage is
therefore smaller and must be evaluated in paid dollars as well as total
input. Shorter prompts do not guarantee proportionally lower bills when
cache reads, output length, and tool choices differ.

## Limits and next benchmark

This is a small, exploratory pilot: two hand-authored workflows, one pinned
repository, two repeats, one model, and a fixed symbol catalog. The guarded
variant was improved after observing a failure on these tasks, so these are
not independent holdout results. The model did not search a large repository,
edit files, run tests, or handle an actual user issue. The handoff is a
model-written fresh-session restart, not provider-managed compaction. The
summary cache was prepared by earlier work; its creation cost is excluded
from these trajectory totals. Provider prompt caching was observed but not
explicitly configured, and variation between repeats is substantial.

The next benchmark should sample real issue/PR tasks in a standard open-source
repository, turn each into a dependency chain (discover path → inspect
callers/guards → plan edit → edit → test/fix → summarize), and score the
resulting patch with existing tests plus source-grounded review. Randomize
paired raw/capsule runs, reset their workspace to the same commit, let the
agent search and choose tools, insert a handoff after a fixed work point, and
log all model/tool turns. Report success rate, false claims, source fallback,
repeated reads, cache categories, wall time, total paid cost, and cost per
accepted patch. This tests the actual long-running-agent objective rather
than extrapolating from one-off answers.

## Artifacts

- `agent_workflows.json`: frozen workflows and rubrics.
- `agent_workflow_session.py`: resumed-session simulator.
- `agent_quality_review.py`: hash-pinned manual quality audit.
- `manual_agent_quality_labels.json` and
  `manual_agent_quality_labels_nosource.json`: one-reviewer labels for the
  recorded outputs.
- `../results/agent-workflows-session/report.json` and
  `../results/agent-workflows-nosource/report.json`: full trajectories and
  aggregate usage.
- `../results/agent-workflows-session/quality_report.md` and
  `../results/agent-workflows-nosource/quality_report.md`: scored summaries.
