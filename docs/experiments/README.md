# Experiment Archive

Current implementation: [deterministic retrieval and context retention](graphify-deterministic.md).
The decision-model pilot is parked; its results remain in `eval/SELECTED_CONTEXT_PILOT.md`.

Historical evaluation results and iteration logs from Codemesh development.

These documents record the evolution of the tool from initial prototype through multiple architectural iterations. The primary benchmark results are in the parent `docs/` directory.

## Documents

- `pydantic-eval-results.md` — Early eval on pydantic (5 tasks, 3 models, baseline vs codemesh MCP)
- `pydantic-eval-responses.md` — Full prompts and responses for all 30 early eval runs
- `approach-iterations.md` — Architecture decision log with iteration notes from multi-tool benchmark comparison
- `map-navigation-eval.md` — Map-first versus filesystem navigation pilot on dependent Pydantic workflows
- [Pydantic repair workflow pilot](edit-workflow-eval.md) — seeded edits,
  fresh-session handoffs, hidden pytest, and matched filesystem/capsule/router
  comparisons.
