<div align="center">

# Codemesh

**Intelligent code knowledge graph for AI coding agents**

**71% cheaper, 72% faster, 82% fewer tool calls** vs baseline Grep+Read\
on 6 real-world repos (Sonnet 4.6) — from a single `codemesh index`.

[![npm](https://img.shields.io/npm/v/@pyalwin/codemesh)](https://www.npmjs.com/package/@pyalwin/codemesh)
[![Tests](https://img.shields.io/badge/tests-124%20passed-brightgreen)]()
[![License](https://img.shields.io/badge/license-MIT-blue)]()
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-blue)]()
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)]()

[Benchmarks](#benchmarks) &middot; [Quick Start](#quick-start) &middot; [Integrations](#client-integrations) &middot; [Write-Back](#agent-write-back-the-graph-that-gets-smarter) &middot; [How It Works](#how-it-works) &middot; [API Reference](#mcp-tools) &middot; [Full Results](docs/benchmark-results.md)

</div>

---

## The Problem

AI coding agents waste **40-80% of their tokens on discovery** — grepping through files, reading irrelevant code, and rebuilding context they've already seen in previous sessions.

On a 600-file codebase, a typical exploration task involves 10+ file reads before the agent even knows what's relevant.

```
Before:  Agent → Grep → 50 matches → Read 10 files → Understand → Work
After:   Agent → codemesh_map → expand chosen branches → read exact symbols → Work
```

Codemesh is an MCP server that gives agents a persistent, queryable knowledge graph. The graph gets smarter over time: agents write back what they learn, so the next session starts informed.

---

## Benchmarks

Benchmarked on 6 real-world codebases (Alamofire, Excalidraw, VS Code, Swift Compiler, pydantic-validators, pydantic-basemodel) with Claude Sonnet 4.6, compared alongside baseline and graph-based approaches for context.

Full methodology, per-repo breakdowns, and pairwise comparisons: [`docs/benchmark-results.md`](docs/benchmark-results.md) | [Early pydantic evals](docs/experiments/pydantic-eval-results.md)

These historical runs used the earlier answer-first workflow. The map-first
navigation workflow described below is evaluated in an exploratory
[Pydantic pilot](docs/experiments/map-navigation-eval.md); the figures in this
section should not be read as its measured performance.

### Cost

| Mode | Alamofire | Excalidraw | VS Code | Swift Compiler[^swift] | pydantic-validators | pydantic-basemodel | **Avg** |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | $0.54 | $0.89 | $0.21 | $0.83 | $1.32 | $0.78 | $0.76 |
| **Codemesh MCP** | **$0.25** | **$0.21** | **$0.16** | **$0.23** | **$0.33** | **$0.13** | **$0.22** |
| Codemesh CLI | $0.67 | $0.51 | $0.16 | $0.83 | $1.00 | $0.18 | $0.56 |
| Codegraph | $0.37 | $0.56 | $0.57 | $0.74 | $0.29 | $0.19 | $0.45 |

### Time

| Mode | Alamofire | Excalidraw | VS Code | Swift[^swift] | pydantic-v | pydantic-b | **Avg** |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 180s | 191s | 87s | 199s | 352s | 232s | 207s |
| **Codemesh MCP** | **78s** | **45s** | **35s** | **87s** | **72s** | **32s** | **58s** |
| Codemesh CLI | 226s | 177s | 62s | 227s | 235s | 51s | 163s |
| Codegraph | 134s | 180s | 192s | 199s | 75s | 60s | 140s |

### Tool calls (agent turns)

| Mode | Alamofire | Excalidraw | VS Code | Swift[^swift] | pydantic-v | pydantic-b | **Avg** |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 31 | 48 | 12 | 29 | 84 | 65 | 45 |
| **Codemesh MCP** | **9** | **5** | **3** | **14** | **14** | **3** | **8** |
| Codemesh CLI | 30 | 32 | 12 | 56 | 64 | 9 | 34 |
| Codegraph | 31 | 35 | 44 | 44 | 20 | 12 | 31 |

### Quality (1–10, LLM-as-judge)

| Mode | Alamofire[^alamo] | Excalidraw | VS Code | Swift Compiler | pydantic-validators | pydantic-basemodel | **Avg** |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | n/a | 9 | 8 | 7 | 2 | 9 | 7.0 |
| **Codemesh MCP** | 9 | 9 | 7 | 8 | 7 | 7.8 | **7.9** |
| Codemesh CLI | 9 | 7 | 7 | 9 | 1 | 8.4 | 6.9 |
| Codegraph | 8 | 9 | 8.7 | 8 | 8 | 9 | **8.4** |

### Cost savings: Codemesh MCP vs Baseline

| Repo | Baseline | Codemesh MCP | **Cost saved** | **Time saved** |
|---|---:|---:|---:|---:|
| Alamofire | $0.54 | $0.25 | **−54%** | **−57%** (180s → 78s) |
| Excalidraw | $0.89 | $0.21 | **−76%** | **−76%** (191s → 45s) |
| VS Code | $0.21 | $0.16 | **−24%** | **−60%** (87s → 35s) |
| Swift Compiler[^swift] | $0.83 | $0.23 | **−72%** | **−56%** (199s → 87s) |
| pydantic-validators | $1.32 | $0.33 | **−75%** | **−79%** (352s → 72s) |
| pydantic-basemodel | $0.78 | $0.13 | **−83%** | **−86%** (232s → 32s) |
| **Average** | **$0.76** | **$0.22** | **−71%** | **−72%** |

> [!NOTE]
> **Codemesh MCP** achieves the lowest cost and fastest time of any mode tested — **71% cheaper and 72% faster than baseline** on average across 6 repos, using **82% fewer tool calls** (8 vs 45). Quality is comparable to baseline (7.9 vs 7.0); Codegraph edges Codemesh on quality (8.4) but at roughly double the cost ($0.45 vs $0.22). Every repo shows cost and time savings — including the comprehension-heavy queries (Excalidraw, pydantic-basemodel) that regressed in prior builds of codemesh.

[^swift]: Swift Compiler's codemesh index failed to complete (indexer regression on 30k+ file codebases — see [known issues](docs/benchmark-results.md)). The codemesh numbers above reflect agent behavior with an empty retrieval graph, falling back to Read + LSP — still ahead of baseline, but unrepresentative of codemesh's capability on a properly-indexed Swift repo.
[^alamo]: Baseline for Alamofire hit a judge error (score recorded as 0 but not meaningful); excluded from the Baseline average.

---

## Quick Start

### 1. Install

```bash
npm install -g @pyalwin/codemesh
```

Or run directly without installing:

```bash
npx -y @pyalwin/codemesh --help
```

<details>
<summary>Build from source</summary>

```bash
git clone https://github.com/pyalwin/codemesh.git
cd codemesh
npm install && npm run build
npm link
```

</details>

> Verify the install: `codemesh --version` should print the package version.

### 2. Index your project

```bash
cd /your/project
codemesh index
```

The default index and retrieval path uses one SQLite database in `.codemesh/`.
It does not generate embeddings or require a vector database. Existing vector
artifacts from older runs are left untouched but are not used by this path.

```
Indexed 656 files
  Symbols found:  16733
  Edges created:  33266
  Duration:       10009ms
  PageRank:       13843 nodes scored
```

### 3. Choose your mode

Codemesh offers two ways to integrate with AI agents:

<details open>
<summary><strong>Option A: MCP Server</strong> (structured tool calls)</summary>

Add to your Claude Code MCP config (`~/.claude/mcp-servers.json` or project `.mcp.json`):

```json
{
  "mcpServers": {
    "codemesh": {
      "command": "npx",
      "args": ["-y", "@pyalwin/codemesh"],
      "env": {
        "CODEMESH_PROJECT_ROOT": "/path/to/your/project"
      }
    }
  }
}
```

The agent gets native MCP tools:

- `codemesh_map` — browse one level of the indexed repository or follow a link
- `codemesh_source` — read a revision-checked source view by symbol ID; `ifHash` avoids repeat delivery
- `codemesh_inspect` — inspect an exact symbol and its direct calls with bounded, exact source blocks
- `codemesh_hydrate` — verify saved symbol references and reopen exact source after a handoff
- `codemesh_answer` — optional ranked context assembly
- `codemesh_explore` — search, context (multi-target), impact
- `codemesh_trace` — follow call chains
- `codemesh_enrich` / `codemesh_workflow` — write back
- `codemesh_recall` — retrieve notes and workflows with freshness status
- `codemesh_status` — health check

**Best for:** Opus, structured workflows, enrichment/write-back

</details>

<details>
<summary><strong>Option B: CLI Mode</strong> (via Bash — zero MCP overhead)</summary>

No MCP config needed. The agent calls codemesh directly via Bash:

```bash
export CODEMESH_PROJECT_ROOT=/path/to/your/project

# Navigate one level at a time, then read a selected symbol:
codemesh explore map
codemesh explore map --node 'dir:src'
codemesh explore map --node 'file:src/server.ts'
codemesh explore source 'symbol:src/server.ts:createServer'
codemesh explore source 'symbol:src/server.ts:createServer' --mode preview
codemesh explore inspect 'symbol:src/server.ts:createServer' --depth 1 --max-symbols 4 --compact
codemesh explore hydrate --references '[{"id":"symbol:src/server.ts:createServer","bodyHash":"HASH"}]' --compact

# Other exploration commands:
codemesh explore search "request flow"
codemesh explore context Source/Core/Session.swift Source/Core/Request.swift
codemesh explore trace Session.request --depth 5
codemesh explore recall --path src/server.ts
codemesh remember --path src/server.ts --summary "Server tool registration" --session my-task
```

Exploration and write commands return JSON to stdout. No MCP server process is required.

**Best for:** Sonnet/Haiku, speed-sensitive workflows, simpler setup

</details>

### Which mode should I use?

| | MCP Server | CLI Mode |
|---|---|---|
| **Setup** | MCP config file | Just `export CODEMESH_PROJECT_ROOT` |
| **Invocation** | Persistent MCP process | CLI subprocess |
| **Enrichment** | Native `codemesh_enrich` tool | `codemesh remember ...` |
| **Retrieval** | Shared SQLite-backed handlers | Same handlers and JSON data |
| **Recommended** | Hosts with reliable MCP tool use | Deterministic shell workflows |

### 4. Use it

The agent can choose a path through the persistent graph before reading code:

```
You: "Find how pydantic handles validation"

Agent calls: codemesh_map({})
       gets: immediate repository directories and files, each with an ID

Agent calls: codemesh_map({ node: "dir:pydantic" })
       gets: immediate files and subdirectories

Agent calls: codemesh_map({ node: "file:pydantic/main.py" })
       gets: top-level symbols, including BaseModel

Agent calls: codemesh_map({ node: "symbol:pydantic/main.py:BaseModel" })
       gets: methods, including model_validate

Agent calls: codemesh_source({ id: "symbol:pydantic/main.py:BaseModel.model_validate" })
       reads: only the selected symbol's source

Agent calls: codemesh_enrich({ path: "pydantic/functional_validators.py",
               summary: "Primary V2 validator API..." })
       saves: summary for next session
```

---

## Client Integrations

Codemesh speaks the Model Context Protocol, so any MCP-compatible client can use it. Paste one of the snippets below, restart the client, and the `codemesh_*` tools show up in the agent's toolbox.

<details open>
<summary><strong>Claude Code</strong> (CLI)</summary>

Add to `~/.claude/mcp-servers.json` (user-wide) or `.mcp.json` (project-local):

```json
{
  "mcpServers": {
    "codemesh": {
      "command": "npx",
      "args": ["-y", "@pyalwin/codemesh"],
      "env": {
        "CODEMESH_PROJECT_ROOT": "/absolute/path/to/your/project"
      }
    }
  }
}
```

</details>

<details>
<summary><strong>Claude Desktop</strong> (macOS / Windows app)</summary>

Edit `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "codemesh": {
      "command": "npx",
      "args": ["-y", "@pyalwin/codemesh"],
      "env": {
        "CODEMESH_PROJECT_ROOT": "/absolute/path/to/your/project"
      }
    }
  }
}
```

Restart Claude Desktop. Codemesh's tools will appear in the tool picker (hammer icon).
</details>

<details>
<summary><strong>Cursor</strong> — stop the agent from wandering your codebase</summary>

Cursor reads `.cursor/mcp.json` per project (or `~/.cursor/mcp.json` for all projects):

```json
{
  "mcpServers": {
    "codemesh": {
      "command": "npx",
      "args": ["-y", "@pyalwin/codemesh"],
      "env": {
        "CODEMESH_PROJECT_ROOT": "${workspaceFolder}"
      }
    }
  }
}
```

Open **Settings → MCP**, confirm `codemesh` is green, then mention it in a prompt (`@codemesh how does auth work?`) to nudge the agent toward graph queries instead of recursive Grep.
</details>

<details>
<summary><strong>Windsurf / VS Code (Continue)</strong></summary>

Add to `~/.continue/config.json` under `experimental.modelContextProtocolServers`:

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "stdio",
          "command": "npx",
          "args": ["-y", "@pyalwin/codemesh"],
          "env": {
            "CODEMESH_PROJECT_ROOT": "/absolute/path/to/your/project"
          }
        }
      }
    ]
  }
}
```
</details>

---

## Agent Write-Back: the graph that gets smarter

Every other code-intelligence tool indexes your repo once and hands the agent a read-only view. Codemesh lets the agent **teach the graph** as it works — summaries, workflows, and cross-concept links persist across sessions and survive re-indexing.

```ts
// Session 1 — agent reads unfamiliar code, then writes back what it learned.
codemesh_enrich({
  path: "pydantic/functional_validators.py",
  summary: "Primary V2 validator API. `@field_validator` wraps "
         + "`_decorators.FieldValidatorDecoratorInfo`; `mode='before'|'after'` "
         + "toggles pre/post-coercion execution. Extends BaseValidator.",
  sessionId: "validation-investigation-1"
})

