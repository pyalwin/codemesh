# Compact CLI observation replay

The compact formatter was applied to successful map/search/source observations
recorded in the graded Codemesh trajectories of the earlier pinned Pydantic
run. This is a byte replay of prior tool responses, not a new model run. It
cannot predict model tool choices, prompt-cache behavior, cost, or repair quality.

Reproduce with `npm run build && node eval/replay_compact.mjs
eval/results/vnext-agent-full/runs.jsonl`.

| Observation | Replayed calls | Original bytes | Compact bytes | Reduction |
|---|---:|---:|---:|---:|
| Search | 17 | 78,418 | 41,530 | 47.0% |
| Source | 23 | 91,012 | 78,237 | 14.0% |
| Map | 6 | 5,057 | 3,504 | 30.7% |
| **Total** | **46** | **174,487** | **123,271** | **29.4%** |

Only successful observations in the nine graded trajectories are included;
failed calls and ungraded attempts are excluded. The source formatter preserves
the exact `source` string. The new CLI also defaults compact search to eight
results, while this replay formats the results already returned by the old
run; it therefore does not attribute further savings to changing search depth.

In a separate CLI smoke check on the same pinned checkout, `adapt search`
folded a 19,054 byte `rg -n FieldInfo pydantic` observation to 14,793 bytes.
`adapt read --intent navigate` outlined an exact 50,646 byte read of
`pydantic/v1/fields.py` to 1,825 bytes. The edit-intent path returned the
original source unchanged. These are individual byte examples, not agent
trajectory results.

The one-pair Haiku pilot in `results/vnext-compact-pilot/` stopped before its
first model decision because Claude CLI reported a session usage limit. It
produced no cost or quality comparison. The prior full eval in
`VNEXT_RESULTS.md` remains the last completed live comparison.
