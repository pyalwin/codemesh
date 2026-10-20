# ID-addressed v2 rerun

The benchmark reran the frozen nine-question Pydantic matrix at commit
`5adfeaaaea947f81f0f01868604f219084ec151a` with three independent Haiku
4.5 calls per question and representation (81 answer calls total). The modes
were v1 capsules (`capsule`), source-backed `v2`, and source-backed `v2_ids`.
The same source units and cached summaries were used in every mode; there were
no source fallbacks or failed calls. Summary generation was not charged in this
rerun.

| Representation | Acceptable answers | Rubric facts | Actual input tokens | Answer cost | Cache reads |
| --- | ---: | ---: | ---: | ---: | ---: |
| v1 capsule | 14/27 | 44/60 | 25,824 | $0.143786 | 0 |
| v2 | 21/27 | 55/60 | 43,245 | $0.213908 | 0 |
| v2 with IDs | 21/27 | 55/60 | 37,983 | $0.191219 | 0 |

Against v2, ID addressing saved 5,262 actual input tokens (12.2%) and
$0.022689 of observed answer cost (10.6%). The generated answer lengths also
varied, so the cost difference is not solely an input-token effect. Mean
measured call time changed from 7.77 to 7.46 seconds, too small and noisy to
claim a latency improvement.

Every ID answer used at least one `E` evidence citation. Across 27 ID answers,
all 153 `E` references mapped to saved evidence; no unresolved `E`/`U` alias
remained after expansion. The server-side map records full path, line, source
unit, and source hash. These are prompt-local aliases, not model-decodable
hashes. The underlying evidence text was unchanged.

## Quality details

The one-reviewer audit used the prespecified rubrics and answer hashes. Both
v2 formats omitted the initial class-method handoff in all three responses to
the depth-two JSON Schema question. ID addressing neither fixed nor caused
that omission. One ID answer about `model_copy` incorrectly described all
updated fields as going through `__dict__.update`, overlooking allowed extra
fields. One plain-v2 answer incorrectly said validation occurs once during
class completion. Each was marked unacceptable for its false claim. The v1
capsules again missed the `model_dump` mode distinction and the exact
`self_instance=self` binding.

Citation precision was not part of the rubric. At least one plain-v2 answer
gave incorrect numeric citation lines despite otherwise correct behavioral
content. ID resolution prevents invented *addresses* for valid aliases, but
it does not prove that a cited line supports the surrounding claim.

## Cache interpretation

The harness recorded zero cache-write and cache-read tokens for all 81 calls.
It does not set explicit provider cache breakpoints. In this run, the largest
whole prompt was 2,082 actual input tokens; Haiku 4.5's published minimum
cacheable prefix is 4,096 tokens. The current prompt also places the varying
question before the context, so it is not arranged for prefix sharing across
different questions. These results measure prompt shortening, not provider
cache savings. A separate cache test would need a model/prefix that meets its
minimum, a stable context block before the question, explicit cache control,
and verification through the cache usage counters.

## Limits

This is one repository revision, one model, nine non-holdout questions, and
one unblinded reviewer. V2 was previously tuned against this matrix. The
`o200k_base` proxy is not the Claude tokenizer; the table uses actual model
usage. The observed 21/27 tie does not prove quality equivalence in larger
codebases. Prompt-local aliases need a map scoped to the source revision and
the particular request; sending only an alias in a fresh stateless call does
not give the model its underlying code fact.

The saved prompt texts, alias maps, model answers, usage counters, and manual
audit are in `eval/results/capsules-pydantic-id-rerun/`. Manual labels are
in `manual_quality_labels_id_rerun.json`.
