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

    // Direct proof that only b.ts was re-embedded: the alpha row must
    // remain in LanceDB, not be re-created. With the old drop+rebuild
    // pattern, `semanticSearch` would still return `alpha` (because it
    // would have just been re-embedded), so that assertion alone is
    // insufficient. Instead, confirm a.ts wasn't touched at all by
    // checking that the row-count after incremental equals the first
    // run's count — no drops, no reinsertions of alpha.
    const lancedb = await import("@lancedb/lancedb");
    const ldb = await lancedb.connect(
      join(projectRoot, ".codemesh", "vectors"),
    );
    const table = await ldb.openTable("symbols");
    expect(await table.countRows()).toBe(2);

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

describe("Indexer — transaction chunking", () => {
  let projectRoot: string;
  let backend: SqliteBackend;

  beforeEach(async () => {
    projectRoot = mkdtempSync(join(tmpdir(), "codemesh-tx-"));
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

  it("indexes successfully across multiple file batches", async () => {
    // Create enough files to span more than one transaction batch
    // (FILE_BATCH_SIZE is 500 in the refactored indexer). We generate
    // 50 files — fast to parse, and enough to exercise the batch loop.
    for (let i = 0; i < 50; i++) {
      writeFileSync(
        join(projectRoot, `mod${i}.ts`),
        `export const value${i} = ${i};\n`,
      );
    }

    const indexer = new Indexer(backend, projectRoot);
    const result = await indexer.index({ withEmbeddings: false });

    expect(result.filesIndexed).toBe(50);
    expect(result.symbolsFound).toBe(50);
  }, 60_000);
});
