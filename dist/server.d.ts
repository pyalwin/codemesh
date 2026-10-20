/**
 * MCP Server — Thin adapter over the shared Codemesh handlers.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { StorageBackend } from "./graph/storage.js";
export declare function createServer(storage: StorageBackend, projectRoot: string): McpServer;
