# Map-first navigation pilot on Pydantic

## Question and setup

Does the new deterministic `codemesh_map` path help a small agent complete a
dependent source investigation with less work and equal quality?

The target is open-source Pydantic at commit
`5adfeaaaea947f81f0f01868604f219084ec151a` (732 indexed files, 19,751
symbols). Claude Haiku handled two existing three-phase workflows: serialization
(`model_dump` → `model_dump_json` → post-handoff impact) and validation
(`model_validate` → JSON sibling and setup → post-handoff guard scenario). There
were two seeded repeats per workflow and mode. Each model call resumed its
session until a model-written memory was handed to a fresh session for phase
three. The agent chose each simulated tool action through a structured action
protocol; observations came from the live Codemesh CLI or the target source
tree. The first phase required a source read. Later phases could reuse prior
evidence. Both modes had the same 20-decision cap per phase.

- **Filesystem:** literal `rg` plus bounded, numbered source reads.
- **Map:** live `codemesh_map` and exact-ID `codemesh_source`, with the same
  filesystem tools available as fallback. The prompt asked the agent to start
  with the map. No fixed symbol catalog was supplied.

The Pydantic index was already built; indexing cost is excluded. The model
cost is the CLI's reported USD total. Input tokens include uncached,
cache-creation, and cache-read tokens. Tool observations were supplied as new
user turns, as in the prior resumed-session capsule pilot; these were not
native MCP tool calls. The frozen workflow questions and rubrics are in
`eval/map_navigation_tasks.json` (copied unchanged from the earlier capsule
workflow set before this run).

## Results

The full attempted set is important for reliability. One map serialization
attempt answered phases one and two but the post-handoff call exhausted the
runner's **$0.12 per-call cap** after a 178,668-token cache-creation charge.
That attempt remains in the totals and counts as incomplete. The cap was a
runner limit, not a wrong answer from the model.

| All attempts (four per mode) | Filesystem | Map with fallback |
| --- | ---: | ---: |
| Complete trajectories | 4/4 | 3/4 |
| Final core conclusion correct | 4/4 | 3/4 |
| Final fully acceptable | 2/4 | 2/4 |
| Handoff memories acceptable | 4/4 | 3/4 |
| Tool calls | 27 | 40 |
| Source reads | 15 | 19 |
| Input tokens | 1,883,418 | 1,323,224 |
| Cache-read tokens | 1,695,053 | 1,155,975 |
| Model cost | $0.619108 | $0.893377 |

For a like-for-like cost comparison, excluding the interrupted pair leaves
three completed workflow/repeat pairs:

| Three completed pairs | Filesystem | Map with fallback |
| --- | ---: | ---: |
| Final rubric facts | 8/9 | 8/9 |
| Acceptable handoff memories | 3/3 | 2/3 |
| Tool calls | 22 | 33 |
| Source reads | 11 | 17 |
| Input tokens | 1,627,669 | 1,199,282 |
| Cache-read tokens | 1,469,022 | 1,071,873 |
| Model cost | $0.516914 | $0.432323 |
| Model wall time | 215.0 s | 223.4 s |

The completed-pair map cost is 16.4% lower and input tokens are 26.3% lower,
but the map arm used 50% more tool calls and 54.5% more source reads. With all
attempts included, map cost is higher because of the failed call. Four runs
per mode are too few to treat either cost difference as a stable estimate.

| Completed pair | Filesystem cost | Map cost |
| --- | ---: | ---: |
| Validation, repeat 1 | $0.162453 | $0.138239 |
| Validation, repeat 2 | $0.130997 | $0.182046 |
| Serialization, repeat 2 | $0.223464 | $0.112039 |

## What the traces show

Map adoption was weak. Across the **three completed pairs**, map-mode agents
made eight map calls, eight grep calls, and 24 filesystem fallback actions.
Only one exact-ID source call occurred in those three map trajectories. The
other source reads came through bounded filesystem reads. The apparent paired
cost saving therefore cannot be attributed to successful graph navigation.
The agent often opened `repo` and `dir:pydantic`, then used grep to locate a
named method more directly. A complete map path to a method takes several
levels and can require a paginated class page.

