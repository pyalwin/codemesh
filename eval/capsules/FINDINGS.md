# Initial Pydantic capsule benchmark

Target: [`pydantic/pydantic`](https://github.com/pydantic/pydantic) at `5adfeaaaea947f81f0f01868604f219084ec151a`. Four fixed comprehension questions used eight distinct code units. Each question was answered once per representation with the Claude CLI `--model haiku`, no tools, and a fresh session. The same units were supplied in all three modes. Capsules used one cached, Haiku-generated behavior summary per unit, plus the declaration, source version, location, and observed calls/raises.

| Representation | Input tokens reported by CLI | Answer cost reported by CLI | Sum of call durations |
|---|---:|---:|---:|
| Raw source | 9,462 | $0.0380 | 34.6 s |
| Versioned capsules | 4,344 | $0.0227 | 23.0 s |
| Compact notation | 4,244 | $0.0221 | 26.4 s |

Capsules used **54% fewer input tokens** and **40% less reported answer cost** than raw source in this one pass. Compact notation saved only another **100 input tokens (2.3%)** relative to capsules. Manual inspection found all ten explicit rubric points covered in each mode after adding the `FieldValidatorModes` alias to the selected units. The earlier run without that alias showed why evidence selection matters: the compressed formats could not establish all accepted field-validator modes.

Generating the eight summaries once took 9,227 input tokens, 10,101 output tokens, about 92 seconds, and $0.0733 of CLI-reported cost. The answer-cost saving of capsules over raw source was $0.0153 for this four-question pass, implying a **roughly five-pass break-even** if the same summaries are reused, at the observed costs. This excludes indexing and storage costs. Summary generation is a material expense to reduce in the next iteration.

These numbers are a **pilot, not a performance claim**. There is one run per mode and question, hand-selected relevant units, and one well-documented Python library. The setup does not measure retrieval quality, edit-task correctness, compaction recovery, or whether an agent chooses to request source when a capsule is insufficient. Durations and output-token counts varied between calls. The `o200k_base` counts in the generated report are only a prompt-size proxy; the table above uses the CLI's actual input-token accounting.

Generated prompts, answers, usage, and the review sheet are in the git-ignored `eval/results/capsules-pydantic-v2/` directory. Run `experiment.py` as described in [README.md](README.md) to reproduce with the pinned target.
