# Structured-reference and router-corrected Luna pilot

Date: 2026-10-18. One paired `factory_validated_data` repair, one repeat.
Both arms requested `gpt-6-luna`, low effort, Codex CLI 0.155.1, using the same
controlled direct-tool catalog and explicit transcript protocol.

## Implemented changes

- Source lookup preserves canonical-ID priority and accepts unique qualified or
  bare method names. Ambiguous names return bounded exact candidate IDs and
  file spans. It never chooses an overload by body size. Existing source freshness
  checks apply to name resolution as well.
- Successful source reads contribute up to eight structured references to a
  versioned handoff payload persisted in the existing SQLite note. References
  include exact ID, path, lines, view hash, and mode. File hashes are checked at
  restore time; changed/unavailable files lose old spans and view hashes. No
  source bytes or conditional-read authorization carry into the fresh context.
- Prompts request JSON actions and nullable defaults explicitly. Models remain
  able to expand search or request full source deliberately.
- The runner counts runtime router rejections in stderr and rejects affected
  calls, even when they subsequently emit valid final JSON. All-attempt usage
  and rejection counts remain visible. No token totals are adjusted by guessed
  retry counts.
- A scoped `model_catalog_json` selects direct tool mode for the same Luna model
  with shell, patch, and experimental native capabilities disabled. Both arms
  use this configuration. Catalog hashes are recorded per call and in the run
  manifest; incompatible resumes fail before preparing fixtures.

## Fresh paired result

| Metric | Filesystem | Codemesh | Change |
| --- | ---: | ---: | ---: |
| First/final patch passes hidden tests | 1/1 | 1/1 | Same on this pair |
| Completed | 1/1 | 1/1 | Same on this pair |
| Runtime router errors | 0 | 0 | Clean under the recorded guards |
| Valid model calls, including handoff | 15/15 | 17/17 | +2 calls |
| Tool attempts before first correct edit | 8 | 10 | +2 calls |
| Successful bytes before first correct edit | 21,689 | 16,954 | -21.8% |
| Total successful tool-output bytes | 21,845 | 21,470 | -1.7% |
| Successful source/read bytes | 14,946 | 5,234 | -65.0% |
| Source/read error bytes, separately counted | 55 | 0 | -55 bytes |
| Input tokens, including cache | 50,958 | 53,194 | +4.4% |
| Uncached input tokens | 47,758 | 49,610 | +3.9% |
| Cache-read input tokens | 3,200 | 3,584 | +12.0% |
| Reported cache-write input tokens | 0 | 0 | Same |
| Output tokens | 1,593 | 1,843 | +15.7% |
| Model-call wall time | 72.161 s | 80.796 s | +12.0% |
| Billed USD | Unavailable | Unavailable | Not calculated |

Both arms ran the visible test and passed the independent hidden acceptance
tests. These observations come from one pair. They do not establish general
quality equivalence, a cost saving, or an isolated contribution from each change.
The historical pilot used a different runner and contains runtime router errors;
it is not a clean control for these changes.

## What happened at restart

Codemesh persisted two references:

```text
symbol:pydantic/fields.py:FieldInfo.get_default@L901
symbol:pydantic/_internal/_fields.py:resolve_default_value
```

The fresh phase-2 agent requested both exact IDs, edited the source, ran a grep
check, ran the visible test, and finished. There was **no missing-symbol error
and no recovery search** after restart. The old source was retrieved again,
which is necessary because that context had been reset.

All four Codemesh searches used the default limit. All four source requests
used the default `auto` mode; the selected methods were small enough to return
exact source. Consequently this run does not measure large-class preview usage.

The remaining extra tool attempts arose during investigation: Codemesh used
two map calls, four searches, and two source reads before the handoff. Structured
references fixed the observed restart failure, while overall investigation
efficiency still needs a broader comparison. Legitimate cache reads also appear
in this clean run; cached tokens are not inherently evidence of router retries.

## Validation and artifacts

- 199 TypeScript tests; build passed.
- 56 focused Python tests plus 29 subtests passed.
- [Nine pinned SQLite handoff checks](results/vnext-handoff-contract.json) passed
  without model calls, including overload IDs and stale/deleted references.
- [Three live smoke calls](results/vnext-luna-router-smoke.json) passed with zero
  router errors, followed by the 32 valid calls in this paired repair.
- [Raw paired report](results/vnext-luna-reference-pilot/report.json).
- [Call journal and provider diagnostics](results/vnext-luna-reference-pilot/calls.jsonl).
- [Run configuration and catalog/protocol hashes](results/vnext-luna-reference-pilot/run_config.json).
- [Audit correcting the earlier pilot](../docs/experiments/luna-router-audit.md).

The runner retains `--no-handoff-references` for a narrative-only ablation.
The next wider comparison should use all three tasks with both arms on the same
corrected runner, and inspect per-task quality, calls to the correct edit, and
complete usage buckets before claiming an efficiency improvement.
