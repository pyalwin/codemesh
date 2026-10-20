/**
 * MCP Server — Thin adapter over the shared Codemesh handlers.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { StorageBackend } from "./graph/storage.js";
import { handleQuery } from "./tools/query.js";
import { handleContext } from "./tools/context.js";
import { handleEnrich } from "./tools/enrich.js";
import { handleWorkflow } from "./tools/workflow.js";
import { handleImpact } from "./tools/impact.js";
import { handleStatus } from "./tools/status.js";
import { handleTrace } from "./tools/trace.js";
import { handleAnswer } from "./tools/answer.js";
import { handleMap } from "./tools/map.js";
import { handleSource } from "./tools/source.js";
import { handleRecall } from "./tools/recall.js";
import { handleInspect } from "./tools/inspect.js";
import { handleHydrate } from "./tools/hydrate.js";
import { getLspClient } from "./tools/lsp-client.js";

function textResult(data: unknown, projectRoot: string) {
  const payload = typeof data === "object" && data !== null ? { projectRoot, ...data } : data;
  return {
    content: [{ type: "text" as const, text: JSON.stringify(payload, null, 2) }],
  };
}

/** The bounded handlers account for their own serialized JSON byte budget. */
function boundedTextResult(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] };
}

export function createServer(storage: StorageBackend, projectRoot: string): McpServer {
  const server = new McpServer({
    name: "codemesh",
    version: "0.1.0",
  });

  // ── codemesh_answer ─────────────────────────────────────────────
  server.tool(
    "codemesh_answer",
    "Optional one-call context assembly for a code question. Returns ranked files, inline source snippets, suggested reads, concepts, workflows, and call graph. For deterministic, model-directed navigation, start with codemesh_map instead.",
    { question: z.string().describe("Natural language question about the codebase") },
    async ({ question }) => {
      const result = await handleAnswer(storage, { question }, projectRoot);
      return textResult(result, projectRoot);
    }
  );

  // ── codemesh_explore ────────────────────────────────────────────
  server.tool(
    "codemesh_explore",
    "Omni-tool for exploring the codebase. Use 'search' to find things by text, 'context' to see relations of a specific file/symbol, and 'impact' to find reverse dependencies (trace).",
    {
      action: z.enum(["search", "context", "impact"]).describe("The exploration action to perform"),
      query: z.string().optional().describe("Search query text (required for 'search' action)"),
      path: z.string().optional().describe("Relative file path for context/impact; optional file or directory filter for search"),
      paths: z.array(z.string()).optional().describe("Multiple file paths to get context for in one call"),
      symbol: z.string().optional().describe("Symbol name for context/impact (fuzzy matched)"),
      scope: z.enum(["files", "symbols", "workflows", "all"]).optional().describe("Scope for 'search' action"),
      limit: z.number().int().min(1).max(20).optional().describe("Maximum search matches (default: 20)"),
      maxBytes: z.number().int().min(512).max(65536).optional().describe("Maximum serialized search bytes (default: 8192)"),
      exactTieBreak: z.enum(["pagerank", "path-depth"]).optional().describe("Exact-name ties: popularity (default), or shallower paths first"),
    },
    async (args) => {
      if (args.action === "search") {
        if (!args.query) throw new Error("query is required for search action");
        const result = await handleQuery(storage, { query: args.query, scope: args.scope, path: args.path,
          limit: args.limit, maxBytes: args.maxBytes, exactTieBreak: args.exactTieBreak }, projectRoot);
        return boundedTextResult(result);
      } else if (args.action === "context") {
        // Support multi-target queries via paths[]
        if (args.paths && args.paths.length > 0) {
          const lspClient = await getLspClient(args.paths[0], projectRoot);
          const result = await handleContext(storage, { paths: args.paths, symbol: args.symbol }, projectRoot, lspClient);
          return textResult(result, projectRoot);
        }
        if (!args.path) throw new Error("path or paths is required for context action");
        // Try to get an LSP client for this file type — returns null silently if unavailable
        const lspClient = await getLspClient(args.path, projectRoot);
        const result = await handleContext(storage, { path: args.path, symbol: args.symbol }, projectRoot, lspClient);
        return textResult(result, projectRoot);
      } else if (args.action === "impact") {
        if (!args.path) throw new Error("path is required for impact action");
        const result = await handleImpact(storage, { path: args.path, symbol: args.symbol });
        return textResult(result, projectRoot);
      }
      throw new Error("Invalid action");
    },
  );

  // ── codemesh_enrich ─────────────────────────────────────────────
  server.tool(
    "codemesh_enrich",
    "Add an AI-generated concept (summary/insight) linked to a file or symbol. Creates a ConceptNode with a describes edge.",
    {
      path: z.string().describe("Relative file path"),
      symbol: z.string().optional().describe("Symbol name within the file"),
      summary: z.string().describe("The concept summary or insight to store"),
      related_files: z
        .array(z.string())
        .optional()
        .describe("Other file paths whose concepts should be linked via related_to edges"),
      sessionId: z.string().describe("Identifier for the agent session creating this concept"),
      status: z.enum(["hypothesis", "verified"]).optional().describe("Confidence label for this agent note"),
    },
    async (args) => {
      const result = await handleEnrich(storage, args);
      return textResult(result, projectRoot);
    },
  );

  // ── codemesh_workflow ───────────────────────────────────────────
  server.tool(
    "codemesh_workflow",
    "Create a workflow that links files in a logical sequence (e.g. request flow, data pipeline). Creates a WorkflowNode with traverses edges.",
    {
      name: z.string().describe("Workflow name"),
      description: z.string().describe("What this workflow represents"),
      files: z
        .array(z.string())
        .describe("Ordered list of relative file paths in the workflow"),
    },
    async (args) => {
      const result = await handleWorkflow(storage, args);
      return textResult(result, projectRoot);
    },
  );

  server.tool(
    "codemesh_recall",
    "Retrieve prior agent notes and workflows by exact path/symbol or text. Each result reports whether its source revision is still current.",
    {
      id: z.string().optional().describe("Exact concept or workflow ID"),
      path: z.string().optional().describe("Exact project-relative file path"),
      symbol: z.string().optional().describe("Qualified symbol name within path"),
      query: z.string().optional().describe("Text to search in saved notes and workflows"),
      limit: z.number().int().min(1).max(100).optional(),
    },
    async (args) => textResult(await handleRecall(storage, args, projectRoot), projectRoot),
  );

  // ── codemesh_status ─────────────────────────────────────────────
  server.tool(
    "codemesh_status",
    "Get statistics about the knowledge graph: node counts by type, edge counts by type, stale count, and last indexed timestamp.",
    async () => {
      const result = await handleStatus(storage);
      return textResult(result, projectRoot);
    },
  );

  // ── codemesh_trace ──────────────────────────────────────────────
  server.tool(
    "codemesh_trace",
    "Trace a call chain from a symbol to its leaf nodes. Follows calls/callers edges through the graph and returns every function in the path. Use this AFTER codemesh_explore to follow a specific execution flow to completion.",
    {
      symbol: z.string().describe("Symbol name to start tracing from (e.g., 'Session.request' or 'request'). Supports fuzzy matching."),
      depth: z.number().int().min(0).max(5).optional().describe("Max call chain depth (default: 5, capped at 5)"),
    },
    async ({ symbol, depth }) => {
      const result = await handleTrace(storage, { symbol, depth: depth ?? 5 }, projectRoot);
      return textResult(result, projectRoot);
    },
  );

  // ── codemesh_map ────────────────────────────────────────────────
  server.tool(
    "codemesh_map",
    "Navigate the indexed codebase one level at a time. Call with no arguments for the repository root; pass an entry's node ID to expand it; use relation to follow one-hop graph links. Results are bounded and paginated, with stable IDs and no source code. Use codemesh_source for an exact symbol when needed. Legacy query/symbol call-tree search remains available.",
    {
      query: z.string().optional().describe("Optional legacy natural-language call-tree search. Omit to navigate deterministically."),
      symbol: z.string().optional().describe("Optional legacy fuzzy symbol lookup. Omit to navigate deterministically."),
      node: z.string().optional().describe("Exact node ID from a prior map result; omit for repository root."),
      relation: z.enum(["contains", "calls", "called_by", "imports", "imported_by", "extends"]).optional().describe("One-hop relation to list from node (default: contains)."),
      limit: z.number().int().min(1).max(100).optional().describe("Maximum entries in one navigation page (default: 30)."),
      cursor: z.string().optional().describe("Opaque nextCursor from a prior page of the same request."),
      maxBytes: z.number().int().min(512).max(65536).optional().describe("Navigation JSON byte limit (default: 8192). Increase if the next complete entry does not fit."),
    },
    async ({ query, symbol, node, relation, limit, cursor, maxBytes }) => {
      if (query === undefined && symbol === undefined) {
        const result = await handleMap(storage, { node, relation, limit, cursor, maxBytes }, projectRoot);
        return boundedTextResult(result);
      }
      if (node !== undefined || relation !== undefined || limit !== undefined || cursor !== undefined || maxBytes !== undefined) {
        throw new Error("query/symbol cannot be combined with node/relation/limit/cursor/maxBytes");
      }
      const result = await handleMap(storage, { query, symbol, node, relation, limit, cursor }, projectRoot);
      return textResult(result, projectRoot);
    },
  );

  // ── codemesh_source ────────────────────────────────────────────
  server.tool(
    "codemesh_source",
    "Read a revision-checked symbol, preferably by exact ID. Unique symbol names also resolve; ambiguous names return candidate IDs and spans. Use auto for bounded navigation, full for edits, and ifHash only when matching bytes remain in context. If stale, re-index before relying on line ranges.",
    {
      id: z.string().describe("Symbol ID from codemesh_map or other tool output (e.g., 'symbol:src/services/gl-coding.ts:validateInvoiceLineItems')"),
      mode: z.enum(["auto", "fold", "preview", "full"]).optional().describe("Defaults to exact full. Auto returns full up to 120 lines/12KiB, otherwise a bounded preview with omitted ranges."),
      ifHash: z.string().optional().describe("Hash from an earlier response in the same mode; returns unchanged when bytes match"),
      force: z.boolean().optional().describe("Return source even when ifHash matches, for rehydration after compaction"),
      sessionId: z.string().optional().describe("Optional host session ID for delivery tracking"),
      contextEpoch: z.string().optional().describe("Optional context generation; change it after compaction"),
    },
    async ({ id, mode, ifHash, force, sessionId, contextEpoch }) => {
      const result = await handleSource(storage, { id, mode, ifHash, force, sessionId, contextEpoch }, projectRoot);
      return textResult(result, projectRoot);
    },
  );

  server.tool(
    "codemesh_inspect",
    "Inspect a symbol and verified direct callees, callers, or both. Indexed links are candidate hints; incoming discovery can be incomplete. Returns whole exact source blocks within byte/symbol budgets, with omitted and unresolved relationships explicit.",
    {
      id: z.string().min(1).describe("Exact symbol ID from codemesh_map"),
      depth: z.union([z.literal(0), z.literal(1)]).optional().describe("0 for the symbol only; 1 also lists direct calls (default: 1)"),
      direction: z.enum(["outgoing", "incoming", "both"]).optional().describe("Which direct neighbors to inspect (default: outgoing)"),
      maxCandidates: z.number().int().min(1).max(128).optional().describe("Maximum indexed caller candidates to verify (default: 32)"),
      maxBytes: z.number().int().min(1024).max(131072).optional().describe("Maximum serialized JSON bytes (default: 24576)"),
      maxSymbols: z.number().int().min(1).max(8).optional().describe("Maximum exact source blocks (default: 4)"),
    },
    async (input) => boundedTextResult(await handleInspect(storage, input, projectRoot)),
  );

  server.tool(
    "codemesh_hydrate",
    "Reopen up to eight saved symbol references after a handoff or compaction. Verifies saved hashes against current source and returns exact full source within byte and symbol budgets. Every reference has an explicit outcome, including fresh, changed, stale, missing, ambiguous, omitted, or error.",
    {
      references: z.array(z.object({
        id: z.string().min(1),
        hash: z.string().optional().describe("Saved rendered-view hash, if available"),
        mode: z.enum(["auto", "fold", "preview", "full"]).optional().describe("Mode used for saved hash"),
        fileHash: z.string().optional().describe("Saved full file hash, if available"),
        bodyHash: z.string().optional().describe("Saved full symbol-body hash, if available"),
      })).min(1).max(8),
      maxBytes: z.number().int().min(1024).max(131072).optional().describe("Maximum serialized JSON bytes (default: 24576)"),
      maxSymbols: z.number().int().min(1).max(8).optional().describe("Maximum exact source blocks (default: 4)"),
    },
    async (input) => boundedTextResult(await handleHydrate(storage, input, projectRoot)),
  );

  return server;
}
