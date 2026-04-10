# Codemesh

Intelligent code knowledge graph for AI coding agents. Reduces code discovery from ~10 reads to ~1 by providing persistent structural and semantic understanding across sessions.

## What It Does

When AI coding agents explore a codebase, they waste tokens scanning files with grep and reading code they've already seen. Codemesh builds a persistent graph of your code -- functions, classes, imports, call chains -- and lets agents query it instantly instead of re-discovering everything from scratch.

**Before Codemesh:** Agent greps 50 files, reads 10, finally understands the landscape.
**After Codemesh:** Agent queries the graph, gets relevant files with summaries, reads 1-2 that matter.

The graph gets smarter over time: agents write back what they learn, so the next session starts informed.

## Installation

```bash
bun install -g codemesh
```

## Quick Start

### 1. Index your project

```bash
cd /your/project
codemesh index
```

### 2. Configure as MCP server

Add to your Claude Code MCP config:

```json
{
  "codemesh": {
    "command": "node",
    "args": ["/path/to/codemesh/dist/index.js"],
    "env": {
      "CODEMESH_PROJECT_ROOT": "/path/to/your/project"
    }
  }
}
```

### 3. Install the skill (optional)

Copy `skills/codemesh.md` to `~/.claude/skills/` or your project's `.claude/skills/`.

### 4. Configure hooks (optional)

Add to your `.claude/settings.json`:

```json
{
  "hooks": {
    "pre_tool_use": [
      {
        "matcher": "Read",
        "command": "/path/to/codemesh/hooks/pre-read.sh"
      }
    ],
    "post_tool_use": [
      {
        "matcher": "Read",
        "command": "/path/to/codemesh/hooks/post-read.sh"
      }
    ]
  }
}
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `codemesh index` | Index the current project (incremental) |
| `codemesh status` | Show graph statistics |
| `codemesh rebuild` | Purge and re-index from scratch |
| `codemesh help` | Show usage |

## MCP Tools

| Tool | Description |
|------|-------------|
| `codemesh_query` | Search the knowledge graph by concept or symbol |
| `codemesh_context` | Get full context for a file or symbol |
| `codemesh_enrich` | Write back what you learned about code |
| `codemesh_workflow` | Record a multi-file workflow path |
| `codemesh_impact` | Check what's affected by a change |
| `codemesh_status` | Graph health check |

## How It Works

Codemesh builds a persistent graph with two layers:

1. **Structural layer** (automatic) -- tree-sitter parses your code into files, symbols (functions, classes), and relationships (imports, calls, extends)

2. **Semantic layer** (agent-built) -- as agents work with your code, they write back summaries and workflow paths that accumulate across sessions

Each session starts smarter than the last.

## Supported Languages

TypeScript, JavaScript, Python, Go, Rust, Java, C#, Ruby, PHP, C, C++, Swift, Kotlin -- any language with a tree-sitter grammar.

## Architecture

```
Claude Code --> MCP Connection --> Codemesh Server
                                    |-- Query Engine (FTS5)
                                    |-- Tree-sitter Parser
                                    +-- SQLite Storage (.codemesh/codemesh.db)
```

## Development

```bash
bun install
bun run test        # Run tests
bun run build       # Compile TypeScript
bun run dev         # Watch mode
```

## License

MIT
