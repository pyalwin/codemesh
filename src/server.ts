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

function textResult(data: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }],
  };
}

export function createServer(storage: StorageBackend, projectRoot: string): McpServer {
  const server = new McpServer({
    name: "codemesh",
    version: "0.1.0",
  });

  // ── codemesh_query ──────────────────────────────────────────────
  server.tool(
    "codemesh_query",
    "Search the code knowledge graph using full-text search. Returns matching files, symbols, concepts, and workflows ranked by relevance.",
    {
      query: z.string().describe("Search query text"),
      scope: z
        .enum(["files", "symbols", "workflows", "all"])
        .optional()
        .describe("Limit results to a specific node type"),
    },
    async (args) => {
      const result = await handleQuery(storage, args, projectRoot);
      return textResult(result);
    },
  );

  // ── codemesh_context ────────────────────────────────────────────
  server.tool(
    "codemesh_context",
    "Get full context for a file or symbol, including its symbols with source code, edges, related concepts, and workflows.",
    {
      path: z.string().describe("Relative file path (e.g. src/index.ts)"),
      symbol: z
        .string()
        .optional()
        .describe("Symbol name within the file to focus on"),
    },
    async (args) => {
      const result = await handleContext(storage, args, projectRoot);
      return textResult(result);
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
      return textResult(result);
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
      return textResult(result);
    },
  );

  // ── codemesh_impact ─────────────────────────────────────────────
  server.tool(
    "codemesh_impact",
    "Find all nodes that depend on a given file or symbol (reverse dependency analysis). Shows what would be affected by changes.",
    {
      path: z.string().describe("Relative file path"),
      symbol: z
        .string()
        .optional()
        .describe("Symbol name within the file"),
    },
    async (args) => {
      const result = await handleImpact(storage, args);
      return textResult(result);
    },
  );

  // ── codemesh_status ─────────────────────────────────────────────
  server.tool(
    "codemesh_status",
    "Get statistics about the knowledge graph: node counts by type, edge counts by type, stale count, and last indexed timestamp.",
    async () => {
      const result = await handleStatus(storage);
      return textResult(result);
    },
  );

  // ── codemesh_trace ─────────────────────────────────────────────
  server.tool(
    "codemesh_trace",
    "Trace a call chain from a symbol. Returns source code of every function in the path. Use this to understand execution flows without reading files.",
    {
      symbol: z.string().describe("Symbol name to start tracing from (e.g., 'Session.request')"),
      depth: z.number().optional().describe("Max call chain depth (default: 3)"),
    },
    async ({ symbol, depth }) => {
      const result = await handleTrace(storage, { symbol, depth }, projectRoot);
      return textResult(result);
    },
  );

  return server;
}
