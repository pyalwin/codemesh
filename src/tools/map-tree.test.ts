import { describe, expect, it, vi } from "vitest";
import type { StorageBackend } from "../graph/storage.js";
import type { GraphEdge, SymbolNode } from "../graph/types.js";
import { buildMapTree } from "./map-tree.js";

describe("bounded call tree", () => {
  it("stops before unbounded hub expansion and reports truncation", async () => {
    const now = new Date().toISOString();
    const node = (n: number): SymbolNode => ({
      id: `symbol:hub.ts:s${n}`, type: "symbol", source: "static", name: `s${n}`,
      kind: "function", filePath: "hub.ts", lineStart: n, lineEnd: n,
      signature: `function s${n}()`, createdAt: now, updatedAt: now,
    });
    const edge = (from: number, to: number): GraphEdge => ({
      id: `edge:${from}:${to}`, type: "calls", source: "static",
      fromId: node(from).id, toId: node(to).id, createdAt: now,
    });
    const storage = {
      getNode: vi.fn(async (id: string) => {
        const n = Number(id.split("s").pop());
        return Number.isFinite(n) ? node(n) : null;
      }),
      getEdges: vi.fn(async (id: string, direction: string) => {
        if (direction === "in") return [];
        const n = Number(id.split("s").pop());
        return n < 100 ? [edge(n, n + 1)] : [];
      }),
    } as unknown as StorageBackend;
    const result = await buildMapTree(storage, [node(0).id]);
    expect(result.truncated).toBe(true);
    expect(result.totalSymbols).toBeLessThanOrEqual(25);
  });
});
