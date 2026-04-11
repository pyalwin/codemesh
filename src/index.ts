#!/usr/bin/env node

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { SqliteBackend } from "./graph/sqlite.js";
import { createServer } from "./server.js";
import { join } from "path";
import { mkdirSync } from "fs";
import { shutdownAllLspClients } from "./tools/lsp-client.js";

const projectRoot = process.env.CODEMESH_PROJECT_ROOT ?? process.cwd();
const dbDir = join(projectRoot, ".codemesh");
const dbPath = join(dbDir, "codemesh.db");

mkdirSync(dbDir, { recursive: true });

const storage = new SqliteBackend(dbPath);
await storage.initialize();

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
