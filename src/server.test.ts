import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { SqliteBackend } from "./graph/sqlite.js";
import type { SymbolNode } from "./graph/types.js";
import { Indexer } from "./indexer/indexer.js";
import { createServer } from "./server.js";
import { handleSource } from "./tools/source.js";
import { handleInspect } from "./tools/inspect.js";
import { handleHydrate } from "./tools/hydrate.js";
import { handleQuery } from "./tools/query.js";
import { handleNavigation } from "./tools/navigation.js";

describe("MCP adapter parity", () => {
  let root: string;
  let storage: SqliteBackend;
  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), "codemesh-mcp-"));
    mkdirSync(join(root, "src"));
    writeFileSync(join(root, "src/example.ts"), "export function check(x: number) {\n  return x > 0;\n}\n");
    storage = new SqliteBackend(join(root, "index.db"));
    await storage.initialize();
    await new Indexer(storage, root).index({ withEmbeddings: false });
  });
  afterEach(async () => {
    await storage.close();
    rmSync(root, { recursive: true, force: true });
  });

  it("returns the same source view as the shared CLI handler", async () => {
    const id = "symbol:src/example.ts:check";
    const expected = await handleSource(storage, { id, mode: "fold" }, root);
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const server = createServer(storage, root);
    const client = new Client({ name: "test", version: "1" });
    await server.connect(serverTransport);
    await client.connect(clientTransport);
    try {
      const response = await client.callTool({ name: "codemesh_source", arguments: { id, mode: "fold" } });
      const content = response.content as Array<{ type: string; text: string }>;
      expect(JSON.parse(content[0].text)).toEqual({ projectRoot: root, ...expected });
    } finally {
      await client.close();
      await server.close();
    }
  });

  it("exposes bounded inspect and hydrate results without adding wrapper bytes", async () => {
    const id = "symbol:src/example.ts:check";
    const source = await handleSource(storage, { id }, root);
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const server = createServer(storage, root);
    const client = new Client({ name: "test", version: "1" });
    await server.connect(serverTransport);
    await client.connect(clientTransport);
    try {
      const listed = await client.listTools();
      expect(listed.tools.map((tool) => tool.name)).toContain("codemesh_inspect");
      expect(listed.tools.map((tool) => tool.name)).toContain("codemesh_hydrate");

      const inspectArgs = { id, depth: 1, direction: "both", maxCandidates: 2, maxBytes: 4096, maxSymbols: 1 } as const;
      const inspectExpected = await handleInspect(storage, inspectArgs, root);
      const inspected = await client.callTool({ name: "codemesh_inspect", arguments: inspectArgs });
      const inspectText = (inspected.content as Array<{ type: string; text: string }>)[0].text;
      expect(JSON.parse(inspectText)).toEqual(inspectExpected);
      expect(Buffer.byteLength(inspectText)).toBeLessThanOrEqual(inspectArgs.maxBytes);

      const hydrateArgs = { references: [{ id, bodyHash: source.bodyHash! }], maxBytes: 4096, maxSymbols: 1 };
      const hydrateExpected = await handleHydrate(storage, hydrateArgs, root);
      const hydrated = await client.callTool({ name: "codemesh_hydrate", arguments: hydrateArgs });
      const hydrateText = (hydrated.content as Array<{ type: string; text: string }>)[0].text;
      expect(JSON.parse(hydrateText)).toEqual(hydrateExpected);
      expect(Buffer.byteLength(hydrateText)).toBeLessThanOrEqual(hydrateArgs.maxBytes);
    } finally {
      await client.close();
      await server.close();
    }
  });

  it("exposes scoped search and byte-bounded navigation without growing their envelopes", async () => {
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const server = createServer(storage, root);
    const client = new Client({ name: "test", version: "1" });
    await server.connect(serverTransport);
    await client.connect(clientTransport);
    try {
      const searchArgs = { query: "check", path: "src", scope: "symbols", limit: 1, maxBytes: 512 } as const;
      const expected = await handleQuery(storage, searchArgs, root);
      const searched = await client.callTool({ name: "codemesh_explore", arguments: { action: "search", ...searchArgs } });
      const searchText = (searched.content as Array<{ type: string; text: string }>)[0].text;
      expect(JSON.parse(searchText)).toEqual(expected);
      expect(Buffer.byteLength(searchText)).toBeLessThanOrEqual(512);
      const mapArgs = { node: "dir:src", maxBytes: 512 };
      const mapped = await client.callTool({ name: "codemesh_map", arguments: mapArgs });
      const mapText = (mapped.content as Array<{ type: string; text: string }>)[0].text;
      expect(JSON.parse(mapText)).toEqual(await handleNavigation(storage, mapArgs));
      expect(Buffer.byteLength(mapText)).toBeLessThanOrEqual(512);
    } finally {
      await client.close();
      await server.close();
    }
  });

  it("forwards the exact-name tie-break while respecting an explicit deeper path", async () => {
    const shallowPath = "src/entry.ts";
    const deepPath = "src/compat/entry.ts";
    mkdirSync(join(root, "src/compat"));
    for (const filePath of [shallowPath, deepPath]) {
      writeFileSync(join(root, filePath), "export function validate() { return true; }\n");
    }
    await new Indexer(storage, root).index({ withEmbeddings: false });
    const shallowId = `symbol:${shallowPath}:validate`;
    const deepId = `symbol:${deepPath}:validate`;
    for (const [id, pagerankScore] of [[shallowId, 0.01], [deepId, 0.9]] as const) {
      const node = await storage.getNode(id);
      expect(node?.type).toBe("symbol");
      await storage.upsertNode({ ...node as SymbolNode, pagerankScore });
    }

    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const server = createServer(storage, root);
    const client = new Client({ name: "test", version: "1" });
    await server.connect(serverTransport);
    await client.connect(clientTransport);
    try {
      const search = async (options: Record<string, string> = {}) => {
        const response = await client.callTool({
          name: "codemesh_explore",
          arguments: { action: "search", query: "validate", scope: "symbols", ...options },
        });
        expect(response.isError).not.toBe(true);
        return JSON.parse((response.content as Array<{ type: string; text: string }>)[0].text);
      };
      const defaultResult = await search();
      expect(defaultResult.results.map((result: { id: string }) => result.id)).toEqual([deepId, shallowId]);
      expect(await search({ exactTieBreak: "pagerank" })).toEqual(defaultResult);
      const pathDepth = await search({ exactTieBreak: "path-depth" });
      expect(pathDepth.results.map((result: { id: string }) => result.id)).toEqual([shallowId, deepId]);
      expect(pathDepth.results.every((result: { matchedField: string }) => result.matchedField === "exact")).toBe(true);
      const scoped = await search({ exactTieBreak: "path-depth", path: "src/compat" });
      expect(scoped.results.map((result: { id: string }) => result.id)).toEqual([deepId]);
    } finally {
      await client.close();
      await server.close();
    }
  });
});
