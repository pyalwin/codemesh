/**
 * MCP Server — Registers all 7 codemesh tools on a McpServer instance.
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
import { getLspClient } from "./tools/lsp-client.js";
import { QueryCache } from "./cache/query-cache.js";

function textResult(data: unknown, projectRoot: string) {
  const payload = typeof data === "object" && data !== null ? { projectRoot, ...data } : data;
  return {
    content: [{ type: "text" as const, text: JSON.stringify(payload, null, 2) }],
  };
}

export function createServer(storage: StorageBackend, projectRoot: string): McpServer {
  const server = new McpServer({
    name: "codemesh",
    version: "0.1.0",
  });

  const cache = new QueryCache<ReturnType<typeof textResult>>();

  // ── codemesh_answer ─────────────────────────────────────────────
  server.tool(
    "codemesh_answer",
    "One-call context assembly. Takes a question, searches the graph, follows call chains, and returns ALL relevant files, symbols, concepts, workflows, and suggested reads in a single response. Use this FIRST before any other exploration.",
    { question: z.string().describe("Natural language question about the codebase") },
    async ({ question }) => {
      const version = (await storage.getStats()).lastIndexedAt ?? "0";
      const cached = cache.get("answer", version, question);
      if (cached) return cached;
      const result = await handleAnswer(storage, { question }, projectRoot);
      const response = textResult(result, projectRoot);
      cache.set("answer", version, question, response);
      return response;
    }
  );

  // ── codemesh_explore ────────────────────────────────────────────
  server.tool(
    "codemesh_explore",
    "Omni-tool for exploring the codebase. Use 'search' to find things by text, 'context' to see relations of a specific file/symbol, and 'impact' to find reverse dependencies (trace).",
    {
      action: z.enum(["search", "context", "impact"]).describe("The exploration action to perform"),
      query: z.string().optional().describe("Search query text (required for 'search' action)"),
      path: z.string().optional().describe("Relative file path (required for 'context' and 'impact' actions)"),
      paths: z.array(z.string()).optional().describe("Multiple file paths to get context for in one call"),
      symbol: z.string().optional().describe("Symbol name for context/impact (fuzzy matched)"),
      scope: z.enum(["files", "symbols", "workflows", "all"]).optional().describe("Scope for 'search' action"),
    },
    async (args) => {
      if (args.action === "search") {
        if (!args.query) throw new Error("query is required for search action");
        const result = await handleQuery(storage, { query: args.query, scope: args.scope }, projectRoot);
        return textResult(result, projectRoot);
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
      depth: z.number().optional().describe("Max call chain depth (default: 5)"),
    },
    async ({ symbol, depth }) => {
      const result = await handleTrace(storage, { symbol, depth: depth ?? 5 }, projectRoot);
      return textResult(result, projectRoot);
    },
  );

  // ── codemesh_map ────────────────────────────────────────────────
  server.tool(
    "codemesh_map",
    "Map the call graph from a query or symbol. Returns a tree of symbols with summaries — no source code. Use codemesh_source to read the code of specific symbols you need.",
    {
      query: z.string().describe("Natural language query, e.g. 'how does invoice validation work'"),
      symbol: z.string().optional().describe("Start from a specific symbol instead of searching (e.g., 'validateInvoiceLineItems')"),
    },
    async ({ query, symbol }) => {
      const cacheKey = symbol ? `${query}::symbol:${symbol}` : query;
      const version = (await storage.getStats()).lastIndexedAt ?? "0";
      const cached = cache.get("map", version, cacheKey);
      if (cached) return cached;
      const result = await handleMap(storage, { query, symbol }, projectRoot);
      const response = textResult(result, projectRoot);
      cache.set("map", version, cacheKey, response);
      return response;
    },
  );

  // ── codemesh_source ────────────────────────────────────────────
  server.tool(
    "codemesh_source",
    "Get the source code of a specific symbol by its ID. Use after codemesh_map or codemesh_answer to read the code of symbols you need to inspect.",
    {
      id: z.string().describe("Symbol ID from codemesh_map or other tool output (e.g., 'symbol:src/services/gl-coding.ts:validateInvoiceLineItems')"),
    },
    async ({ id }) => {
      const result = await handleSource(storage, { id }, projectRoot);
      return textResult(result, projectRoot);
    },
  );

  return server;
}
