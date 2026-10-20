import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SqliteBackend } from "../graph/sqlite.js";
import { Indexer } from "../indexer/indexer.js";
import { handleSource } from "./source.js";
import { handleHydrate, type HydrateInput } from "./hydrate.js";

const hash = (value: string | Buffer): string => createHash("sha256").update(value).digest("hex");

describe("live source hydration", () => {
  let root: string;
  let storage: SqliteBackend;
  const path = "src/example.ts";
  const id = `symbol:${path}:check`;

  async function index(): Promise<void> {
    await new Indexer(storage, root).index({ withEmbeddings: false });
  }

  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), "codemesh-hydrate-"));
    mkdirSync(join(root, "src"));
    writeFileSync(join(root, path), "export function check(x: number) {\n  return x > 0;\n}\n");
    storage = new SqliteBackend(join(root, "index.db"));
    await storage.initialize();
    await index();
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await storage.close();
    rmSync(root, { recursive: true, force: true });
  });

  it("hydrates saved auto references as exact full code and verifies all supplied hashes", async () => {
    const saved = await handleSource(storage, { id, mode: "auto" }, root);
    const ref = { id, mode: "auto" as const, hash: saved.sourceHash!, bodyHash: saved.bodyHash!,
                  fileHash: hash(readFileSync(join(root, path))) };
    const result = await handleHydrate(storage, { references: [ref] }, root);
    expect(result.status).toBe("complete");
    expect(result.references).toEqual([{ index: 0, requestedId: id, id, status: "fresh", sourceIndex: 0 }]);
    expect(result.sources[0]).toMatchObject({ id, status: "fresh", mode: "full", source: saved.source,
                                             bodyHash: saved.bodyHash, sourceHash: saved.bodyHash });
    expect(result.sources[0].omittedRanges).toBeUndefined();
    expect(result.omitted.sources).toBe(0);
    expect(await handleHydrate(storage, { references: [ref] }, root)).toEqual(result);
  });

  it("checks a preview hash in its saved mode before expanding to exact full code", async () => {
    writeFileSync(join(root, path), ["export function check() {",
      ...Array.from({ length: 40 }, (_, n) => `  const item${n} = ${n};`), "  return item39;", "}", ""].join("\n"));
    await index();
    const preview = await handleSource(storage, { id, mode: "preview" }, root);
    const result = await handleHydrate(storage, { references: [{ id, hash: preview.sourceHash!, mode: "preview",
      fileHash: hash(readFileSync(join(root, path))) }] }, root);
    expect(result.status).toBe("complete");
    expect(result.sources[0].source).toContain("const item20 = 20");
    expect(result.sources[0].source).not.toContain("omitted");
    expect(result.sources[0].sourceHash).toBe(preview.bodyHash);
    expect(result.sources[0].sourceHash).not.toBe(preview.sourceHash);
    const wrongMode = await handleHydrate(storage, { references: [{ id, hash: preview.sourceHash! }] }, root);
    expect(wrongMode.references[0]).toMatchObject({ status: "changed", reason: "saved_view_hash_mismatch" });
    expect(wrongMode.sources).toEqual([]);
  });

  it("separates stale unindexed files from changed reindexed references without returning old bytes", async () => {
    const before = await handleSource(storage, { id }, root);
    const ref = { id, hash: before.sourceHash! };
    writeFileSync(join(root, path), "export function check(x: number) {\n  return x >= 0;\n}\n");
    const stale = await handleHydrate(storage, { references: [ref] }, root);
    expect(stale.references[0]).toMatchObject({ status: "stale", reason: "index_not_current" });
    expect(stale.sources).toEqual([]);
    await index();
    const changed = await handleHydrate(storage, { references: [ref] }, root);
    expect(changed.references[0]).toMatchObject({ status: "changed", reason: "saved_view_hash_mismatch" });
    expect(changed.sources).toEqual([]);
  });

  it("distinguishes expected file and body hash failures and accepts uppercase hex", async () => {
    const source = await handleSource(storage, { id }, root);
    const fileHash = hash(readFileSync(join(root, path)));
    const accepted = await handleHydrate(storage, { references: [{ id, hash: source.sourceHash!.toUpperCase(),
      bodyHash: source.bodyHash!.toUpperCase(), fileHash: fileHash.toUpperCase() }] }, root);
    expect(accepted.status).toBe("complete");
    for (const [field, reason] of [["fileHash", "saved_file_hash_mismatch"], ["bodyHash", "saved_body_hash_mismatch"]] as const) {
      const result = await handleHydrate(storage, { references: [{ id, [field]: "0".repeat(64) }] }, root);
      expect(result.references[0]).toMatchObject({ status: "changed", reason });
      expect(result.sources).toEqual([]);
    }
  });

  it("returns current live source without claiming that a reference without hashes was unchanged", async () => {
    writeFileSync(join(root, path), "export function check() { return 'current'; }\n");
    await index();
    const result = await handleHydrate(storage, { references: [{ id }] }, root);
    expect(result.references[0].status).toBe("fresh");
    expect(result.sources[0].source).toContain("'current'");
  });

  it("reports missing IDs, deleted files, and ambiguity without choosing a candidate", async () => {
    writeFileSync(join(root, "src/other.ts"), "export function check() { return false; }\n");
    await index();
    const result = await handleHydrate(storage, { references: [{ id: "check" }, { id: "symbol:src/missing.ts:check" }, { id }] }, root);
    expect(result.references.map(item => item.status)).toEqual(["ambiguous", "missing", "fresh"]);
    expect(result.sources).toHaveLength(1);
    expect(result.sources[0].id).toBe(id);
    rmSync(join(root, path));
    const deleted = await handleHydrate(storage, { references: [{ id }] }, root);
    expect(deleted.references[0]).toMatchObject({ status: "missing", reason: "source_unavailable" });
    expect(deleted.sources).toEqual([]);
  });

  it("deduplicates canonical aliases while independently checking each reference", async () => {
    const before = await handleSource(storage, { id }, root);
    const result = await handleHydrate(storage, { references: [{ id }, { id: "check", hash: before.sourceHash! },
      { id, hash: "0".repeat(64) }], maxSymbols: 1 }, root);
    expect(result.sources).toHaveLength(1);
    expect(result.references.map(item => item.status)).toEqual(["fresh", "fresh", "changed"]);
    expect(result.references[1].sourceIndex).toBe(0);
  });

  it("honors symbol and UTF-8 envelope budgets without truncating source or falsely recording delivery", async () => {
    writeFileSync(join(root, path), `export function huge() { return '${"🦊".repeat(500)}'; }\nexport function small() { return 1; }\n`);
    await index();
    const huge = `symbol:${path}:huge`, small = `symbol:${path}:small`;
    const metric = vi.spyOn(storage, "incrementMetric");
    const delivery = vi.spyOn(storage, "recordDelivery");
    const result = await handleHydrate(storage, { references: [{ id: huge }, { id: small }], maxBytes: 1800 }, root);
    expect(Buffer.byteLength(JSON.stringify(result))).toBeLessThanOrEqual(1800);
    expect(result.references.map(item => item.status)).toEqual(["omitted", "fresh"]);
    expect(result.references[0].reason).toBe("max_bytes");
    expect(result.sources[0].source).toBe("export function small() { return 1; }");
    expect(metric.mock.calls).toEqual([["source_bytes_delivered", Buffer.byteLength(result.sources[0].source!)]]);
    expect(delivery).not.toHaveBeenCalled();
    metric.mockClear();
    const capped = await handleHydrate(storage, { references: [{ id: small }, { id: huge }], maxSymbols: 1 }, root);
    expect(capped.references[1]).toMatchObject({ status: "omitted", reason: "max_symbols" });
    expect(metric.mock.calls).toEqual([["source_bytes_delivered", Buffer.byteLength(capped.sources[0].source!)]]);
    expect(delivery).not.toHaveBeenCalled();
  });

  it("preserves every indexed outcome within the minimum budget even with long IDs", async () => {
    const references = Array.from({ length: 8 }, (_, i) => ({ id: `symbol:src/${i}${"x".repeat(3000)}.ts:missing` }));
    const result = await handleHydrate(storage, { references, maxBytes: 1024 }, root);
    expect(Buffer.byteLength(JSON.stringify(result))).toBeLessThanOrEqual(1024);
    expect(result.references.map(item => item.index)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(result.references.every(item => item.status === "missing")).toBe(true);
    expect(result.sources).toEqual([]);
  });

  it("rejects an earlier snapshot if another reference lookup races a file edit", async () => {
    writeFileSync(join(root, "src/other.ts"), "export function other() { return 2; }\n");
    await index();
    const other = "symbol:src/other.ts:other";
    const get = storage.getNode.bind(storage);
    vi.spyOn(storage, "getNode").mockImplementation(async target => {
      if (target === other) writeFileSync(join(root, path), "export function check() { return 'changed'; }\n");
      return get(target);
    });
    const result = await handleHydrate(storage, { references: [{ id }, { id: other }] }, root);
    expect(result.references[0]).toMatchObject({ status: "stale", reason: "changed_during_hydration" });
    expect(result.sources.map(source => source.id)).toEqual([other]);
  });

  it("rechecks source after metric awaits and retracts all alias delivery claims", async () => {
    const original = storage.incrementMetric.bind(storage);
    const metric = vi.spyOn(storage, "incrementMetric").mockImplementation(async (name, delta) => {
      await original(name, delta);
      if (name === "source_bytes_delivered" && delta > 0) {
        writeFileSync(join(root, path), "export function check() { return 'changed'; }\n");
      }
    });
    const result = await handleHydrate(storage, { references: [{ id }, { id: "check" }] }, root);
    expect(result.status).toBe("partial");
    expect(result.sources).toEqual([]);
    expect(result.references.every(item => item.status === "stale" && item.sourceIndex === undefined)).toBe(true);
    expect(metric.mock.calls.reduce((sum, [name, delta]) => name === "source_bytes_delivered" ? sum + delta : sum, 0)).toBe(0);
    expect(Buffer.byteLength(JSON.stringify(result))).toBeLessThanOrEqual(result.limits.maxBytes);
  });

  it("rechecks remaining source after an awaited metric correction", async () => {
    const otherPath = "src/other.ts", other = `symbol:${otherPath}:other`;
    writeFileSync(join(root, otherPath), "export function other() { return 2; }\n");
    await index();
    const original = storage.incrementMetric.bind(storage);
    let changedFirst = false;
    const metric = vi.spyOn(storage, "incrementMetric").mockImplementation(async (name, delta) => {
      await original(name, delta);
      if (name !== "source_bytes_delivered") return;
      if (delta > 0 && !changedFirst) {
        changedFirst = true;
        writeFileSync(join(root, path), "export function check() { return 'changed'; }\n");
      } else if (delta < 0) {
        rmSync(join(root, otherPath), { force: true });
      }
    });
    const result = await handleHydrate(storage, { references: [{ id }, { id: other }] }, root);
    expect(result.sources).toEqual([]);
    expect(result.references.map(item => item.status)).toEqual(["stale", "missing"]);
    expect(metric.mock.calls.filter(([, delta]) => delta < 0)).toHaveLength(2);
    expect(metric.mock.calls.reduce((sum, [name, delta]) => name === "source_bytes_delivered" ? sum + delta : sum, 0)).toBe(0);
  });

  it("validates the complete input before reading source", async () => {
    const get = vi.spyOn(storage, "getNode");
    const invalid = [
      { references: [] }, { references: Array.from({ length: 9 }, () => ({ id })) },
      { references: [{ id: "" }] }, { references: [{ id, hash: "invalid" }] },
      { references: [{ id, mode: "unknown" }] }, { references: [{ id }], maxBytes: 1023 },
      { references: [{ id }], maxBytes: 131073 }, { references: [{ id }], maxSymbols: 0 },
      { references: [{ id }], maxSymbols: 9 },
    ];
    for (const input of invalid) await expect(handleHydrate(storage, input as HydrateInput, root)).rejects.toThrow();
    expect(get).not.toHaveBeenCalled();
  });
});
