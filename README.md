<p align="center">
  <h1 align="center">Codemesh</h1>
  <p align="center">
    <strong>Intelligent code knowledge graph for AI coding agents</strong>
  </p>
  <p align="center">
    <a href="#benchmarks">Benchmarks</a> &middot;
    <a href="#quick-start">Quick Start</a> &middot;
    <a href="#how-it-works">How It Works</a> &middot;
    <a href="#mcp-tools">API Reference</a> &middot;
    <a href="docs/eval-results.md">Eval Results</a>
  </p>
</p>

---

Codemesh is an MCP server that gives AI coding agents a persistent, queryable knowledge graph of your codebase. Instead of grepping through hundreds of files every session, agents query the graph and go straight to what matters.

**The graph gets smarter over time:** agents write back what they learn, so the next session starts informed.

## Why

AI coding agents waste 40-80% of their tokens on discovery — grepping, reading irrelevant files, and rebuilding context they've already seen. On a 600-file codebase, a typical exploration task involves 10+ file reads before the agent even knows what's relevant.

Codemesh eliminates the discovery phase:

```
Before: Agent → Grep → 50 matches → Read 10 files → Understand → Work
After:  Agent → codemesh_query → 3 relevant files → Read 1 → Work
```

## Benchmarks

Evaluated on [pydantic](https://github.com/pydantic/pydantic) (656 files, 13,187 symbols) across 5 code comprehension tasks. Full methodology and raw responses in [`docs/eval-results.md`](docs/eval-results.md) and [`docs/eval-responses.md`](docs/eval-responses.md).

### Cost and Time

| Model | Baseline Cost | Codemesh Cost | **Saved** | Baseline Time | Codemesh Time | **Faster** |
|---|---:|---:|---:|---:|---:|---:|
| Opus 4.6 | $3.35 | $2.72 | **19%** | 14.5 min | 8.6 min | **41%** |
| Sonnet 4.6 | $3.37 | $1.87 | **44%** | 20.2 min | 8.6 min | **57%** |
| Haiku 4.5 | $2.67 | $1.79 | **33%** | 11.3 min | 9.4 min | **17%** |

### Quality (1-10, LLM-as-judge)

| Model | Baseline | Codemesh | **Delta** |
|---|---:|---:|---:|
| Opus 4.6 | 8.0 | 7.6 | -0.4 |
| Sonnet 4.6 | 2.8 | 6.4 | **+3.6** |
| Haiku 4.5 | 3.6 | 8.2 | **+4.6** |

> **Key finding:** Haiku + Codemesh (8.2/10, $1.79) outperforms Opus baseline (8.0/10, $3.35) at **47% lower cost**. The graph acts as an equalizer — smaller models produce Opus-quality results when they know where to look.

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

### 3. Configure as MCP server

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

### 4. Use it

The agent now has 6 new tools. Query the graph before reading code:

```
You: "Find how pydantic handles validation"

Agent: codemesh_query({ query: "validation" })
  → 12 relevant files with summaries, 4 known workflows

Agent: codemesh_context({ path: "pydantic/functional_validators.py" })
  → symbols, imports, callers, cached summaries

Agent: Read("pydantic/functional_validators.py")  // targeted read

Agent: codemesh_enrich({ path: "pydantic/functional_validators.py",
  summary: "Primary V2 validator API. @field_validator for per-field,
  @model_validator for whole-model. AfterValidator/BeforeValidator/
  WrapValidator for Annotated[] usage." })
  → saved for next session
```

## How It Works

Codemesh builds a persistent graph with two layers:

```
                    ┌─────────────────────────────────┐
                    │         Knowledge Graph          │
                    │                                  │
                    │  ┌───────────┐  ┌────────────┐  │
                    │  │ Structural│  │  Semantic   │  │
                    │  │  (auto)   │  │  (agents)   │  │
                    │  │           │  │             │  │
                    │  │ files     │  │ summaries   │  │
                    │  │ symbols   │  │ workflows   │  │
                    │  │ imports   │  │ concepts    │  │
                    │  │ calls     │  │ related_to  │  │
                    │  └───────────┘  └────────────┘  │
                    │                                  │
                    │         SQLite + FTS5            │
                    └─────────────────────────────────┘
                              ▲           │
                        index │           │ query
                              │           ▼
                    ┌─────────┴───────────────────────┐
                    │       MCP Server (6 tools)       │
                    │                                  │
                    │  query · context · enrich        │
                    │  workflow · impact · status       │
                    └──────────────────────────────────┘
                              ▲           │
                         tool │           │ result
                        calls │           │
                              │           ▼
                    ┌──────────────────────────────────┐
                    │          Claude Code              │
                    │     (or any MCP-compatible        │
                    │        AI coding agent)           │
                    └──────────────────────────────────┘
```

**Structural layer** (automatic) — tree-sitter parses your code into files, symbols (functions, classes, methods), and relationships (imports, calls, extends). Rebuilt on each index.

**Semantic layer** (agent-built) — as agents work with your code, they write back summaries and workflow paths. These survive re-indexing and accumulate across sessions. Invalidated when referenced files change.

## MCP Tools

| Tool | Description | Example |
|---|---|---|
| `codemesh_query` | Search the graph by concept or symbol name | `codemesh_query({ query: "validation pipeline" })` |
| `codemesh_context` | Full context for a file: symbols, edges, concepts, workflows | `codemesh_context({ path: "src/auth.py" })` |
| `codemesh_enrich` | Write back what you learned about a file | `codemesh_enrich({ path: "src/auth.py", summary: "..." })` |
| `codemesh_workflow` | Record a multi-file workflow path | `codemesh_workflow({ name: "login flow", files: [...] })` |
| `codemesh_impact` | What would be affected if you change this? | `codemesh_impact({ path: "src/models.py" })` |
| `codemesh_status` | Graph health: node/edge counts, stale nodes | `codemesh_status()` |

## CLI

```bash
codemesh index      # Index the project (incremental — only re-parses changed files)
codemesh status     # Show graph statistics
codemesh rebuild    # Purge and re-index from scratch
codemesh help       # Show usage
```

## Optional: Hooks and Skills

### Skill (teaches agents the workflow)

Copy `skills/codemesh.md` to `~/.claude/skills/` or your project's `.claude/skills/`. This teaches agents to query the graph before using Grep/Read.

### Hooks (automatic enrichment)

Add to `.claude/settings.json` for automatic graph-first behavior:

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

- **Pre-read hook** — injects cached summaries before file reads so the agent has context
- **Post-read hook** — nudges the agent to call `codemesh_enrich` after reading unfamiliar files

## Supported Languages

Tree-sitter grammars for 14 languages:

TypeScript, JavaScript, Python, Go, Rust, Java, C#, Ruby, PHP, C, C++, Swift, Kotlin, and Dart.

Any language with a tree-sitter grammar can be added.

## Architecture

```
codemesh/
├── src/
│   ├── index.ts              # MCP server entry point (stdio transport)
│   ├── server.ts             # Tool registration with zod schemas
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

**Storage is backend-agnostic.** The `StorageBackend` interface abstracts all persistence. v1 uses SQLite with FTS5 for zero-dependency local operation. The interface supports swapping to Memgraph, Neo4j, or other graph databases if query complexity demands it.

## Graph Data Model

### Nodes

| Type | Source | Fields |
|---|---|---|
| `file` | Static (tree-sitter) | path, hash, last_indexed_at |
| `symbol` | Static (tree-sitter) | name, kind, file_path, line_start, line_end, signature |
| `concept` | Agent-written | summary, last_updated_by, stale |
| `workflow` | Agent-written | description, file_sequence, last_walked_at |

### Edges

| Type | Direction | Source |
|---|---|---|
| `contains` | file → symbol | Static |
| `imports` | file → file | Static |
| `calls` | symbol → symbol | Static |
| `extends` | symbol → symbol | Static |
| `describes` | concept → file/symbol | Agent |
| `related_to` | concept → concept | Agent |
| `traverses` | workflow → file | Agent |

## Eval Framework

The `eval/` directory contains a reproducible evaluation harness:

```bash
# Run evals against pydantic
git clone --depth 1 https://github.com/pydantic/pydantic.git /tmp/pydantic
CODEMESH_PROJECT_ROOT=/tmp/pydantic node dist/cli.js index

# Run all 5 tasks for a specific model
python3 eval/run_eval.py --model opus
python3 eval/run_eval.py --model sonnet
python3 eval/run_eval.py --model haiku

# Quality scoring (LLM-as-judge)
python3 eval/judge.py
```

Tasks cover discovery, comprehension, and impact analysis. Results include cost, time, turn count, and quality scores. See [`docs/eval-results.md`](docs/eval-results.md) for full methodology.

## Development

```bash
# Setup
bun install

# Build
bun run build

# Test (102 tests)
bun run test

# Watch mode
bun run dev

# Type check
bun run lint
```

## Differentiators

Existing tools ([CodeGraph](https://github.com/colbymchenry/codegraph), [Graphify](https://github.com/safishamsi/graphify), [Axon](https://github.com/harshkedia177/axon)) provide read-only structural indexes. Codemesh adds:

| Feature | CodeGraph | Graphify | Axon | **Codemesh** |
|---|---|---|---|---|
| Structural indexing | Yes | Yes | Yes | Yes |
| FTS search | Yes | No | Yes | Yes |
| Agent write-back | No | No | No | **Yes** |
| Workflow memory | No | No | No | **Yes** |
| Hook interception | No | No | No | **Yes** |
| Backend-swappable | No | No | No | **Yes** |
| Eval framework | No | No | No | **Yes** |

## Contributing

Contributions welcome. The main areas for improvement:

- **More languages** — add tree-sitter grammars and language-specific extractors
- **AST-diff invalidation** — function-level instead of file-level staleness detection
- **Graph backends** — Memgraph/Neo4j adapters for the StorageBackend interface
- **Smarter search** — embedding columns for semantic similarity alongside FTS5
- **Agent adoption** — better patterns for getting agents to prefer graph tools without disabling Grep/Glob

## License

MIT
