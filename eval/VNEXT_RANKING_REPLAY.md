# Search ranking replay

Run: `node eval/replay_ranking.mjs`

Replayed **18 unique historical task/query pairs** against copied pinned Pydantic indexes. The previous comparator and the current handler both receive the same index snapshot. The original indexes and dist files are untouched.

The change preserves exact identifiers, ranks lexical results by normalized BM25, treats trigrams as a separate fallback stage, and uses PageRank only on equal relevance. Repeated query words are deduplicated case-insensitively before lexical search; exact lookups retain the original query.

| Navigation diagnostic | Before | After |
|---|---:|---:|
| targetSymbolTop8 | 5/18 | 6/18 |
| targetSymbolTop20 | 7/18 | 10/18 |
| targetFileTop8 | 11/18 | 13/18 |

| Task | Historical query | Target symbol rank before | After | Target file rank before | After |
|---|---|---:|---:|---:|---:|
| kebab_alias | to_snake | 1 | 1 | 1 | 1 |
| kebab_alias | field alias kebab snake case conversion | 4 | 3 | 4 | 3 |
| kebab_alias | alias_generators tests | — | — | — | 19 |
| kebab_alias | test camelCase PascalCase snake_case conversion | — | — | — | — |
| negative_bytesize | ByteSize byte quantity | — | 11 | 9 | 2 |
| negative_bytesize | ByteSize pydantic/types.py | 9 | 11 | 6 | 2 |
| negative_bytesize | ByteSize | 9 | 11 | 2 | 2 |
| kebab_alias | kebab case snake case alias conversion | 3 | 2 | 3 | 2 |
| factory_validated_data | default factory validated data missing | — | — | — | 3 |
| factory_validated_data | FieldInfo class definition | — | — | 4 | 2 |
| negative_bytesize | bytesize byte quantity format | — | 18 | 10 | 2 |
| factory_validated_data | FieldInfo get_default | — | 1 | 2 | 1 |
| factory_validated_data | default factory validated field test | — | — | — | — |
| negative_bytesize | byte quantity unit scaling KiB | — | — | 8 | — |
| negative_bytesize | ByteSize display format negative | — | — | 4 | 2 |
| negative_bytesize | ByteSize human_readable | 7 | 2 | 7 | 2 |
| factory_validated_data | default factory missing-data validated field | — | — | — | 14 |
| kebab_alias | kebab case snake case conversion alias | 3 | 2 | 3 | 2 |

A dash means absent from the first 20 results. Symbol targets: `to_snake`, `ByteSize.human_readable`, and `FieldInfo.get_default` (including overload IDs). File rank is the first hit from the task's implementation file.

## Interpretation and limits

- Offline navigation diagnostic; no model calls, repair acceptance, token measurements, or cost claims.
- Target symbols come from the three repair tasks; overload IDs with @L are treated as the same symbol family.
- A broad investigative query can usefully find a test or parent class before the eventual edit symbol.
- Saved index snapshots can differ from index state at the original action; recordedSymbolRank is retained separately.
- OR lexical search still allows partial matches and cannot infer behavioral concepts absent from indexed text.

The repeated-word issue was visible in `kebab case snake case alias conversion`: two OR clauses for `case` doubled generic case-match relevance. Deduplication prevents accidental weighting from phrasing. This does not introduce test, legacy-path, or repository-specific ranking penalties.

Detailed results: `eval/results/vnext-ranking-replay.json`.