// Session 1 — agent traces a multi-file flow, records the path.
codemesh_workflow({
  name: "pydantic field validation",
  description: "Request → BaseModel.__init__ → SchemaValidator → field_validator",
  files: [
    "pydantic/main.py",
    "pydantic/_internal/_model_construction.py",
    "pydantic/functional_validators.py"
  ]
})

// Session 2 (days later) — another agent navigates the same indexed code.
codemesh_map({ node: "file:pydantic/functional_validators.py" })
// → returns stable symbol IDs without reading their bodies.
codemesh_explore({ action: "context", path: "pydantic/functional_validators.py" })
// → includes the earlier agent's concept alongside indexed relationships.
```

The graph now knows things no static analyzer could infer: why a file matters, which files move together, what a maintainer called a concept. Re-indexing rebuilds the structural layer (files, symbols, imports, calls) but **preserves every enrichment** — entries only go stale when their referenced files change.

See `codemesh_enrich` and `codemesh_workflow` under [MCP Tools](#mcp-tools).

---

## How It Works

```
                      ┌──────────────────────────────────┐
                      │         Knowledge Graph           │
                      │                                   │
                      │  ┌──────────┐ ┌───────────────┐  │
                      │  │Structural│ │   Semantic     │  │
                      │  │  (auto)  │ │   (agents)    │  │
                      │  │          │ │               │  │
                      │  │ files    │ │ summaries     │  │
                      │  │ symbols  │ │ workflows     │  │
                      │  │ imports  │ │ concepts      │  │
                      │  │ calls    │ │ enrichments   │  │
                      │  └──────────┘ └───────────────┘  │
                      │                                   │
                      │  ┌──────────┐ ┌───────────────┐  │
                      │  │   Git    │ │   Search      │  │
                      │  │  Intel   │ │               │  │
                      │  │          │ │ FTS5 (exact)  │  │
                      │  │ hotspots │ │ Trigram (fuzzy)│  │
                      │  │ co-change│ │ notes + cache │  │
                      │  │ churn    │ │ PageRank      │  │
                      │  └──────────┘ └───────────────┘  │
                      │                                   │
                      │          SQLite only              │
                      └────────────┬──────────────────────┘
                                   │
                      ┌────────────┴──────────────────────┐
                      │    MCP Server / CLI (shared core)  │
                      │                                    │
                      │  map · source · answer · explore    │
                      │  trace · recall · enrich · workflow │
                      │  status                            │
                      └────────────────────────────────────┘