The handoff exposed a correctness problem. In the completed map serialization
repeat, the memory said **both** `model_dump` and `model_dump_json` forward
`indent` and `ensure_ascii`; only the JSON method accepts those options. Its
final answer got the main impact decision right but omitted the requested
`round_trip`/`indent`/`ensure_ascii` distinction. Both filesystem serialization
finals also missed a requested detail (`.decode()`), so neither arm achieved a
fully acceptable serialization final. Both modes got the validation final
scenario right in both repeats.

The model's cache behavior varied substantially. One interrupted map call
alone reported 178,668 cache-creation tokens and $0.363459, above the
per-call budget. Summing all attempted costs and then claiming a navigation
effect would confound this provider/session variation with tool behavior.

## Interpretation and next test

This pilot does **not** establish a token, cost, or quality win for map-first
navigation. The new API works, but named-symbol tasks made `grep` a shorter
route than walking four map levels; the model mostly took that route. The
next change to test is an exact, deterministic resolver for a known file or
symbol name, returning a graph ID and its immediate neighborhood in one call.
It should preserve map traversal for unfamiliar areas. Then rerun these
paired tasks with a higher per-call cap and add held-out issue/patch tasks
where the agent must discover the relevant area without a method name in the
prompt. Measure accepted patches, repeated source spans, wall time, and paid
cost, including index refresh cost when relevant.

This is an exploratory, unblinded pilot on one repository and one model. A
single reviewer scored the saved answers against the prewritten rubrics;
citation precision and writing style were not scored. The workflow asks for
code understanding, not actual edits or tests, so it cannot establish patch
success. The raw trajectories, usage, answer hashes, and manual labels are in
`eval/results/map-navigation-final/` in this workspace. Reproduce with:

```bash
python3.11 eval/map_navigation_eval.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --modes filesystem,map --repeats 2 --max-turns 20 --budget-usd 0.12 \
  --out eval/results/map-navigation-final
```

## Follow-up: interpret grep calls at the harness boundary

A follow-up prototype intercepted the familiar `grep(query)` action in the
simulated harness. It routes only declaration-shaped queries (`def name`,
`async def name`, `class Name`) to the Codemesh SQLite index. The router checks
source SHA-256 against the indexed file hash and compares the candidate line
set with a shadow native `rg` call. If the sets differ, the observation is
native grep unchanged. On parity, it returns the same `file:line:source`
matches with an exact `codemesh-id` annotation. Arbitrary body text and
unsupported queries remain native. The agent can use the ID with the existing
exact `source` action.

The same two workflows ran for two repeats in `routed` mode. This was a later
run rather than a simultaneous three-arm batch, and the per-call CLI cap was
raised to $1.00 to avoid the previous budget interruption. None of these
routed calls hit that cap. The comparison below is descriptive; provider cache
variation and model sampling remain confounders.

| Four attempts per mode | Filesystem | Explicit map | Routed grep |
| --- | ---: | ---: | ---: |
| Complete trajectories | 4/4 | 3/4 | 4/4 |
| Final core conclusion correct | 4/4 | 3/4 | 4/4 |
| Final fully acceptable | 2/4 | 2/4 | 3/4 |
| Handoff memories acceptable | 4/4 | 3/4 | 2/4 |
| Tool calls | 27 | 40 | 32 |
| Source reads | 15 | 19 | 21 |
| Input tokens | 1,883,418 | 1,323,224 | 1,862,650 |
| Model cost | $0.619108 | $0.893377 | $0.565158 |

The router chose Codemesh for **4 of 11** grep calls and native grep for the
other seven. In one validation run, the agent used a routed `def
model_validate` result to call `source` on both `model_validate` and
`model_validate_json`. In the other runs, agents still preferred file reads
after seeing IDs. Routed source reads totaled 21 versus 15 in filesystem
mode, so exposing IDs alone did not reduce rereading in this pilot. One
validation answer also cited incorrect line numbers after an exact source
read; citation precision was outside the scored rubric.

All routed final answers got the core decision right, and three of four met
every final rubric fact. The two serialization handoff memories were weaker:
one incorrectly said both methods accept `mode`, and the other omitted
`.decode()` plus the forwarding distinction. That makes the routed arm's
handoff quality worse than the filesystem arm's despite the higher final
answer count. The cost difference of about 8.7% is too small and noisy to
claim a stable saving, especially because the parity gate still executes
native grep. This prototype demonstrates **safe call interpretation and
occasional exact-ID adoption**, not a proven efficiency improvement.

