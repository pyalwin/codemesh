import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { SqliteBackend } from "./sqlite.js";

describe("persistent SQLite retrieval cache", () => {
  let root: string;
  let path: string;
  let storage: SqliteBackend;
  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), "codemesh-cache-"));
    path = join(root, "index.db");
    storage = new SqliteBackend(path);
    await storage.initialize();
  });
  afterEach(async () => {
    await storage.close();
    rmSync(root, { recursive: true, force: true });
  });

  it("survives a new process and invalidates when a better candidate is indexed", async () => {
    const now = new Date().toISOString();
    await storage.upsertNode({ id: "file:alpha.ts", type: "file", source: "static", name: "alpha.ts",
      path: "alpha.ts", hash: "a", lastIndexedAt: now, createdAt: now, updatedAt: now });
    expect((await storage.search("alpha")).map((r) => r.node.id)).toContain("file:alpha.ts");
    await storage.close();
    storage = new SqliteBackend(path);
    await storage.initialize();
    expect((await storage.search("alpha")).map((r) => r.node.id)).toContain("file:alpha.ts");
    const db = new Database(path);
    expect((db.prepare("SELECT hit_count FROM retrieval_cache WHERE key = ?").get("all:alpha") as { hit_count: number }).hit_count).toBe(1);
    db.close();
    await storage.upsertNode({ id: "file:alpha-more.ts", type: "file", source: "static", name: "alpha-more.ts",
      path: "alpha-more.ts", hash: "b", lastIndexedAt: now, createdAt: now, updatedAt: now });
    expect((await storage.search("alpha")).map((r) => r.node.id)).toContain("file:alpha-more.ts");
  });
});
