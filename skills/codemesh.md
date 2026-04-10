---
name: codemesh
description: Query the code knowledge graph before reading code. Reduces discovery from ~10 reads to ~1 by providing cached structural and semantic understanding.
---

# Codemesh: Code Knowledge Graph

You have access to a persistent code knowledge graph via `codemesh_*` MCP tools. This graph contains structural data (files, symbols, imports, call chains) and semantic data (agent-written summaries, workflow paths) that accumulate across sessions.

## Protocol

### Before exploring code, query the graph first

When you need to understand a part of the codebase:

1. **Start with `codemesh_query`** — search by concept, symbol name, or keyword
   - Example: `codemesh_query({ query: "approval flow" })`
   - This returns relevant files with summaries, symbols, and known workflows

2. **Deep-dive with `codemesh_context`** — get full context for a specific file
   - Example: `codemesh_context({ path: "src/services/approval.ts" })`
   - Returns: symbols in the file, incoming/outgoing dependencies, cached summaries

3. **Before changes, check impact with `codemesh_impact`**
   - Example: `codemesh_impact({ path: "src/models/invoice.ts" })`
   - Returns: all files and symbols that depend on this target

### After reading code, write back what you learned

4. **Enrich the graph with `codemesh_enrich`** — save your understanding
   - Example: `codemesh_enrich({ path: "src/services/approval.ts", summary: "Handles invoice state transitions DRAFT->PENDING->APPROVED. Dispatches notifications on each transition. Depends on AuditLog for compliance tracking." })`

5. **Record workflows with `codemesh_workflow`** — save multi-file paths
   - Example: `codemesh_workflow({ name: "invoice submission", description: "Full flow from API to database", files: ["src/views/invoice.ts", "src/serializers/invoice.ts", "src/services/approval.ts", "src/services/notification.ts"] })`

## When to fall back to Grep/Read

- `codemesh_query` returns nothing relevant (new or unindexed area)
- Graph data is marked stale and you need fresh code
- Line-level debugging where summaries aren't sufficient

## When to skip codemesh

- Simple one-file edits where you already know the exact file
- User explicitly asks to read a specific file
- The file was just created and hasn't been indexed yet

## Key principle

Codemesh doesn't replace code reading — it replaces the **discovery phase**. Instead of 10 exploratory greps and reads to figure out what's relevant, one `codemesh_query` tells you where to look and what you'll find. Then you read the 1-2 files that actually matter.
