import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SqliteBackend } from "../graph/sqlite.js";
import { Indexer } from "./indexer.js";
import { resetLanceDb, semanticSearch } from "./embeddings.js";

describe("Indexer — incremental embeddings", () => {
  let projectRoot: string;
  let backend: SqliteBackend;

  beforeEach(async () => {
    projectRoot = mkdtempSync(join(tmpdir(), "codemesh-idx-"));
    mkdirSync(join(projectRoot, ".codemesh"), { recursive: true });
    backend = new SqliteBackend(join(projectRoot, ".codemesh", "codemesh.db"));
    await backend.initialize();
    resetLanceDb();
  });

  afterEach(async () => {
    await backend.close();
    rmSync(projectRoot, { recursive: true, force: true });
    resetLanceDb();
  });

  it("does not re-embed symbols in unchanged files on incremental index", async () => {
    writeFileSync(
      join(projectRoot, "a.ts"),
      "export function alpha() { return 1; }\n",
    );
    writeFileSync(
      join(projectRoot, "b.ts"),
      "export function beta() { return 2; }\n",
    );

    const indexer = new Indexer(backend, projectRoot);
    const firstRun = await indexer.index({ withEmbeddings: true });
    expect(firstRun.embeddings?.count).toBe(2);

    // Touch only b.ts
    writeFileSync(
      join(projectRoot, "b.ts"),
      "export function beta() { return 99; }\n",
    );

    const secondRun = await indexer.index({ withEmbeddings: true });
    expect(secondRun.embeddings?.count).toBe(1);

    // alpha survives
    const results = await semanticSearch(projectRoot, "alpha", 5);
    expect(results.map((r) => r.id)).toContain("symbol:a.ts:alpha");
  }, 180_000);

  it("removes embeddings when a source file is deleted", async () => {
    writeFileSync(
      join(projectRoot, "a.ts"),
      "export function alpha() { return 1; }\n",
    );
    writeFileSync(
      join(projectRoot, "b.ts"),
      "export function beta() { return 2; }\n",
    );

    const indexer = new Indexer(backend, projectRoot);
    await indexer.index({ withEmbeddings: true });

    rmSync(join(projectRoot, "b.ts"));
    await indexer.index({ withEmbeddings: true });

    const results = await semanticSearch(projectRoot, "beta", 5);
    expect(results.map((r) => r.id)).not.toContain("symbol:b.ts:beta");
  }, 180_000);
});
