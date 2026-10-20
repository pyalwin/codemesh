import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SqliteBackend } from "../graph/sqlite.js";
import Database from "better-sqlite3";
import { Indexer } from "../indexer/indexer.js";
import { handleSource } from "./source.js";

describe("revision-aware source", () => {
  let root: string;
  let storage: SqliteBackend;
  const id = "symbol:src/example.ts:check";

  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), "codemesh-source-"));
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

  it("returns exact source and an unchanged reference only for the same rendered view", async () => {
    const full = await handleSource(storage, { id }, root);
    expect(full.status).toBe("fresh");
    expect(full.source).toContain("return x > 0");
    const conditional = await handleSource(storage, { id, ifHash: full.sourceHash! }, root);
    expect(conditional.status).toBe("unchanged");
    expect(conditional.source).toBeNull();
    const metrics = (await storage.getStats()).metrics;
    expect(metrics.source_bytes_delivered).toBeGreaterThan(0);
    expect(metrics.source_bytes_avoided).toBe(Buffer.byteLength(full.source!));
    expect(metrics.conditional_hits).toBe(1);
    const fold = await handleSource(storage, { id, mode: "fold", ifHash: full.sourceHash! }, root);
    expect(fold.status).toBe("fresh");
    expect(fold.source).toContain("function check");
    const restored = await handleSource(storage, { id, ifHash: full.sourceHash!, force: true }, root);
    expect(restored.source).toBe(full.source);
  });

  it("resolves a unique exact name to the canonical ID with the same source hashes", async () => {
    const exact = await handleSource(storage, { id }, root);
    const byName = await handleSource(storage, { id: "check" }, root);
    expect(byName).toEqual(exact);
    const conditional = await handleSource(storage, { id: "check", ifHash: exact.sourceHash! }, root);
    expect(conditional.status).toBe("unchanged");
    expect(conditional.id).toBe(id);
    await expect(handleSource(storage, { id: "chec" }, root)).rejects.toThrow("Symbol not found");
  });

  it("resolves a qualified stored method name or an unambiguous bare method name", async () => {
    writeFileSync(join(root, "src/methods.ts"), "export class Example {\n  verify() { return true; }\n}\n");
    await new Indexer(storage, root).index({ withEmbeddings: false });
    const qualified = await handleSource(storage, { id: "Example.verify" }, root);
    const bare = await handleSource(storage, { id: "verify" }, root);
    expect(qualified.id).toBe("symbol:src/methods.ts:Example.verify");
    expect(qualified.source).toBe("  verify() { return true; }");
    expect(bare).toEqual(qualified);
  });

  it("lists exact candidates for overloads in one file instead of selecting the longest body", async () => {
    rmSync(join(root, "src/example.ts"));
    writeFileSync(join(root, "src/overloads.py"), [
      "from typing import overload", "@overload", "def check(x: str) -> str: ...",
      "@overload", "def check(x: int) -> int: ...", "def check(x):", "    return x", "",
    ].join("\n"));
    await new Indexer(storage, root).index({ withEmbeddings: false });
    const matches = await storage.queryNodes({ type: "symbol", name: "check" });
    expect(matches).toHaveLength(3);
    let message = "";
    try { await handleSource(storage, { id: "check" }, root); }
    catch (error) { message = (error as Error).message; }
    expect(message).toContain("Ambiguous symbol name: 3 matches");
    for (const match of matches) {
      expect(message).toContain(JSON.stringify(match.id));
    }
    expect(message).toContain('"filePath":"src/overloads.py"');
    expect(message).toContain('"lineStart":6,"lineEnd":7');
    const full = await handleSource(storage, { id: "symbol:src/overloads.py:check@L6" }, root);
    expect(full.source).toBe("def check(x):\n    return x");
  });

  it("rejects ambiguity across files but exact IDs retain priority", async () => {
    writeFileSync(join(root, "src/other.ts"), "export function check() { return false; }\n");
    await new Indexer(storage, root).index({ withEmbeddings: false });
    await expect(handleSource(storage, { id: "check" }, root)).rejects.toThrow(/Ambiguous symbol name: 2 matches/);
    await expect(handleSource(storage, { id: "check" }, root)).rejects.toThrow('"id":"symbol:src/other.ts:check","filePath":"src/other.ts","lineStart":1,"lineEnd":1');
    const query = vi.spyOn(storage, "queryNodes");
    const exact = await handleSource(storage, { id }, root);
    expect(exact.id).toBe(id);
    expect(exact.source).toContain("return x > 0");
    await expect(handleSource(storage, { id: "file:src/example.ts" }, root)).rejects.toThrow("Symbol not found");
    await expect(handleSource(storage, { id: "symbol:src/missing.ts:check" }, root)).rejects.toThrow("Symbol not found");
    writeFileSync(join(root, "src/example.ts"), "// modified without indexing\n");
    expect((await handleSource(storage, { id }, root)).status).toBe("stale");
    rmSync(join(root, "src/example.ts"));
    const deleted = await handleSource(storage, { id }, root);
    expect(deleted.status).toBe("deleted");
    expect(deleted.id).toBe(id);
    expect(query).not.toHaveBeenCalled();
  });

  it("requires a bare method name to be unambiguous even against a top-level function", async () => {
    writeFileSync(join(root, "src/methods.ts"), [
      "export class First {", "  check() { return 1; }", "}",
      "export class Second {", "  check() { return 2; }", "}", "",
    ].join("\n"));
    await new Indexer(storage, root).index({ withEmbeddings: false });
    await expect(handleSource(storage, { id: "check" }, root)).rejects.toThrow("Ambiguous symbol name: 3 matches");
    const qualified = await handleSource(storage, { id: "Second.check" }, root);
    expect(qualified.id).toBe("symbol:src/methods.ts:Second.check");
  });

  it("applies live freshness checks to name fallback and never redirects a stale or deleted ID", async () => {
    writeFileSync(join(root, "src/example.ts"), "export function check() { return false; }\n");
    const byName = await handleSource(storage, { id: "check", force: true }, root);
    expect(byName.id).toBe(id);
    expect(byName.status).toBe("stale");
    expect(byName.source).toBeNull();
    const exact = await handleSource(storage, { id }, root);
    expect(exact.status).toBe("stale");
    rmSync(join(root, "src/example.ts"));
    const deleted = await handleSource(storage, { id }, root);
    expect(deleted.status).toBe("deleted");
    expect(deleted.id).toBe(id);
    const deletedName = await handleSource(storage, { id: "check" }, root);
    expect(deletedName.status).toBe("deleted");
  });

  it("returns the exact full body in auto mode for small symbols", async () => {
    const full = await handleSource(storage, { id, mode: "full" }, root);
    const auto = await handleSource(storage, { id, mode: "auto" }, root);
    expect(auto.mode).toBe("auto");
    expect(auto.source).toBe("export function check(x: number) {\n  return x > 0;\n}");
    expect(auto.source).toBe(full.source);
    expect(auto.sourceHash).toBe(full.sourceHash);
    expect(auto.bodyHash).toBe(full.bodyHash);
    expect(auto.omittedRanges).toBeUndefined();
    expect(auto.hint).toBeUndefined();
    const unchanged = await handleSource(storage, { id, mode: "auto", ifHash: full.sourceHash! }, root);
    expect(unchanged.status).toBe("unchanged");
  });

  it("bounds large class navigation while explicit full stays exact and separately recoverable", async () => {
    const body = [
      "export class Example {",
      ...Array.from({ length: 130 }, (_, index) => `  method${index}() { return ${index}; }`),
      "}",
    ].join("\n");
    writeFileSync(join(root, "src/example.ts"), `${body}\n`);
    await new Indexer(storage, root).index({ withEmbeddings: false });
    const classId = "symbol:src/example.ts:Example";
    const auto = await handleSource(storage, { id: classId, mode: "auto" }, root);
    expect(auto.status).toBe("fresh");
    expect(auto.mode).toBe("auto");
    expect(Buffer.byteLength(auto.source!)).toBeLessThanOrEqual(12 * 1024);
    expect(auto.omittedRanges).toEqual([{ start: 13, end: 124 }]);
    expect(auto.source).toContain("\n  method0() { return 0; }\n");
    expect(auto.source).toContain("\n  method129() { return 129; }\n}");
    expect(auto.hint).toContain("Bounded navigation preview");
    expect(auto.hint).toContain("mode=full");

    const unchanged = await handleSource(storage, { id: classId, mode: "auto", ifHash: auto.sourceHash! }, root);
    expect(unchanged.status).toBe("unchanged");
    expect(unchanged.source).toBeNull();
    expect(unchanged.omittedRanges).toEqual(auto.omittedRanges);
    const restored = await handleSource(storage, {
      id: classId, mode: "auto", ifHash: auto.sourceHash!, force: true,
    }, root);
    expect(restored.source).toBe(auto.source);
    expect(restored.sourceHash).toBe(auto.sourceHash);

    const full = await handleSource(storage, { id: classId, mode: "full", ifHash: auto.sourceHash! }, root);
    expect(full.status).toBe("fresh");
    expect(full.mode).toBe("full");
    expect(full.source).toBe(body);
    expect(full.sourceHash).toBe(auto.bodyHash);
    expect(full.sourceHash).not.toBe(auto.sourceHash);
    expect(full.omittedRanges).toBeUndefined();
    expect(full.hint).toBeUndefined();
    const defaultView = await handleSource(storage, { id: classId }, root);
    expect(defaultView.source).toBe(body);
  });

  it("omits oversized UTF-8 lines completely and keeps displayed code lines exact", async () => {
    const oversizedLine = `  const message = "${"🙂".repeat(6000)}";`;
    const body = ["export function check() {", oversizedLine, "  return message;", "}"].join("\n");
    writeFileSync(join(root, "src/example.ts"), `${body}\n`);
    await new Indexer(storage, root).index({ withEmbeddings: false });
    const auto = await handleSource(storage, { id, mode: "auto" }, root);
    expect(auto.status).toBe("fresh");
    expect(auto.source).toBe("export function check() {\n… [lines 2-2 omitted] …\n  return message;\n}");
    expect(auto.omittedRanges).toEqual([{ start: 2, end: 2 }]);
    expect(Buffer.byteLength(auto.source!)).toBeLessThanOrEqual(12 * 1024);
    const full = await handleSource(storage, { id, mode: "full" }, root);
    expect(full.source).toBe(body);
    // Explicit preview retains its existing behavior for symbols with <=24 lines.
    const preview = await handleSource(storage, { id, mode: "preview" }, root);
    expect(preview.source).toBe(body);
    expect(auto.bodyHash).toBe(full.sourceHash);
  });

  it("does not return even a bounded view from stale spans when forced", async () => {
    const current = await handleSource(storage, { id, mode: "auto" }, root);
    writeFileSync(join(root, "src/example.ts"), "export function check() { return false; }\n");
    const stale = await handleSource(storage, { id, mode: "auto", ifHash: current.sourceHash!, force: true }, root);
    expect(stale.status).toBe("stale");
    expect(stale.source).toBeNull();
    expect(stale.bodyHash).toBeNull();
    expect(stale.sourceHash).toBeNull();
  });

  it("can omit the entire source when a single-line symbol exceeds the navigation budget", async () => {
    const body = `export function check() { return "${"x".repeat(20000)}"; }`;
    writeFileSync(join(root, "src/example.ts"), `${body}\n`);
    await new Indexer(storage, root).index({ withEmbeddings: false });
    const auto = await handleSource(storage, { id, mode: "auto" }, root);
    expect(auto.source).toBe("… [lines 1-1 omitted] …");
    expect(auto.omittedRanges).toEqual([{ start: 1, end: 1 }]);
    expect(auto.hint).toContain("mode=full");
    const full = await handleSource(storage, { id, mode: "full" }, root);
    expect(full.source).toBe(body);
  });

  it("does not return old line ranges after an edit", async () => {
    writeFileSync(join(root, "src/example.ts"), "// moved\nexport function check(x: number) {\n  return x > 0;\n}\n");
    const stale = await handleSource(storage, { id }, root);
    expect(stale.status).toBe("stale");
    expect(stale.source).toBeNull();
    await new Indexer(storage, root).index({ withEmbeddings: false });
    const fresh = await handleSource(storage, { id }, root);
    expect(fresh.status).toBe("fresh");
    expect(fresh.lineStart).toBe(2);
  });

  it("records delivered views only when a session and context epoch are supplied", async () => {
    const delivered = await handleSource(storage, { id, mode: "preview", sessionId: "task-1", contextEpoch: "before-compact" }, root);
    const db = new Database(join(root, "index.db"));
    const rows = db.prepare("SELECT source_hash, mode FROM session_deliveries WHERE session_id = ? AND context_epoch = ?")
      .all("task-1", "before-compact") as Array<{ source_hash: string; mode: string }>;
    db.close();
    expect(rows).toEqual([{ source_hash: delivered.sourceHash, mode: "preview" }]);
  });
});
