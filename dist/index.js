#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { SqliteBackend } from "./graph/sqlite.js";
import { createServer } from "./server.js";
import { join } from "path";
import { mkdirSync } from "fs";
import { shutdownAllLspClients } from "./tools/lsp-client.js";
export const configSchema = z.object({
    codemeshProjectRoot: z
        .string()
        .describe("The absolute path to the local codebase you want to index"),
});
function readArgValue(name) {
    const assignment = process.argv.find((arg) => arg.startsWith(`${name}=`));
    if (assignment)
        return assignment.slice(name.length + 1);
    const flagNames = [
        `--${name}`,
        `--${name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`,
    ];
    for (const flag of flagNames) {
        const index = process.argv.indexOf(flag);
        if (index !== -1 && process.argv[index + 1])
            return process.argv[index + 1];
    }
    return undefined;
}
function getProjectRoot() {
    return (readArgValue("codemeshProjectRoot") ||
        process.env.CODEMESH_PROJECT_ROOT ||
        process.cwd());
}
const projectRoot = getProjectRoot();
async function createInitializedStorage(root) {
    const dbDir = join(root, ".codemesh");
    const dbPath = join(dbDir, "codemesh.db");
    mkdirSync(dbDir, { recursive: true });
    const storage = new SqliteBackend(dbPath);
    await storage.initialize();
    return storage;
}
function createScanStorage() {
    return {
        async initialize() { },
        async close() { },
        async upsertNode(node) { return node.id; },
        async getNode() { return null; },
        async queryNodes() { return []; },
        async deleteNode() { },
        async upsertEdge(edge) { return edge.id; },
        async getEdges() { return []; },
        async deleteEdgesByNode() { return 0; },
        async traverse() { return []; },
        async search() { return []; },
        async beginTransaction() { },
        async commitTransaction() { },
        async rollbackTransaction() { },
        async getStaleFiles() { return { changed: [], deleted: [], added: [] }; },
        async markConceptsStale() { return 0; },
        async purgeFileNodes() { return 0; },
        async getStats() {
            return {
                nodeCount: {},
                edgeCount: {},
                staleCount: 0,
                lastIndexedAt: null,
            };
        },
    };
}
export function createSandboxServer() {
    return createServer(createScanStorage(), projectRoot).server;
}
export default async function createSmitheryServer({ config, } = {}) {
    const root = config?.codemeshProjectRoot || getProjectRoot();
    const storage = await createInitializedStorage(root);
    return createServer(storage, root).server;
}
async function main() {
    let storage;
    try {
        storage = await createInitializedStorage(projectRoot);
    }
    catch (e) {
        // If we can't initialize storage (e.g. read-only environment during Smithery scan),
        // we still want the server to boot so it can list tools.
        console.error("Warning: Could not initialize storage. Tools may return empty results.", e);
        storage = createScanStorage();
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
}
if (process.argv[1] &&
    /(?:^|[/\\])(?:dist[/\\]index\.js|index\.cjs)$/.test(process.argv[1])) {
    main().catch((error) => {
        console.error(error);
        process.exit(1);
    });
}
//# sourceMappingURL=index.js.map