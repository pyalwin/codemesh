# Source-backed capsule v2 evaluation

## Change

The v2 capsule keeps the source hash, declaration, and cached short overview. It adds query-selected evidence extracted from the pinned Python AST and docstring:

- physical source lines for relevant documentation;
- calls with exact positional and keyword arguments, plus enclosing `if` and conditional-expression guards;
- assignment targets and values with branch guards.

The overview is explicitly marked as generated. The answer prompt tells Haiku to use source-backed evidence for argument and branch claims. When requested detail is absent, it can emit `NEED_SOURCE:`. The harness selects the named unit or path, resends numbered source, and counts both calls. This is an experimental question-aware capsule formatter in the benchmark harness, not yet a Codemesh MCP index or retrieval implementation.

## Frozen matrix rerun

The questions, code units, Pydantic commit, model, three repeats, and answer length limit match `MATRIX_FINDINGS.md`. The final v2 iteration made **27 stateless Haiku answer calls with zero errors and zero source fallbacks**. The same source-hash-matched summary cache was reused, so this run incurred no new summary-generation calls.

| Representation | Input tokens | Answer cost | Sum of call durations | Source fallbacks |
| --- | ---: | ---: | ---: | ---: |
| Raw source, earlier run | 51,879 | $0.225677 | 213.1 s | n/a |
| Original capsules, earlier run | 25,743 | $0.148480 | 174.1 s | n/a |
| Source-backed v2 | 43,218 | $0.192276 | 190.2 s | 0 |

V2 used **16.7% fewer input tokens** and **14.8% less answer cost** than raw source. The extra evidence makes it more expensive than the original capsules. The duration values are sums of sequential CLI calls, with normal runtime noise. Answer costs exclude the ten summaries' prior $0.087593 generation cost, which is shared with the original capsule run.

| Question group | Raw input | Original capsule input | V2 input |
| --- | ---: | ---: | ---: |
| Unrelated paths | 16,077 | 8,265 | 12,894 |
| Same path, depth 1–3 | 15,912 | 8,973 | 13,458 |
| Same path and depth, three phrasings | 19,890 | 8,505 | 16,866 |

## Answer review

Manual review of the 27 final v2 responses against the prespecified rubrics found **25/27 complete and acceptable**. The earlier raw run also had **25/27 acceptable**; its two misses omitted `field_validator`'s `after` default. The v2 misses were two answers to the deepest JSON Schema question that explained `GenerateJsonSchema.generate` but omitted the initial `BaseModel.model_json_schema` handoff. For comparison, the original and compact capsules each had **14/27 acceptable** answers. These scores are embedded in each answer's `quality` field and summarized in each report's `quality_summary`; the [combined quality report](../results/capsules-quality/quality_report.json) includes category counts. `manual_quality_labels.json` pins every label to the answer hash. This is a one-reviewer, unblinded rubric judgment; source-line citation precision was not separately scored.

The originally broken facts improved:

| Required detail | Original capsule | V2 |
| --- | ---: | ---: |
| `model_dump`: JSON mode only emits JSON-serializable types; Python mode may retain objects | 0/3 | 3/3 |
| JSON Schema utility: `generate(cls.__pydantic_core_schema__, mode=mode)` | 0/3 | 3/3 |
| `model_copy`: selective deep copy, unvalidated updates, and fields-set update | 3/3 on the original rubric | 3/3 |

The guarded assignment evidence also improved the `model_copy` detail beyond the original rubric: two of three v2 answers explicitly distinguished the allowed-extra destination; the third cited the relevant assignment lines without explaining that branch. All three v2 answers to the differently framed `Model(x=1)` question reproduced the `validate_python(data, self_instance=self)` call and identified the validator assignment in `complete_model_class`. The v2 answers to `field_validator` also identified `after` as the default in all three repeats. The frozen matrix's lexical flags remain only review aids; they missed the earlier serialization and mode-handoff failures.

## Source fallback probe

A separate question asked how the shared `deepcopy` memo is initialized in `model_copy`. The query-selected capsule showed calls using `memo` but omitted its initialization. The model requested source; the harness made a second stateless call with numbered source lines. The final answer identified `memo: dict[int, Any] = {}` at line 437 and the shared-memo comment at line 436. **Two calls together used 2,889 input tokens and cost $0.013268.** A first probe exposed that Haiku sometimes embeds `NEED_SOURCE:` in a longer answer; the parser now recognizes that form. A subsequent probe exposed fabricated line citations when source was unnumbered; numbered fallback source fixed the cited initialization line. The probe report is in `eval/results/capsules-pydantic-v2b-fallback-probe/report.json`.

## Interpretation and next gate

The first tradeoff is clear: source-backed capsules recovered the tested behavioral facts while retaining a modest token saving versus raw source. This is still exploratory. V2 was refined after inspecting answers on this same matrix, so the 25/27 result is **not an independent holdout**. The fallback depends on the model recognizing missing evidence; confident wrong answers will not automatically trigger it. Evidence selection is lexical and may omit a relevant branch in other code. The next gate should use unseen repositories and questions, with rubric scoring and fallback cost measured before integrating this into Codemesh's MCP surface. Compact notation remains available as a separate benchmark mode for later work.
