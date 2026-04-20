#!/usr/bin/env node

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { SqliteBackend } from "./graph/sqlite.js";
import { createServer } from "./server.js";
import { join } from "path";
import { mkdirSync } from "fs";
import { shutdownAllLspClients } from "./tools/lsp-client.js";

const projectRoot = process.env.CODEMESH_PROJECT_ROOT || process.cwd();
const dbDir = join(projectRoot, ".codemesh");
const dbPath = join(dbDir, "codemesh.db");

let storage: SqliteBackend;
try {
  mkdirSync(dbDir, { recursive: true });
  storage = new SqliteBackend(dbPath);
  await storage.initialize();
} catch (e) {
  // If we can't initialize storage (e.g. read-only environment during Smithery scan),
  // we still want the server to boot so it can list tools.
  console.error("Warning: Could not initialize storage. Tools may fail if called.", e);
  storage = new SqliteBackend(dbPath); // It will fail later when actually used
}

const server = createServer(storage, projectRoot);
const transport = new StdioServerTransport();
await server.connect(transport);

// Cleanup LSP clients on exit
const cleanup = async () => {
  await shutdownAllLspClients();
  process.exit(0);
};
process.on("SIGINT", cleanup);
process.on("SIGTERM", cleanup);
