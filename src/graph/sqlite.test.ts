import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mkdtempSync, rmSync } from "node:fs";
import { SqliteBackend } from "./sqlite.js";
import type { SymbolNode } from "./types.js";

describe("SqliteBackend.queryNodesByFilePaths", () => {
  let tmpDir: string;
  let backend: SqliteBackend;

  beforeEach(async () => {
    tmpDir = mkdtempSync(join(tmpdir(), "codemesh-sqlite-"));
    backend = new SqliteBackend(join(tmpDir, "test.db"));
    await backend.initialize();
  });

  afterEach(async () => {
    await backend.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("returns only symbol nodes whose filePath is in the given list", async () => {
    const now = new Date().toISOString();
    const makeSym = (file: string, name: string): SymbolNode => ({
      id: `symbol:${file}:${name}`,
      type: "symbol",
      source: "static",
      name,
      kind: "function",
      filePath: file,
      lineStart: 1,
      lineEnd: 5,
      signature: `${name}()`,
      createdAt: now,
      updatedAt: now,
    });

    await backend.upsertNode(makeSym("a.ts", "fa"));
    await backend.upsertNode(makeSym("b.ts", "fb"));
    await backend.upsertNode(makeSym("c.ts", "fc"));

    const result = await backend.queryNodesByFilePaths(["a.ts", "c.ts"]);
    const names = result.map((n) => n.name).sort();
    expect(names).toEqual(["fa", "fc"]);
  });

  it("returns empty array when no paths match", async () => {
    const result = await backend.queryNodesByFilePaths(["nonexistent.ts"]);
    expect(result).toEqual([]);
  });

  it("returns empty array when paths list is empty", async () => {
    const result = await backend.queryNodesByFilePaths([]);
    expect(result).toEqual([]);
  });
});