```

**Structural layer** (automatic) — Tree-sitter parses your code into files, symbols (functions, classes, methods), and relationships (imports, calls, extends). Rebuilt on each index.

**Semantic layer** (agent-built) — As agents work with your code, they write back summaries and workflow paths. These survive re-indexing and accumulate across sessions. Invalidated when referenced files change.

---

## MCP Tools

| Tool | Purpose | Example |
|---|---|---|
| `codemesh_map` | Bounded, one-level navigation through directories, files, symbols, and graph links | `codemesh_map({ node: "dir:src", limit: 30 })` |
| `codemesh_source` | Exact source for a selected symbol ID | `codemesh_source({ id: "symbol:src/auth.ts:login" })` |
| `codemesh_inspect` | Exact source for a symbol plus direct call evidence, within a byte and symbol budget | `codemesh_inspect({ id: "symbol:src/auth.ts:login", depth: 1 })` |
| `codemesh_hydrate` | Verify saved symbol references and reopen exact source after context loss | `codemesh_hydrate({ references: [{ id: "symbol:src/auth.ts:login", bodyHash: "..." }] })` |
| `codemesh_answer` | Optional ranked context assembly with source snippets | `codemesh_answer({ question: "How does auth work?" })` |
| `codemesh_explore` | Search, context (multi-target), impact analysis | `codemesh_explore({ action: "search", query: "auth" })` |
| `codemesh_trace` | Follow call chains with source code | `codemesh_trace({ symbol: "login", depth: 5 })` |
| `codemesh_enrich` | Write back what you learned for future sessions | `codemesh_enrich({ path: "src/auth.py", summary: "..." })` |
| `codemesh_workflow` | Record multi-file workflow paths | `codemesh_workflow({ name: "login flow", files: [...] })` |
| `codemesh_status` | Graph health check | `codemesh_status()` |

---

## CLI

```bash
codemesh index                          # structural + git intel + pagerank
codemesh rebuild                        # force re-index; preserves saved knowledge
codemesh status                         # graph statistics

