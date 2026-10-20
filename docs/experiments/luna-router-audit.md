# Luna pilot: router audit

Date: 2026-10-18. Read-only analysis of the original `factory_validated_data` pair using Codex CLI 0.155.1 and requested model `gpt-6-luna`.

## Decision

Keep the pilot as diagnostic evidence, marked as containing runtime router errors in both arms. Its patches and observed retrieval bytes remain inspectable, but it is not a clean comparison of tool-free model decisions. Future strict harness runs should fail closed on runtime router errors, preserve the raw evidence, and exclude affected trajectories from the primary comparison. Do not silently retry until a clean result appears.

The earlier statement that no unexpected native tool activity was accepted was too broad. The parser checked JSONL items, while successful-process stderr still contained tool-router errors. The logs do not show successful native file access, but they do not establish the absence of internal routing attempts.

## Verified counts

Counts below come from [`calls.jsonl`](../../eval/results/vnext-luna-factory-pilot/calls.jsonl). A router error is an occurrence of `error=code-mode host is disabled` in stderr. The exact startup warning in JSONL is counted separately.

| Observation | Filesystem | Codemesh |
| --- | ---: | ---: |
| Model calls, including handoff | 15 | 14 |
| Calls containing runtime router errors | 4 | 10 |
| Runtime router error lines | 5 | 13 |
| Calls with the startup warning | 15 | 14 |
| Reported input tokens | 130,598 | 153,893 |
| Reported cache-read input tokens | 24,192 | 60,032 |
| Model-call wall time | 80.965 s | 113.008 s |

Codemesh error calls are 1, 2, 4, 5, 6, 7, 10, 11, 12, and 14. Calls 4, 10, and 11 each contain two errors. Filesystem error calls are 1, 4, 7, and 10; call 7 contains two. The supplied critique correctly identifies 10 affected Codemesh calls, but undercounts its error lines as 11 instead of 13.

All 29 invocations have exactly one `turn.completed` event. None expose a native tool item in the recorded JSONL. Each also has the known startup diagnostic before `turn.started`, including calls with no stderr error. That startup diagnostic therefore cannot serve as a count of runtime attempts.

## Tokens and time: association, not a measured retry budget

| Call group | Calls | Input tokens | Cache-read tokens | Wall time | Median per call |
| --- | ---: | ---: | ---: | ---: | ---: |
| Filesystem, no router errors | 11 | 72,168 | 0 | 50.790 s | 4.589 s |
| Filesystem, router errors | 4 | 58,430 | 24,192 | 30.175 s | 7.558 s |
| Codemesh, no router errors | 4 | 27,014 | 0 | 20.612 s | 4.931 s |
| Codemesh, router errors | 10 | 126,879 | 60,032 | 92.396 s | 8.251 s |

All reported cache reads occur in calls containing router errors. Those calls also tend to take longer. This is consistent with additional internal processing, but the logs do not expose each internal request or prove that each error equals one model retry. Prompt length, action type, and transcript position differ across these groups. Do not subtract cache reads as if they were an independently measured waste budget.

For example, Codemesh call 4 reports two errors, 18,138 input tokens, 11,776 cached tokens, and 10.339 s. Call 10 reports two errors, 12,458 input tokens, 10,624 cached tokens, and 8.312 s. Conversely, its slowest call, number 6 at 16.077 s, also logs a failed analytics request. The complete 32.043 s difference between arms cannot be allocated to router retries from these traces. Clean edit call 13 took 6.690 s, rather than approximately 4.1 s.

The claim that the words “Codemesh CLI” triggered routing is a plausible hypothesis, not an established cause. Filesystem calls also contain router errors. A clearer JSON-action protocol is appropriate, but its effectiveness must be verified with the stricter detector.

## Other corrections to the critique

- Strict structured output requires nullable optional fields to be present; it does not require non-null values. The recorded JSON contains many null values. Search limits of 10, 10, and 20 and source mode `full` were model choices allowed by the schema. They are not proof that strict output forced those choices.
- Those limits override the search default of eight. They do not bypass compact formatting: the harness still requested `--compact`.
- All source requests selected `full`, and successful requests targeted methods/functions. This pair does not test the live benefit of bounded class `auto` views.
- Reported source observation bytes of 4,142 include the 192-byte failed source call. Successful exact source observations total 3,950 bytes, compared with filesystem's 16,575 bytes, a reduction of 76.2%.
- The first two phases used six tool calls in each arm: Codemesh 12,085 bytes, filesystem 19,902 bytes. Codemesh had an observation-size advantage, but no call-count lead at that boundary.
- The restart recovery sequence is directly observable: bare `FieldInfo.get_default` failed, search returned exact IDs, and exact source retrieval succeeded. The failed call plus recovery search delivered 4,400 bytes. Structured references and conservative name resolution target this failure; eliminating those two calls remains a counterfactual until rerun.

## What remains useful, and the rerun gate

Both patches passed independent hidden acceptance tests. Successful tool-output bytes were 17,684 versus 23,630, a 25.2% reduction, and both arms required nine tool attempts before the first correct edit. These describe the observed trajectories. Router contamination and one paired task prevent clean causal or broad quality claims. Billed USD is unavailable.

For the next strict run, distinguish the exact pre-turn startup warning from runtime stderr router errors. Preserve per-call error counts, raw stderr, provider events, usage, and elapsed time. A runtime router error should invalidate the call even when valid final JSON follows. Keep affected attempts in the journal and report their work separately; halt the diagnostic pilot if the configured protocol repeatedly cannot produce clean decisions. The analytics warning should remain visible as a separate diagnostic rather than being mislabeled a tool attempt.

Run a representative clean smoke before expanding the paired suite. Use a fresh same-provider filesystem baseline, symmetric restored handoffs, and the same fixed retrieval implementation in both arms. The primary gates remain hidden acceptance and calls/bytes to the first correct edit. Token and latency comparisons require clean, comparable traces; cost claims additionally require trustworthy billing data.

Original artifacts remain unchanged: [report](../../eval/results/vnext-luna-factory-pilot/report.json), [calls](../../eval/results/vnext-luna-factory-pilot/calls.jsonl), and [configuration fingerprint](../../eval/results/vnext-luna-factory-pilot/run_config.json).