Router code and focused parity/staleness tests are in `eval/grep_router.py`
and `eval/test_grep_router.py`. Full routed trajectories and the hash-pinned
quality audit are in `eval/results/grep-router-v1/`. Reproduce with:

```bash
python3.11 eval/test_grep_router.py
python3.11 eval/map_navigation_eval.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --modes routed --repeats 2 --max-turns 20 --budget-usd 1.0 \
  --out eval/results/grep-router-v1
```

## Follow-up: verified grep spans and numbered read routing

The seven native fallbacks in the routed-grep v1 traces were two searches for
`__pydantic_validator__`, two for `BaseModel model_dump`, two for bare
`model_dump`, and one for the alias guard expression. The new router can
annotate the two bare `model_dump` searches while preserving every native call
site. The other five still need native search because they are not indexed
symbol declarations. Declaration searches continue to require exact parity
with a shadow `rg` result. All annotations require a matching indexed SHA-256
file hash.

Grep annotations now give the indexed line span and an AST-derived return
delegate with exact keyword bindings. This AST hint matters for dynamic calls
such as `self.__pydantic_serializer__.to_python(...)`: the graph has no outgoing
`calls` edge for that delegate. The routed protocol exposes only `grep`, `read`,
and `answer`; it no longer offers an unnumbered `source(node)` result. A routed
read returns numbered source. If its slice ends before a verified return, it
adds a short, line-cited tail with the delegate and bindings. An exact repeated
span within the same model session returns a source-hash stub and the tail;
the dedup state resets at the explicit handoff. Stale indexes return ordinary
numbered source without a tail or dedup stub.

Ten router tests and two protocol tests cover parity, bare-name call-site
preservation, source staleness, the `.decode()` tail, duplicate reads, handoff
reset, and removal of the routed `source` action. The router remains an eval
prototype, not an MCP or host grep interception. A shadow native grep still
runs on every query, so no search CPU saving is claimed.

One paired Haiku repeat per workflow ran on the same pinned Pydantic commit.
The two modes ran in a shuffled order. This run used a $1.00 per-call cap and
excluded initial indexing cost.

| Two workflows, one repeat each | Filesystem | Routed grep + read |
| --- | ---: | ---: |
| Complete trajectories | 2/2 | 2/2 |
| Final rubric facts (manual inspection) | 5/6 | 5/6 |
| Handoff rubric facts (manual inspection) | 6/6 | 6/6 |
| Tool calls | 20 | 12 |
| Source reads | 15 | 8 |
| Grep calls | 5 | 4 (2 indexed, 1 hybrid, 1 native) |
| Verified-tail reads / dedup reads | — | 0 / 0 |
| Input tokens, including cache reads | 670,927 | 724,229 |
| Reported model cost | $1.015693 | $0.291159 |

Both final answers reached the core conclusion in both modes. Both
serialization finals omitted the JSON-only `indent` and `ensure_ascii`
forwarding distinction; the filesystem final also omitted `round_trip`.
The filesystem validation final wrote an incorrect shorthand evaluation of
the guard while reaching the correct decision. The routed validation final
misspelled the `_internal/_model_construction.py` citation path. Those issues
should be considered alongside the rubric fact counts.

The routed arm used fewer reads, but **none of those reads triggered the new
tail or dedup behavior**. The difference reflects the agent's chosen path in
these samples, not demonstrated read-router savings. Model cache composition
also differed substantially: routed had more total input tokens but far fewer
cache-creation tokens and lower reported cost. With one repeat per workflow,
the cost difference is not a stable estimate. The next acceptance gate is a
held-out patch-and-pytest workflow with symptom-based prompts, explicit
handoff, and per-mode patch success, repeated source bytes, latency, and cost.
The complete raw pilot is in `eval/results/read-router-v2/` (git-ignored).

```bash
python3.11 -m unittest discover -s eval -p 'test_grep_router.py' -q
python3.11 -m unittest discover -s eval -p 'test_map_navigation_eval.py' -q
python3.11 eval/map_navigation_eval.py \
  --repo /tmp/codemesh-pydantic-benchmark \
  --modes filesystem,routed --repeats 1 --budget-usd 1.0 \
  --out eval/results/read-router-v2
```
