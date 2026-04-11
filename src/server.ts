/**
 * MCP Server — Registers all 6 codemesh tools on a McpServer instance.
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

  // ── codemesh_explore ────────────────────────────────────────────
  server.tool(
    "codemesh_explore",
    "Omni-tool for exploring the codebase. Use 'search' to find things by text, 'context' to see relations of a specific file/symbol, and 'impact' to find reverse dependencies (trace).",
    {
      action: z.enum(["search", "context", "impact"]).describe("The exploration action to perform"),
      query: z.string().optional().describe("Search query text (required for 'search' action)"),
      path: z.string().optional().describe("Relative file path (required for 'context' and 'impact' actions)"),
      symbol: z.string().optional().describe("Symbol name for context/impact (fuzzy matched)"),
      scope: z.enum(["files", "symbols", "workflows", "all"]).optional().describe("Scope for 'search' action"),
    },
    async (args) => {
      if (args.action === "search") {
        if (!args.query) throw new Error("query is required for search action");
        const result = await handleQuery(storage, { query: args.query, scope: args.scope });
        return textResult(result, projectRoot);
      } else if (args.action === "context") {
        if (!args.path) throw new Error("path is required for context action");
        const result = await handleContext(storage, { path: args.path, symbol: args.symbol });
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

  return server;
}
