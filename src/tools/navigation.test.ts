import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SqliteBackend } from "../graph/sqlite.js";
import type { FileNode, GraphEdge, SymbolNode } from "../graph/types.js";
import { handleNavigation } from "./navigation.js";

const now = "2026-01-01T00:00:00.000Z";

describe("handleNavigation", () => {
  let root: string;
  let storage: SqliteBackend;

  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), "codemesh-navigation-"));
    storage = new SqliteBackend(join(root, "graph.db"));
    await storage.initialize();

    const files = ["index.ts", "src/a.ts", "src/b.ts", "src/deep/c.ts", "src-other/d.ts"];
    for (const path of files) {
      const file: FileNode = {
        id: `file:${path}`, type: "file", source: "static",
        name: path.split("/").at(-1)!, path, hash: "hash", lastIndexedAt: now,
        createdAt: now, updatedAt: now,
      };
      await storage.upsertNode(file);
    }

    const symbols: Array<[string, string, string]> = [
      ["src/a.ts", "A", "class A"],
      ["src/a.ts", "A.run", "run()"],
      ["src/a.ts", "A.run.inner", "inner()"],
      ["src/a.ts", "top", "top()"],
      ["src/b.ts", "B", "class B"],
    ];
    for (const [filePath, name, signature] of symbols) {
      const node: SymbolNode = {
        id: `symbol:${filePath}:${name}`, type: "symbol", source: "static",
        name, kind: name === "A" || name === "B" ? "class" : "method",
        filePath, lineStart: 1, lineEnd: 10, signature,
        summary: "SECRET_SOURCE_BODY_SHOULD_NEVER_APPEAR",
        createdAt: now, updatedAt: now,
      };
      await storage.upsertNode(node);
    }

    const links: Array<[string, string, GraphEdge["type"]]> = [
      ["symbol:src/a.ts:A.run", "symbol:src/a.ts:top", "calls"],
      ["symbol:src/a.ts:top", "symbol:src/a.ts:A.run", "calls"],
      ["file:src/a.ts", "file:src/b.ts", "imports"],
      ["symbol:src/a.ts:A", "symbol:src/b.ts:B", "extends"],
    ];
    for (const [fromId, toId, type] of links) {
      const edge: GraphEdge = {
        id: `edge:${type}:${fromId}:${toId}`, type, source: "static",
        fromId, toId, createdAt: now,
      };
      await storage.upsertEdge(edge);
    }
  });

  afterEach(async () => {
    await storage.close();
    rmSync(root, { recursive: true, force: true });
  });

  it("expands directories, files, classes and nested symbols one level at a time", async () => {
    const repo = await handleNavigation(storage, {});
    expect(repo.node).toEqual({ id: "repo", name: "repo", type: "repo" });
    expect(repo.entries.map((entry) => entry.id)).toEqual(["file:index.ts", "dir:src", "dir:src-other"]);
    expect(repo.entries[0].hasChildren).toBe(false);

    const src = await handleNavigation(storage, { node: "dir:src" });
    expect(src.entries.map((entry) => entry.id)).toEqual(["file:src/a.ts", "file:src/b.ts", "dir:src/deep"]);
    expect(src.entries[0].hasChildren).toBe(true);
    const deep = await handleNavigation(storage, { node: "dir:src/deep" });
    expect(deep.entries.map((entry) => entry.id)).toEqual(["file:src/deep/c.ts"]);

    const file = await handleNavigation(storage, { node: "file:src/a.ts" });
    expect(file.entries.map((entry) => entry.id)).toEqual(["symbol:src/a.ts:A", "symbol:src/a.ts:top"]);
    expect(file.entries[0].hasChildren).toBe(true);

    const klass = await handleNavigation(storage, { node: "symbol:src/a.ts:A" });
    expect(klass.entries.map((entry) => entry.id)).toEqual(["symbol:src/a.ts:A.run"]);
    expect(klass.entries[0].hasChildren).toBe(true);
    const method = await handleNavigation(storage, { node: "symbol:src/a.ts:A.run" });
    expect(method.entries.map((entry) => entry.id)).toEqual(["symbol:src/a.ts:A.run.inner"]);
  });

  it("sorts stably and returns a bounded page with a validated cursor", async () => {
    const first = await handleNavigation(storage, { node: "dir:src", limit: 2 });
    expect(first.entries.map((entry) => entry.name)).toEqual(["a.ts", "b.ts"]);
    expect(first.nextCursor).toBe("2");
    const second = await handleNavigation(storage, { node: "dir:src", limit: 2, cursor: first.nextCursor });
    expect(second.entries.map((entry) => entry.name)).toEqual(["deep"]);
    expect(second.nextCursor).toBeUndefined();
    expect(await handleNavigation(storage, { node: "dir:src", limit: 2 })).toEqual(first);
    await expect(handleNavigation(storage, { limit: 101 })).rejects.toThrow("limit");
    await expect(handleNavigation(storage, { cursor: "-1" })).rejects.toThrow("cursor");
  });

  it("queries only paths under the expanded directory", async () => {
    expect(await storage.queryFilePaths("src")).toEqual(["src/a.ts", "src/b.ts", "src/deep/c.ts"]);
    const paths = vi.spyOn(storage, "queryFilePaths");
    const allNodes = vi.spyOn(storage, "queryNodes");
    const result = await handleNavigation(storage, { node: "dir:src" });
    expect(paths).toHaveBeenCalledExactlyOnceWith("src");
    expect(allNodes).not.toHaveBeenCalled();
    expect(result.entries.map((entry) => entry.id)).not.toContain("dir:src-other");
  });

  it("bounds full serialized pages and leaves every omitted entry reachable", async () => {
    const expected = await handleNavigation(storage, { node: "file:src/a.ts" });
    const long = await storage.getNode("symbol:src/a.ts:A") as SymbolNode;
    await storage.upsertNode({ ...long, signature: "x".repeat(1800) });
    const blocked = await handleNavigation(storage, { node: "file:src/a.ts", maxBytes: 512 });
    expect(Buffer.byteLength(JSON.stringify(blocked))).toBeLessThanOrEqual(512);
    expect(blocked.entries).toEqual([]);
    expect(blocked.nextCursor).toBe("0");
    expect(blocked.truncated).toBe(true);
    expect(blocked.omitted?.entries).toBe(2);
    expect(blocked.hint).toContain("increase");
    const recovered = await handleNavigation(storage, { node: "file:src/a.ts", cursor: blocked.nextCursor, maxBytes: 4096 });
    expect(recovered.entries.map(entry => entry.id)).toEqual(expected.entries.map(entry => entry.id));
    expect(recovered.entries[0].signature).toBe("x".repeat(1800));

    const seen: string[] = [];
    let cursor: string | undefined;
    do {
      const page = await handleNavigation(storage, { node: "dir:src", maxBytes: 512, cursor });
      expect(Buffer.byteLength(JSON.stringify(page))).toBeLessThanOrEqual(512);
      seen.push(...page.entries.map(entry => entry.id));
      cursor = page.nextCursor;
    } while (cursor);
    expect(seen).toEqual(["file:src/a.ts", "file:src/b.ts", "dir:src/deep"]);
    await expect(handleNavigation(storage, { maxBytes: 511 })).rejects.toThrow("maxBytes");
  });

  it("follows only selected one-hop graph edges, including reverse links and cycles", async () => {
    const run = "symbol:src/a.ts:A.run";
    const top = "symbol:src/a.ts:top";
    expect((await handleNavigation(storage, { node: run, relation: "calls" })).entries.map((entry) => entry.id)).toEqual([top]);
    expect((await handleNavigation(storage, { node: run, relation: "called_by" })).entries.map((entry) => entry.id)).toEqual([top]);
    expect((await handleNavigation(storage, { node: "file:src/a.ts", relation: "imports" })).entries.map((entry) => entry.id)).toEqual(["file:src/b.ts"]);
    expect((await handleNavigation(storage, { node: "file:src/b.ts", relation: "imported_by" })).entries.map((entry) => entry.id)).toEqual(["file:src/a.ts"]);
    expect((await handleNavigation(storage, { node: "symbol:src/a.ts:A", relation: "extends" })).entries.map((entry) => entry.id)).toEqual(["symbol:src/b.ts:B"]);
    expect((await handleNavigation(storage, { node: "file:index.ts", relation: "calls" })).entries).toEqual([]);
  });

  it("rejects unknown IDs and never includes source text or free-text summaries", async () => {
    await expect(handleNavigation(storage, { node: "dir:missing" })).rejects.toThrow("not found");
    await expect(handleNavigation(storage, { node: "file:missing.ts" })).rejects.toThrow("not found");
    await expect(handleNavigation(storage, { node: "dir:src/../deep" })).rejects.toThrow("Invalid directory ID");
    await expect(handleNavigation(storage, { node: "repo", relation: "calls" })).rejects.toThrow("only available");
    const serialized = JSON.stringify(await handleNavigation(storage, { node: "file:src/a.ts" }));
    expect(serialized).not.toContain("SECRET_SOURCE_BODY_SHOULD_NEVER_APPEAR");
    expect(serialized).not.toContain("lineStart");
  });
});