codemesh explore map                    # repository root
codemesh explore map --node 'dir:src'   # expand one branch
codemesh explore map --node 'symbol:src/auth.ts:login' --relation calls
codemesh explore source 'symbol:src/auth.ts:login'
codemesh explore source 'symbol:src/auth.ts:login' --if-hash HASH  # unchanged reference if bytes match
codemesh explore source 'symbol:src/auth.ts:login' --force         # rehydrate exact source
codemesh explore inspect 'symbol:src/auth.ts:login' --depth 1 --compact
codemesh explore hydrate --references '[{"id":"symbol:src/auth.ts:login","bodyHash":"HASH"}]' --compact
codemesh explore answer "question"      # optional ranked context assembly
codemesh explore search "query"         # SQLite FTS5 + trigram
codemesh explore search get_default --path pydantic --scope symbols --max-bytes 4096
codemesh explore inspect 'symbol:src/auth.py:login' --direction both --max-candidates 16
codemesh explore recall --path file     # prior notes with freshness
codemesh explore context file1 file2    # multi-target context
codemesh explore trace symbol --depth 5 # follow call chains
codemesh explore impact file            # reverse dependencies
codemesh remember --path file --summary "note" --session task-id
codemesh workflow --name flow --description "flow description" --file first.ts --file second.ts
codemesh explore search "query" --compact  # 8 candidates with IDs and match reasons
codemesh explore source 'symbol:src/auth.ts:login' --mode full --compact
codemesh explore source 'symbol:src/auth.ts:login' --mode auto --compact
rg -n 'login' src | codemesh adapt search  # reversible fold when smaller
cat src/auth.ts | codemesh adapt read --path src/auth.ts --intent navigate
```

`--compact` is available for `map`, `search`, `source`, `recall`, `inspect`, and `hydrate`. It removes
repeated metadata from CLI JSON; `source` still includes the exact requested code
and a content hash. Navigation maps, search, `inspect`, and `hydrate` emit bounded JSON without
pretty printing; their compact form preserves exact source and freshness hashes.
`hydrate` also accepts `--references-file PATH` for a JSON array saved on disk.
Bounded responses omit the extra project-root wrapper so the final JSON stays
within the requested budget. Other commands retain their existing response format.

For handoffs, save a symbol's exact ID and `bodyHash` from a fresh source result.
`codemesh_hydrate` checks that hash against live source before returning full code.
If a saved `hash` came from `fold` or `preview`, include its `mode`; that hash
checks only the rendered view, so include `bodyHash` to verify the whole symbol.
Inspect and hydrate report omitted blocks explicitly when a budget is reached.
Both default to four source blocks and a 24 KiB JSON response budget. `inspect`
follows at most one hop of conservatively resolved local Python functions and
imports. Use `--direction incoming` or `both` to inspect candidate callers too;
`--max-candidates` bounds caller verification (default 32, maximum 128). Indexed
edges are hints: each delivered caller is checked against its current Python
binding. Incoming discovery is explicitly incomplete when edges are missing;
an empty result never proves that a symbol has no callers. Dynamic receivers,
ambiguous names, and uncertain bindings remain
explicitly unresolved; other languages expose unverified graph candidates.
These are static bindings, not guarantees about runtime behavior. Hydration
returns live-checked code; clients decide when to invoke it after a handoff and
which evidence to retain in their model context.

`--mode auto` is an explicit navigation view: symbols up to 120 lines and 12KiB
are returned in full; larger symbols return a bounded preview with omitted ranges
and a hint to map their members. Explicit `--mode full` always returns exact source.
Search ranks exact identifiers and exact qualified/leaf names first, then query
term coverage and lexical relevance, with popularity used only to break ties.
For an opt-in structural hint, `--exact-tie-break path-depth` prefers shallower
paths before popularity on equally ranked exact-name/leaf matches. MCP exposes
the same `exactTieBreak` option on `codemesh_explore`. Explicit IDs and path
filters still select deeper implementations; depth does not establish which
implementation is canonical. The default remains `pagerank`.
`--path` restricts candidates to a file or directory before SQLite result caps;
`src` does not match `src-other`. Compact search includes signatures and spans.
Search and navigation default to an 8192-byte JSON budget; `--max-bytes` accepts
512..65536. Omitted results are explicit. Navigation's cursor always points at
the first unreturned entry; if that entry alone exceeds the budget, increase
`--max-bytes` and reuse the cursor. Source code remains behind `source`/`inspect`.

The [deterministic Graphify follow-up](docs/experiments/graphify-deterministic.md)
documents the single-agent design and focused context policy. Context replacement
belongs to the harness: MCP/CLI tools cannot remove earlier client messages.
The [controlled next iteration](eval/NEXT_ITERATION.md) documents lean eval
instructions, optional search ranking, and two multi-file handoff fixtures.

Source retrieval also accepts a unique symbol name, such as `MyType.myMethod`.
An ambiguous name returns exact candidate IDs and spans; it never guesses an
overload from its size. Exact IDs remain the preferred reference across sessions.
`adapt` reads a native tool observation from stdin. Search folding preserves every
match and falls back for unsupported formats. Read outlining requires a complete
byte-exact match with a fresh indexed file and explicit navigation intent; edit
reads and partial reads pass through unchanged. `--json` reports the route and
reason instead of emitting only the adapted observation.

---

## Optional: Hooks & Skills

<details>
<summary><strong>Skill</strong> — teaches agents the graph-first workflow</summary>

Copy `skills/codemesh.md` to `~/.claude/skills/` or your project's `.claude/skills/`.

```bash
# Install the skill so Claude Code loads the workflow automatically
cp /path/to/codemesh/skills/codemesh.md /your/project/.claude/skills/
```

The skill instructs agents to query the graph before using Grep/Read, and to write back via `codemesh_enrich` after reading code.
</details>

<details>
<summary><strong>Hooks</strong> — automatic pre-read context injection</summary>

Add to `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Read",
      "hooks": [{
        "type": "command",
        "command": "/path/to/codemesh/hooks/pre-read.sh"
      }]
    }],
    "PostToolUse": [{
      "matcher": "Read",
      "hooks": [{
        "type": "command",
        "command": "/path/to/codemesh/hooks/post-read.sh"
      }]
    }]
  }
}
```

- **Pre-read** — Adds a cached summary marked non-stale alongside the pending Read result
- **Post-read** — Nudges the agent to enrich after reading unfamiliar files
</details>

---

## Supported Languages

<table>
<tr>
<td>TypeScript</td><td>JavaScript</td><td>Python</td><td>Go</td><td>Rust</td><td>Java</td><td>C#</td>
</tr>
<tr>
<td>Ruby</td><td>PHP</td><td>C</td><td>C++</td><td>Swift</td><td>Kotlin</td><td>Dart</td>
</tr>
</table>

Any language with a [tree-sitter](https://tree-sitter.github.io/tree-sitter/) grammar can be added.

---

## Graph Data Model

### Nodes

| Type | Source | Key Fields |
|:--|:--|:--|
| `file` | Static (tree-sitter) | `path`, `hash`, `last_indexed_at` |
| `symbol` | Static (tree-sitter) | `name`, `kind`, `file_path`, `line_start`, `line_end`, `signature` |
| `concept` | Agent-written | `summary`, `last_updated_by`, `stale` |
| `workflow` | Agent-written | `description`, `file_sequence`, `last_walked_at` |

### Edges

| Type | Direction | Source |
|:--|:--|:--|
| `contains` | file &rarr; symbol | Static |
| `imports` | file &rarr; file | Static |
| `calls` | symbol &rarr; symbol | Static |
| `extends` | symbol &rarr; symbol | Static |
| `describes` | concept &rarr; file/symbol | Agent |
| `related_to` | concept &rarr; concept | Agent |
| `traverses` | workflow &rarr; file | Agent |

---

## Architecture

```
codemesh/
├── src/
│   ├── index.ts              # MCP server entry (stdio transport)
│   ├── server.ts             # Tool registration (zod schemas)
│   ├── graph/
│   │   ├── types.ts          # Node/edge type definitions
│   │   ├── storage.ts        # StorageBackend interface (swappable)
│   │   └── sqlite.ts         # SQLite + FTS5 implementation
│   ├── indexer/
│   │   ├── indexer.ts        # File walking, hashing, incremental indexing
│   │   ├── parser.ts         # Tree-sitter AST extraction
│   │   └── languages.ts      # Language registry (ext → grammar)
│   ├── tools/                # 6 MCP tool handlers
│   └── cli.ts                # CLI entry point
├── skills/codemesh.md        # Agent education skill
├── hooks/                    # Pre/post read hooks
└── eval/                     # Eval framework (5 tasks, 3 models)
```

Storage is **backend-agnostic**. The `StorageBackend` interface abstracts all persistence. v1 uses SQLite with FTS5 for zero-dependency local operation. The interface supports swapping to Memgraph, Neo4j, or other graph databases.

---

## Eval Framework

Reproducible evaluation harness with LLM-as-judge scoring:

```bash
# Setup
npm install -g @pyalwin/codemesh
git clone --depth 1 https://github.com/Alamofire/Alamofire.git /tmp/alamofire
# ... clone other repos ...

