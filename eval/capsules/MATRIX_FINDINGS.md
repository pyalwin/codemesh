# Repeated Pydantic question matrix

## Design

- Pydantic commit: `5adfeaaaea947f81f0f01868604f219084ec151a`.
- Nine prespecified questions: three unrelated code paths, three increasing depths of the same JSON Schema path, and three differently worded questions over the same two initialization units.
- Three representations of the same selected source units: raw code, versioned capsules, and compact capsules.
- Three independent, stateless Claude Haiku calls per question and representation, in seeded shuffled order: **81 answer calls**, with **zero call errors**.
- No repository tools or retrieval during answering. Each answer was limited to the supplied context and 120 words. This isolates representation quality from retrieval quality.
- `report.json`, `review.md`, and `runs.jsonl` in `eval/results/capsules-pydantic-matrix/` contain prompts, responses, usage, and the call order.

## Usage

| Representation | Answer calls | Actual input tokens | Output tokens | Answer cost | Sum of call durations |
| --- | ---: | ---: | ---: | ---: | ---: |
| Raw source | 27 | 51,879 | 21,369 | $0.225677 | 213.1 s |
| Capsules | 27 | 25,743 | 16,386 | $0.148480 | 174.1 s |
| Compact capsules | 27 | 25,578 | 15,419 | $0.143350 | 164.2 s |

Relative to raw, capsules used **50.4% fewer input tokens** and **34.2% less answer cost**. Compact capsules used **50.7% fewer input tokens** and **36.5% less answer cost**. The duration column sums individual sequential calls; it is not a controlled latency estimate.

| Question group | Raw input | Capsule input | Compact input |
| --- | ---: | ---: | ---: |
| Unrelated paths | 16,077 | 8,265 | 8,283 |
| Same path, depth 1–3 | 15,912 | 8,973 | 8,871 |
| Same path and depth, three phrasings | 19,890 | 8,505 | 8,424 |

Ten unique code-unit summaries were in the cache. Their **total original generation cost was $0.087593**. Only two were newly generated for this matrix, costing **$0.014285 and 15.8 s**; the other eight came from the prior pilot. Answer costs above exclude this one-time preparation. Repeated questions reuse the summaries, but they still resend capsule text in every answer prompt; this experiment does not test a persistent model-side cache.

## Answer audit

The predefined lexical flags yielded **58/60 hits for every representation**. Manual inspection of the saved answers shows why those flags cannot be used as a quality score:

The frozen one-reviewer rubric audit is now embedded in each `report.json` under `quality_summary` and each answer's `quality` field. `manual_quality_labels.json` pins every label to the exact answer hash. An answer is **acceptable** when all prespecified rubric facts are present and no clear false behavioral claim was identified. Exact call arguments count where the rubric explicitly names them; citation precision and writing style were not graded.

| Representation | Rubric facts met | Rubric-complete answers | Acceptable answers |
| --- | ---: | ---: | ---: |
| Raw source | 58/60 | 25/27 | 25/27 |
| Capsules | 45/60 | 14/27 | 14/27 |
| Compact capsules | 45/60 | 15/27 | 14/27 |

The detailed [combined quality report](../results/capsules-quality/quality_report.json) also breaks these counts down by question group. This is a manual, unblinded judgment on a small benchmark, not an independent estimate of production accuracy.

| Required detail | Raw | Capsule | Compact | Observation |
| --- | ---: | ---: | ---: | --- |
| `model_dump`: explain JSON-serializable versus Python-object output | 3/3 | 0/3 | 0/3 | All compressed answers named both modes but correctly said their supplied context did not explain the behavioral difference. The source docstring did. |
| JSON Schema depth 2: pass core schema **and `mode`** to `generate` | 3/3 | 0/3 | 0/3 | Compressed answers omitted the `mode` handoff or incorrectly listed it among generator constructor options. The capsule kept call names but no argument bindings. |
| `field_validator`: identify `after` as the default mode | 1/3 | 3/3 | 3/3 | The compressed declaration made the default easier to spot than raw source in this run. |

Other inspected answers correctly described selective deep copying, the public JSON Schema delegation, and the class-validator setup across most runs. One compact `model_copy` answer incorrectly said the `deep=True, update=...` branch delegates to `__deepcopy__`; the source actually starts with `__copy__` and selectively deep-copies unchanged fields. One capsule answer at JSON Schema depth 3 described the final method but omitted the upstream call chain. On the initialization questions, compressed answers generally captured the division of labor, but none reproduced the exact `self_instance=self` binding that appears in the raw `__init__` source. These are relevant omissions when a question asks for exact call arguments.

The present capsules are therefore useful as a small structural overview, **not sufficient as a source replacement for behavioral or argument-level questions**. A better capsule should preserve selected branch semantics and call edges with argument bindings, and allow a model to fetch the exact source when evidence is missing. A more aggressive symbol or numeric encoding is premature: the two compressed layouts had nearly identical input usage, while missing semantics dominated quality.

## Limits

This is one library revision and one small model, with three repeats per cell. Relevant units were selected in advance, so the run says nothing about retrieval accuracy, graph construction, agent navigation, compaction recovery, or edit-task success. The summary cache was generated by the same model family and does not constitute independently verified truth. Manual review was performed by one reviewer, and source-line citation precision was not separately scored. The next experiment should add argument-aware edges and targeted fallback to source, then rerun the same frozen matrix before expanding to more repositories and tasks.
