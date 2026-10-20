# Codex / Luna retrieval pilot

> **Validity correction:** this pilot contains runtime router rejections that the
> original runner failed to reject: 13 across 10/14 Codemesh calls and 5 across
> 4/15 filesystem calls. Preserve the results as diagnostics, not a clean
> efficiency comparison. The stronger claim of a tool-free run below was not
> established. See [the router audit](../docs/experiments/luna-router-audit.md).

Date: 2026-10-18. One paired Pydantic repair: `factory_validated_data`, one repeat.
Requested model: `gpt-6-luna`, low effort, Codex CLI 0.155.1.
Both arms use the same explicit transcript protocol, fresh editing context,
12-turn-per-phase cap, visible test, and hidden acceptance test. Historical
Claude/Haiku trajectories are not the baseline for these numbers.

## Result

| Metric | Filesystem | Codemesh | Change |
| --- | ---: | ---: | ---: |
| First patch passes hidden tests | 1/1 | 1/1 | Same on this pair |
| Final patch passes hidden tests | 1/1 | 1/1 | Same on this pair |
| Completed | 1/1 | 1/1 | Same on this pair |
| Successful tool-output bytes | 23,630 | 17,684 | -25.2% |
| Bytes before first correct edit | 23,474 | 17,626 | -24.9% |
| Tool calls before first correct edit | 9 | 9 | No reduction |
| Total tool calls | 11 | 10 | -1 call |
| Model calls, including handoff | 15 | 14 | -1 call |
| Reported input tokens, including cache | 130,598 | 153,893 | +17.8% |
| Uncached input tokens | 106,406 | 93,861 | -11.8% |
| Cache-read input tokens | 24,192 | 60,032 | +148.1% |
| Reported cache-write input tokens | 0 | 0 | Same |
| Output tokens | 1,708 | 2,107 | +23.4% |
| Model-call wall time | 80.965 s | 113.008 s | +39.6% |
| Billed USD | Unavailable | Unavailable | Not calculated |

These are diagnostic observations from one pair, not an estimate of broad repair
quality or a demonstrated cost saving. Cache reads are part of total input, not
additional input. Provider counters describe reported work, not necessarily the
context length of one underlying request. Codex CLI does not report billed USD.

The filesystem agent ran the visible test; Codemesh finished without requesting
it. Both patches passed the harness's independent visible and hidden checks.
Consequently the one-call difference at completion is not evidence of faster
navigation: both needed nine tool attempts before the correct edit.

## What we learned

- The deliberately integrated Luna agent used Codemesh exclusively for retrieval:
  two maps, three searches, and four source attempts, including one failed source
  attempt. It made no native grep/read calls. Tool adoption was not the problem
  in this controlled run.
- It selected exact methods rather than fetching the entire `FieldInfo` class.
  All its source requests explicitly asked for `full`. This pair therefore does
  **not** establish the live benefit of the new bounded `auto` view.
- The restored handoff preserved the method name but omitted its exact graph ID.
  The fresh agent requested `FieldInfo.get_default`, received a symbol-not-found
  error, searched again, and then requested the returned exact ID. One source
  observation was legitimately repeated after the fresh-context boundary; none
  were repeated within the same context.
- Smaller observations did not yield fewer calls to the correct edit, lower
  total input, or lower model-call latency on this pair. More compression alone
  is not the next hypothesis to test.

## Next bounded experiment

Persist a small structured set of source references alongside the narrative
handoff: exact symbol ID, file, source-view hash, and the reason it was read.
Restore those references with current freshness status. They are navigation
pointers; the fresh model must retrieve the needed source again.

Compare narrative-only handoff with narrative plus those references using the
same Luna runner and fixed retrieval implementation. Measure failed source
calls, searches after restart, calls/bytes to the correct edit, acceptance, and
full usage buckets. Include all three tasks and held-out retrieval queries before
claiming a general improvement. Do not automatically omit source on the basis
of a previous session's delivery record.

## Artifacts and reproduction

- [Raw report](results/vnext-luna-factory-pilot/report.json)
- [Call journal with raw provider events](results/vnext-luna-factory-pilot/calls.jsonl)
- [Pinned configuration and protocol fingerprint](results/vnext-luna-factory-pilot/run_config.json)
- [Correctness contract: 14/14](results/vnext-contract-retrieval-reset.json)
- [Historical query ranking replay](VNEXT_RANKING_REPLAY.md)
- [Diagnosis and decision](../docs/experiments/vnext-retrieval-reset.md)

```sh
/tmp/codemesh-edit-eval-venv/bin/python eval/vnext_agent.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --task factory_validated_data --repeats 1 \
  --provider codex --model gpt-6-luna \
  --out eval/results/vnext-luna-factory-reproduction --run
```

Runner validation: 26 focused Python tests (plus 21 subtests), 193 TypeScript
tests, build, and all 14 pinned CLI contract checks passed. The Luna connectivity
smoke passed before the paired pilot. No unexpected native tool activity was
visible in JSONL, but the original runner missed rejected internal dispatches in
stderr. The updated runner counts these and invalidates affected calls.