# Index
CODEMESH_PROJECT_ROOT=/tmp/alamofire codemesh index

# Run benchmarks
python3 eval/head_to_head.py --model sonnet alamofire excalidraw vscode swift-compiler
```

See [`docs/benchmark-results.md`](docs/benchmark-results.md) for full methodology and results. Early pydantic evals are archived in [`docs/experiments/`](docs/experiments/).

The SQLite-only CLI has a separate [vNext contract and agent workflow eval](eval/VNEXT_EVALS.md) on a pinned Pydantic checkout. Its correctness checks and model cost/quality comparison are reported separately from the historical benchmarks.
The [vNext Pydantic results](eval/VNEXT_RESULTS.md) record the first full paired run.

---

## vs. Existing Tools

| Feature | [CodeGraph](https://github.com/colbymchenry/codegraph) | [Graphify](https://github.com/safishamsi/graphify) | [Axon](https://github.com/harshkedia177/axon) | **Codemesh** |
|:--|:--:|:--:|:--:|:--:|
| Structural indexing | Yes | Yes | Yes | Yes |
| FTS search | Yes | &mdash; | Yes | Yes |
| Agent write-back | &mdash; | &mdash; | &mdash; | **Yes** |
| Workflow memory | &mdash; | &mdash; | &mdash; | **Yes** |
| Hook interception | &mdash; | &mdash; | &mdash; | **Yes** |
| Backend-swappable | &mdash; | &mdash; | &mdash; | **Yes** |
| Eval framework | &mdash; | &mdash; | &mdash; | **Yes** |
| Published benchmarks | &mdash; | &mdash; | &mdash; | **Yes** |

---

## Development

```bash
bun install          # Install dependencies
bun run build        # Compile TypeScript
bun run test         # Run 102 tests
bun run dev          # Watch mode
bun run lint         # Type check
```

---

## Contributing

Contributions welcome. Areas for improvement:

- **More languages** &mdash; Add tree-sitter grammars and language-specific extractors
- **AST-diff invalidation** &mdash; Function-level instead of file-level staleness detection
- **Graph backends** &mdash; Memgraph/Neo4j adapters for `StorageBackend`
- **Semantic search** &mdash; Embedding columns alongside FTS5
- **Agent adoption** &mdash; Better patterns for agents to prefer graph tools naturally

---

## License

MIT
