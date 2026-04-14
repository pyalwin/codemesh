/**
 * MCP Server — Registers all 7 codemesh tools on a McpServer instance.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { StorageBackend } from "./graph/storage.js";
export declare function createServer(storage: StorageBackend, projectRoot: string): McpServer;
