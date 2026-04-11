<div align="center">

# Codemesh

**Intelligent code knowledge graph for AI coding agents**

Reduces cost by up to 44%, speeds up exploration by up to 57%,\
and boosts quality for smaller models to match Opus — all from a single `codemesh index`.

[![Tests](https://img.shields.io/badge/tests-102%20passed-brightgreen)]()
[![License](https://img.shields.io/badge/license-MIT-blue)]()
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-blue)]()
[![MCP](https://img.shields.io/badge/MCP-compatible-purple)]()

[Benchmarks](#benchmarks) &middot; [Quick Start](#quick-start) &middot; [How It Works](#how-it-works) &middot; [API Reference](#mcp-tools) &middot; [Eval Results](docs/eval-results.md)

</div>

---

## The Problem

AI coding agents waste **40-80% of their tokens on discovery** — grepping through files, reading irrelevant code, and rebuilding context they've already seen in previous sessions.

On a 600-file codebase, a typical exploration task involves 10+ file reads before the agent even knows what's relevant.

```
Before:  Agent → Grep → 50 matches → Read 10 files → Understand → Work
After:   Agent → codemesh_explore → 3 relevant files → codemesh_trace → full path → Work
```

Codemesh is an MCP server that gives agents a persistent, queryable knowledge graph. The graph gets smarter over time: agents write back what they learn, so the next session starts informed.

---

## Benchmarks

Evaluated on [pydantic](https://github.com/pydantic/pydantic) (656 files, 13,187 symbols) across 5 code comprehension tasks.

Full methodology, raw responses, and judge notes: [`docs/eval-results.md`](docs/eval-results.md) &middot; [`docs/eval-responses.md`](docs/eval-responses.md)

### Cost & Time

<table>
<thead>
<tr>
<th>Model</th>
<th align="right">Baseline Cost</th>
<th align="right">Codemesh Cost</th>
<th align="right">Saved</th>
<th align="right">Baseline Time</th>
<th align="right">Codemesh Time</th>
<th align="right">Faster</th>
</tr>
</thead>
<tbody>
<tr>
<td><strong>Opus 4.6</strong></td>
<td align="right">$3.35</td>
<td align="right">$2.72</td>
<td align="right"><strong>19%</strong></td>
<td align="right">14.5 min</td>
<td align="right">8.6 min</td>
<td align="right"><strong>41%</strong></td>
</tr>
<tr>
<td><strong>Sonnet 4.6</strong></td>
<td align="right">$3.37</td>
<td align="right">$1.87</td>
<td align="right"><strong>44%</strong></td>
<td align="right">20.2 min</td>
<td align="right">8.6 min</td>
<td align="right"><strong>57%</strong></td>
</tr>
<tr>
<td><strong>Haiku 4.5</strong></td>
<td align="right">$2.67</td>
<td align="right">$1.79</td>
<td align="right"><strong>33%</strong></td>
<td align="right">11.3 min</td>
<td align="right">9.4 min</td>
<td align="right"><strong>17%</strong></td>
</tr>
</tbody>
</table>

### Quality (1-10, LLM-as-judge)

<table>
<thead>
<tr>
<th>Model</th>
<th align="right">Baseline</th>
<th align="right">Codemesh</th>
<th align="right">Delta</th>
</tr>
</thead>
<tbody>
<tr>
<td><strong>Opus 4.6</strong></td>
<td align="right">8.0</td>
<td align="right">7.6</td>
<td align="right">-0.4</td>
</tr>
<tr>
<td><strong>Sonnet 4.6</strong></td>
<td align="right">2.8</td>
<td align="right">6.4</td>
<td align="right"><strong>+3.6</strong></td>
</tr>
<tr>
<td><strong>Haiku 4.5</strong></td>
<td align="right">3.6</td>
<td align="right">8.2</td>
<td align="right"><strong>+4.6</strong></td>
</tr>
</tbody>
</table>

> [!NOTE]
> **Haiku + Codemesh (8.2/10, $1.79)** outperforms **Opus baseline (8.0/10, $3.35)** at 47% lower cost.
> The graph acts as an equalizer — smaller models produce Opus-quality results when they know where to look.

---

## Quick Start

### 1. Install

```bash
bun install -g codemesh
```

### 2. Index your project

```bash
cd /your/project
codemesh index
```

```
Indexed 656 files
  Symbols found:  16733
  Edges created:  33266
  Duration:       10009ms
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
      "command": "node",
      "args": ["/path/to/codemesh/dist/index.js"],
      "env": {
        "CODEMESH_PROJECT_ROOT": "/path/to/your/project"
      }
    }
  }
}
```

The agent gets native MCP tools: `codemesh_explore`, `codemesh_trace`, `codemesh_enrich`, `codemesh_workflow`, `codemesh_status`.

**Best for:** Opus, structured workflows, enrichment/write-back

</details>

<details>
<summary><strong>Option B: CLI Mode</strong> (via Bash — zero MCP overhead)</summary>

No MCP config needed. The agent calls codemesh directly via Bash:

```bash
# Set the project root
export CODEMESH_PROJECT_ROOT=/path/to/your/project

# Agent uses Bash to call these:
codemesh explore search "request flow"
codemesh explore context Source/Core/Session.swift
codemesh explore trace Session.request --depth 5
codemesh explore impact Source/Core/Session.swift
```

All commands return JSON to stdout. No MCP server process, no protocol overhead.

**Best for:** Sonnet/Haiku, speed-sensitive workflows, simpler setup

</details>

### Which mode should I use?

| | MCP Server | CLI Mode |
|---|---|---|
| **Setup** | MCP config file | Just `export CODEMESH_PROJECT_ROOT` |
| **Overhead** | MCP protocol per call | Zero — direct subprocess |
| **Enrichment** | Native `codemesh_enrich` tool | Via `Bash("codemesh enrich ...")` |
| **Best model** | Opus (follows MCP well) | **Sonnet** (55% cheaper, 61% faster than baseline) |
| **Recommended** | Complex codebases | **Default choice** |

### 4. Use it

The agent now has 6 new tools. Query the graph before reading code:

```
You: "Find how pydantic handles validation"

Phase 1 — MAP:
Agent calls: codemesh_explore({ action: "search", query: "validation" })
       gets: 12 relevant files with summaries, 4 known workflows

Agent calls: codemesh_explore({ action: "context", path: "pydantic/functional_validators.py" })
       gets: 23 symbols, 4 imports, 2 cached summaries

Phase 2 — TRACE:
Agent calls: codemesh_trace({ symbol: "field_validator", depth: 5 })
       gets: complete call chain with source code at every step

Phase 3 — VERIFY:
Agent checks: Did I reach the leaf? Did I cover all files? → Yes → writes answer

Agent calls: codemesh_enrich({
               path: "pydantic/functional_validators.py",
               summary: "Primary V2 validator API. @field_validator for
                         per-field, @model_validator for whole-model..."
             })
       saves: summary for next session
```

---

## How It Works

```
                      ┌──────────────────────────────────┐
                      │         Knowledge Graph           │
                      │                                   │
                      │  ┌────────────┐ ┌─────────────┐  │
                      │  │ Structural │ │  Semantic    │  │
                      │  │   (auto)   │ │  (agents)   │  │
                      │  │            │ │             │  │
                      │  │  files     │ │  summaries  │  │
                      │  │  symbols   │ │  workflows  │  │
                      │  │  imports   │ │  concepts   │  │
                      │  │  calls     │ │  related_to │  │
                      │  └────────────┘ └─────────────┘  │
                      │                                   │
                      │          SQLite + FTS5            │
                      └────────────┬──────────────────────┘
                                   │
                      ┌────────────┴──────────────────────┐
                      │       MCP Server (6 tools)         │
                      │                                    │
                      │  query · context · enrich          │
                      │  workflow · impact · status         │
                      └────────────┬──────────────────────┘
                                   │
                      ┌────────────┴──────────────────────┐
                      │         Claude Code                │
                      │    (or any MCP-compatible agent)    │
                      └────────────────────────────────────┘
```

**Structural layer** (automatic) — Tree-sitter parses your code into files, symbols (functions, classes, methods), and relationships (imports, calls, extends). Rebuilt on each index.

**Semantic layer** (agent-built) — As agents work with your code, they write back summaries and workflow paths. These survive re-indexing and accumulate across sessions. Invalidated when referenced files change.

---

## MCP Tools

| Tool | Purpose | Example |
|:--|:--|:--|
| `codemesh_explore` | Omni-tool: search, context, or impact in one tool | `codemesh_explore({ action: "search", query: "validation" })` |
| `codemesh_trace` | Follow a call chain to leaf nodes with source code | `codemesh_trace({ symbol: "Session.request", depth: 5 })` |
| `codemesh_enrich` | Write back what you learned | `codemesh_enrich({ path: "src/auth.py", summary: "..." })` |
| `codemesh_workflow` | Record a multi-file workflow path | `codemesh_workflow({ name: "login flow", files: [...] })` |
| `codemesh_status` | Graph health check | `codemesh_status()` |

---

## CLI

```bash
codemesh index       # Index the project (incremental — only changed files)
codemesh status      # Node/edge counts, stale concepts, last indexed
codemesh rebuild     # Purge and re-index from scratch
codemesh help        # Usage
```

---

## Optional: Hooks & Skills

<details>
<summary><strong>Skill</strong> — teaches agents the graph-first workflow</summary>

Copy `skills/codemesh.md` to `~/.claude/skills/` or your project's `.claude/skills/`.

The skill instructs agents to query the graph before using Grep/Read, and to write back via `codemesh_enrich` after reading code.
</details>

<details>
<summary><strong>Hooks</strong> — automatic pre-read context injection</summary>

Add to `.claude/settings.json`:

```json
{
  "hooks": {
    "pre_tool_use": [{
      "matcher": "Read",
      "command": "/path/to/codemesh/hooks/pre-read.sh"
    }],
    "post_tool_use": [{
      "matcher": "Read",
      "command": "/path/to/codemesh/hooks/post-read.sh"
    }]
  }
}
```

- **Pre-read** — Injects cached summaries before file reads
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
git clone --depth 1 https://github.com/pydantic/pydantic.git /tmp/pydantic
CODEMESH_PROJECT_ROOT=/tmp/pydantic node dist/cli.js index

# Run evals (baseline + codemesh in parallel per task)
python3 eval/run_eval.py --model opus
python3 eval/run_eval.py --model sonnet
python3 eval/run_eval.py --model haiku

# Quality scoring
python3 eval/judge.py
```

See [`docs/eval-results.md`](docs/eval-results.md) for full methodology and [`docs/eval-responses.md`](docs/eval-responses.md) for all 30 raw responses.

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
