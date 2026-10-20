import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SqliteBackend } from "../graph/sqlite.js";
import type { SymbolNode } from "../graph/types.js";
import { Indexer } from "../indexer/indexer.js";
import { handleEnrich } from "./enrich.js";
import { handleRecall } from "./recall.js";
import { handleWorkflow } from "./workflow.js";
import { handleAnswer } from "./answer.js";

describe("cross-session knowledge", () => {
  let root: string;
  let storage: SqliteBackend;
  const path = "src/example.ts";
  const id = `symbol:${path}:check`;

  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), "codemesh-recall-"));
    mkdirSync(join(root, "src"));
    writeFileSync(join(root, path), "export function check(x: number) {\n  return x > 0;\n}\n");
    storage = new SqliteBackend(join(root, "index.db"));
    await storage.initialize();
    await new Indexer(storage, root).index({ withEmbeddings: false });
  });

  afterEach(async () => {
    await storage.close();
    rmSync(root, { recursive: true, force: true });
  });

  it("consolidates exact repeated notes and keeps differing notes visible", async () => {
    const a = await handleEnrich(storage, { path, symbol: "check", summary: "Positive values pass", sessionId: "one" });
    const b = await handleEnrich(storage, { path, symbol: "check", summary: "Positive values pass", sessionId: "two" });
    const c = await handleEnrich(storage, { path, symbol: "check", summary: "Zero also passes", sessionId: "two" });
    expect(a.nodeId).toBe(b.nodeId);
    expect(c.nodeId).not.toBe(a.nodeId);
    const stored = await storage.getNode(a.nodeId);
    expect(stored?.type === "concept" ? stored.history?.[0].sessionId : null).toBe("one");
    const recalled = await handleRecall(storage, { path, symbol: "check" });
    expect(recalled.notes).toHaveLength(2);
    expect(recalled.notes.every((note) => note.status === "hypothesis")).toBe(true);
    expect((await handleRecall(storage, { id: a.nodeId })).notes[0].id).toBe(a.nodeId);
  });

  it("preserves a note and summary when a symbol merely moves, then marks the note stale on a body edit", async () => {
    const old = await storage.getNode(id) as SymbolNode;
    await storage.upsertNode({ ...old, summary: "Checks positivity" });
    await handleEnrich(storage, { path, symbol: "check", summary: "Positive values pass", sessionId: "one", status: "verified" });
    writeFileSync(join(root, path), "// added header\nexport function check(x: number) {\n  return x > 0;\n}\n");
    await new Indexer(storage, root).index({ withEmbeddings: false });
    expect((await storage.getNode(id) as SymbolNode).summary).toBe("Checks positivity");
    expect((await handleRecall(storage, { path, symbol: "check" })).notes[0].status).toBe("verified");
    writeFileSync(join(root, path), "// added header\nexport function check(x: number) {\n  return x >= 0;\n}\n");
    await new Indexer(storage, root).index({ withEmbeddings: false });
    expect((await handleRecall(storage, { path, symbol: "check" })).notes[0].status).toBe("stale");
  });

  it("retains notes and workflow links through a forced structural rebuild", async () => {
    await handleEnrich(storage, { path, symbol: "check", summary: "Positive values pass", sessionId: "one", status: "verified" });
    await handleWorkflow(storage, { name: "Validation flow", description: "Call check", files: [path] });
    await new Indexer(storage, root).index({ withEmbeddings: false, force: true });
    const notes = (await handleRecall(storage, { path, symbol: "check" })).notes;
    expect(notes).toHaveLength(1);
    expect(notes[0].status).toBe("verified");
    const fileNotes = (await handleRecall(storage, { path })).notes;
    expect(fileNotes.some((note) => note.kind === "workflow")).toBe(true);
  });

  it("uses a saved concept as a lexical bridge to code without vectors", async () => {
    expect(await storage.search("blorb protocol")).toEqual([]);
    await handleEnrich(storage, { path, symbol: "check", summary: "Handles the blorb protocol", sessionId: "one" });
    const answer = await handleAnswer(storage, { question: "blorb protocol" }, root);
    expect(answer.relevantFiles.some((file) => file.path === path)).toBe(true);
    expect(answer.suggestedReads.some((read) => read.file === path)).toBe(true);
  });

  it("marks a note stale immediately when the live file changes before indexing", async () => {
    await handleEnrich(storage, { path, symbol: "check", summary: "Positive values pass", sessionId: "one", status: "verified" });
    writeFileSync(join(root, path), "export function check(x: number) {\n  return x >= 0;\n}\n");
    expect((await handleRecall(storage, { path, symbol: "check" }, root)).notes[0].status).toBe("stale");
  });
});
