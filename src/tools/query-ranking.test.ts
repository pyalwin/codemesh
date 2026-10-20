import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SqliteBackend } from "../graph/sqlite.js";
import type { SymbolNode } from "../graph/types.js";
import { handleQuery } from "./query.js";
import { handleMap } from "./map.js";

function symbol(name: string, summary: string, pagerankScore = 0): SymbolNode {
  return {
    id: `symbol:src/example.ts:${name}`,
    type: "symbol",
    source: "static",
    name,
    kind: "function",
    filePath: "src/example.ts",
    signature: `function ${name}()`,
    lineStart: 12,
    lineEnd: 19,
    summary,
    pagerankScore,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  };
}

function symbolAtPath(name: string, filePath: string, pagerankScore = 0, summary = ""): SymbolNode {
  return { ...symbol(name, summary, pagerankScore), id: `symbol:${filePath}:${name}`, filePath };
}

describe("query relevance ranking", () => {
  let storage: SqliteBackend;

  beforeEach(async () => {
    storage = new SqliteBackend(":memory:");
    await storage.initialize();
  });

  afterEach(async () => {
    await storage.close();
  });

  it("ranks a relevant conversion before a popular generic field symbol", async () => {
    const relevant = symbol("convertAlias", "field alias kebab snake case conversion", 0.0001);
    const popular = symbol("Field", "field metadata", 0.9);
    await storage.upsertNode(relevant);
    await storage.upsertNode(popular);
    for (let i = 0; i < 5; i++) {
      await storage.upsertNode(symbol(`fieldHelper${i}`, "field metadata helper"));
    }

    const query = "field alias kebab snake case conversion";
    const candidates = await storage.search(query);
    expect(candidates.find((r) => r.node.id === relevant.id)!.rank)
      .toBeGreaterThan(candidates.find((r) => r.node.id === popular.id)!.rank);
    const output = await handleQuery(storage, { query, limit: 1 });
    expect(output.results[0]).toMatchObject({
      id: relevant.id,
      signature: relevant.signature,
      lineStart: 12,
      lineEnd: 19,
    });
    expect(output.total).toBe(7);
  });

  it("keeps exact symbol names first even when another match has more PageRank", async () => {
    const exact = symbol("cache", "temporary storage");
    const popular = symbol("cacheManager", "cache cache cache", 0.9);
    await storage.upsertNode(exact);
    await storage.upsertNode(popular);

    const output = await handleQuery(storage, { query: "cache" });
    expect(output.results[0]).toMatchObject({ id: exact.id, matchedField: "exact" });
  });

  it("keeps lexical matches before fuzzy fallback scores on another scale", async () => {
    for (const name of ["alpha", "beta", "gamma", "delta"]) {
      await storage.upsertNode(symbol(name, "cache"));
    }
    const fuzzy = symbol("cachex", "temporary storage", 0.9);
    await storage.upsertNode(fuzzy);

    const candidates = await storage.search("cache");
    const lexical = candidates.filter((r) => r.matchedField !== "trigram");
    const fallback = candidates.find((r) => r.node.id === fuzzy.id)!;
    expect(lexical).toHaveLength(4);
    expect(fallback.matchedField).toBe("trigram");
    expect(fallback.rank).toBeGreaterThan(Math.max(...lexical.map((r) => r.rank)));

    const output = await handleQuery(storage, { query: "cache" });
    expect(output.results.slice(0, 4).every((r) => r.matchedField !== "trigram")).toBe(true);
    expect(output.results[4].id).toBe(fuzzy.id);
  });

  it("uses PageRank only to break equal lexical relevance, then sorts IDs", async () => {
    const low = symbol("alpha", "cache", 0.01);
    const high = symbol("beta", "cache", 0.02);
    const tied = symbol("gamma", "cache", 0.02);
    for (const node of [tied, low, high]) await storage.upsertNode(node);

    const candidates = await storage.search("cache");
    expect(new Set(candidates.map((r) => r.rank)).size).toBe(1);
    const output = await handleQuery(storage, { query: "cache" });
    expect(output.results.map((r) => r.id)).toEqual([high.id, tied.id, low.id]);
  });

  it("does not multiply a word's relevance when the query repeats it", async () => {
    const snake = symbol("alpha", "snake", 0.02);
    const generic = symbol("beta", "case", 0.01);
    await storage.upsertNode(snake);
    await storage.upsertNode(generic);

    const distinct = await handleQuery(storage, { query: "snake case" });
    const repeated = await handleQuery(storage, { query: "snake case CASE" });
    expect(distinct.results[0].id).toBe(snake.id);
    expect(repeated.results.map((r) => r.id)).toEqual(distinct.results.map((r) => r.id));
  });

  it("keeps exact qualified names and exact leaves before test helpers", async () => {
    const declaration = symbol("Validator.check", "checks the input", 0);
    const other = symbol("Other.check", "checks the input", 0.1);
    const partial = symbol("test_check", "check check check", 100);
    for (const node of [partial, other, declaration]) await storage.upsertNode(node);
    const qualified = await handleQuery(storage, { query: "Validator.check" });
    expect(qualified.results[0]).toMatchObject({ id: declaration.id, matchedField: "exact" });
    const leaf = await handleQuery(storage, { query: "check" });
    expect(leaf.results.slice(0, 2).map(result => result.id).sort()).toEqual([declaration.id, other.id].sort());
    expect(leaf.results.slice(0, 2).every(result => result.matchedField === "exact_leaf")).toBe(true);
    expect(leaf.results[0]).toHaveProperty("filePath", "src/example.ts");
    expect(leaf.results[0]).toHaveProperty("signature");
  });

  it.each(["check", "Validator.check"])("optionally prefers a shallower tied exact name: %s", async (name) => {
    const shallow = symbolAtPath(name, "pkg/entry.py", 0.01);
    const deep = symbolAtPath(name, "pkg/compat/entry.py", 0.9);
    for (const node of [deep, shallow]) await storage.upsertNode(node);

    const current = await handleQuery(storage, { query: name });
    const explicitCurrent = await handleQuery(storage, { query: name, exactTieBreak: "pagerank" });
    const pathDepth = await handleQuery(storage, { query: name, exactTieBreak: "path-depth" });
    expect(current.results.map(result => result.id)).toEqual([deep.id, shallow.id]);
    expect(explicitCurrent).toEqual(current);
    expect(pathDepth.results.map(result => result.id)).toEqual([shallow.id, deep.id]);
    expect(pathDepth.results.every(result => result.matchedField === "exact")).toBe(true);
  });

  it("uses path depth only within the exact-leaf tier, preserving stronger exact names", async () => {
    const exact = symbolAtPath("check", "pkg/nested/impl/check.py");
    const shallowLeaf = symbolAtPath("Validator.check", "pkg/check.py");
    const deepLeaf = symbolAtPath("Other.check", "pkg/nested/check.py", 0.9);
    const partial = symbolAtPath("checkHelper", "check.py", 100);
    for (const node of [partial, deepLeaf, shallowLeaf, exact]) await storage.upsertNode(node);

    const result = await handleQuery(storage, { query: "check", exactTieBreak: "path-depth" });
    expect(result.results.map(item => item.id)).toEqual([exact.id, shallowLeaf.id, deepLeaf.id, partial.id]);
    expect(result.results.map(item => item.matchedField).slice(0, 3)).toEqual(["exact", "exact_leaf", "exact_leaf"]);
    const qualified = await handleQuery(storage, { query: "Other.check", exactTieBreak: "path-depth" });
    expect(qualified.results[0]).toMatchObject({ id: deepLeaf.id, matchedField: "exact" });
  });

  it("respects an explicit deeper ID or path scope with the path-depth tie-break", async () => {
    const shallow = symbolAtPath("Validator.check", "pkg/entry.py", 0.9);
    const deep = symbolAtPath("Validator.check", "pkg/compat/entry.py");
    for (const node of [shallow, deep]) await storage.upsertNode(node);

    const explicit = await handleQuery(storage, { query: deep.id, exactTieBreak: "path-depth" });
    expect(explicit.results[0]).toMatchObject({ id: deep.id, matchedField: "exact" });
    for (const path of ["pkg/compat", deep.filePath]) {
      const scoped = await handleQuery(storage, { query: "check", path, exactTieBreak: "path-depth" });
      expect(scoped.results.map(result => result.id)).toEqual([deep.id]);
    }
  });

  it("preserves PageRank and deterministic ID order for equally deep exact matches", async () => {
    const low = symbolAtPath("check", "pkg/a.py", 0.01);
    const high = symbolAtPath("check", "pkg/b.py", 0.02);
    const tied = symbolAtPath("check", "pkg/c.py", 0.02);
    for (const node of [tied, low, high]) await storage.upsertNode(node);

    const output = await handleQuery(storage, { query: "check", exactTieBreak: "path-depth" });
    expect(output.results.map(result => result.id)).toEqual([high.id, tied.id, low.id]);
  });

  it("does not use path depth to displace lexical relevance", async () => {
    const shallow = symbolAtPath("generic", "entry.py", 100, "rareterm ".repeat(50));
    const deep = symbolAtPath("specific", "pkg/deep/entry.py", 0, "rareterm commonterm");
    for (const node of [shallow, deep]) await storage.upsertNode(node);
    for (let i = 0; i < 20; i++) await storage.upsertNode(symbol(`filler${i}`, "commonterm"));
    const original = await handleQuery(storage, { query: "rareterm commonterm" });
    const pathDepth = await handleQuery(storage, { query: "rareterm commonterm", exactTieBreak: "path-depth" });
    expect(pathDepth).toEqual(original);
    expect(pathDepth.results[0].id).toBe(deep.id);
  });

  it("preserves PageRank for equally relevant lexical matches at different depths", async () => {
    const shallow = symbolAtPath("alpha", "entry.py", 0.01, "cache");
    const deep = symbolAtPath("beta", "pkg/deep/entry.py", 0.9, "cache");
    for (const node of [shallow, deep]) await storage.upsertNode(node);
    const candidates = await storage.search("cache");
    expect(new Set(candidates.map(result => result.rank)).size).toBe(1);
    const output = await handleQuery(storage, { query: "cache", exactTieBreak: "path-depth" });
    expect(output.results.map(result => result.id)).toEqual([deep.id, shallow.id]);
  });

  it("rejects an invalid exact-match tie-break policy", async () => {
    await expect(handleQuery(storage, {
      query: "check", exactTieBreak: "canonical" as "path-depth",
    })).rejects.toThrow("exactTieBreak must be pagerank or path-depth");
  });

  it("finds exact leaves even when lexical candidates are capped", async () => {
    for (let i = 0; i < 205; i++) await storage.upsertNode(symbol(`test_check_${i}`, "check"));
    const declaration = symbol("Validator.check", "unrelated ".repeat(100));
    await storage.upsertNode(declaration);
    const result = await handleQuery(storage, { query: "check", limit: 1 });
    expect(result.results[0]).toMatchObject({ id: declaration.id, matchedField: "exact_leaf" });
  });

  it("filters source paths before lexical caps and isolates cached scoped searches", async () => {
    for (let i = 0; i < 205; i++) await storage.upsertNode(symbol(`helper${i}`, "needle"));
    const inside = { ...symbol("nested", "needle ".repeat(2) + "background ".repeat(100)),
      id: "symbol:lib/deep/target.ts:nested", filePath: "lib/deep/target.ts" };
    const sibling = { ...symbol("neighbor", "needle"), id: "symbol:library/target.ts:neighbor", filePath: "library/target.ts" };
    await storage.upsertNode(inside);
    await storage.upsertNode(sibling);
    await handleQuery(storage, { query: "needle" });
    const scoped = await handleQuery(storage, { query: "needle", path: "./lib/" });
    expect(scoped.results.map(result => result.id)).toEqual([inside.id]);
    expect(scoped.total).toBe(1);
    expect((await handleQuery(storage, { query: "needle", path: "library" })).results.map(result => result.id)).toEqual([sibling.id]);
    expect((await handleQuery(storage, { query: "needle", path: inside.filePath })).results.map(result => result.id)).toEqual([inside.id]);
    expect((await handleQuery(storage, { query: "needle", path: "lib" })).results).toEqual(scoped.results);
  });

  it("applies the same path boundary before fuzzy candidate caps", async () => {
    for (let i = 0; i < 25; i++) await storage.upsertNode(symbol(`zxcachex${i}`, ""));
    const target = { ...symbol("zxcachexTarget", ""), id: "symbol:lib/target.ts:zxcachexTarget", filePath: "lib/target.ts" };
    await storage.upsertNode(target);
    const scoped = await handleQuery(storage, { query: "zxcache", path: "lib" });
    expect(scoped.results).toHaveLength(1);
    expect(scoped.results[0]).toMatchObject({ id: target.id, matchedField: "trigram" });
  });

  it("applies type scope before the lexical candidate cap", async () => {
    for (let i = 0; i < 205; i++) await storage.upsertNode(symbol(`needle${i}`, "needle"));
    const now = "2026-01-01T00:00:00Z";
    await storage.upsertNode({ id: "file:lib/needle.ts", name: "needle.ts", type: "file", source: "static",
      path: "lib/needle.ts", hash: "a", lastIndexedAt: now, createdAt: now, updatedAt: now });
    await handleQuery(storage, { query: "needle" });
    const files = await handleQuery(storage, { query: "needle", scope: "files", path: "lib" });
    expect(files.results.map(result => result.id)).toEqual(["file:lib/needle.ts"]);
    expect(files.total).toBe(1);
  });

  it("scopes saved concepts and workflows through their source targets", async () => {
    const target = { ...symbol("run", ""), id: "symbol:lib/a.ts:run", filePath: "lib/a.ts" };
    await storage.upsertNode(target);
    const now = "2026-01-01T00:00:00Z";
    await storage.upsertNode({ id: "concept:validation", name: "validation", type: "concept", source: "agent",
      summary: "validation behavior", targetId: target.id, lastUpdatedBy: "test", stale: false,
      createdAt: now, updatedAt: now });
    await storage.upsertNode({ id: "workflow:validation", name: "validation", type: "workflow", source: "agent",
      description: "validation steps", fileSequence: [target.filePath], lastWalkedAt: now, stale: false,
      createdAt: now, updatedAt: now });
    const notes = await handleQuery(storage, { query: "validation", path: "lib" });
    expect(notes.results.map(result => result.id).sort()).toEqual(["concept:validation", "workflow:validation"]);
    const workflows = await handleQuery(storage, { query: "validation", path: target.filePath, scope: "workflows" });
    expect(workflows.results.map(result => result.id)).toEqual(["workflow:validation"]);
    expect((await handleQuery(storage, { query: "validation", path: "library" })).results).toEqual([]);
  });

  it("uses distinct term coverage before lexical popularity and preserves scope", async () => {
    const broad = symbol("broad", "rareterm ".repeat(50), 0.9);
    const complete = symbol("complete", "rareterm commonterm", 0);
    await storage.upsertNode(broad);
    await storage.upsertNode(complete);
    for (let i = 0; i < 20; i++) await storage.upsertNode(symbol(`filler${i}`, "commonterm"));
    expect((await handleQuery(storage, { query: "rareterm commonterm", limit: 1 })).results[0].id).toBe(complete.id);
    expect((await handleQuery(storage, { query: "rareterm", scope: "files" })).results).toEqual([]);
  });

  it("bounds the complete UTF-8 output and reports whole records omitted", async () => {
    for (let i = 0; i < 8; i++) await storage.upsertNode(symbol(`cache${i}`, "cache " + "日本語 ".repeat(20)));
    const output = await handleQuery(storage, { query: "cache", maxBytes: 1024 });
    expect(Buffer.byteLength(JSON.stringify(output))).toBeLessThanOrEqual(1024);
    expect(output).toMatchObject({ truncated: true, omitted: output.total - output.results.length,
      limits: { maxBytes: 1024, limit: 20 } });
    for (const result of output.results) expect(await storage.getNode(result.id)).not.toBeNull();
    const huge = symbol("huge", "é".repeat(2000));
    await storage.upsertNode(huge);
    const oversized = await handleQuery(storage, { query: "huge", maxBytes: 512 });
    expect(oversized.results).toEqual([]);
    expect(oversized.omitted).toBeGreaterThan(0);
    expect(Buffer.byteLength(JSON.stringify(oversized))).toBeLessThanOrEqual(512);
  });

  it("rejects unsafe path constraints and invalid output budgets", async () => {
    for (const path of ["../src", "src/../lib", "/src", "src//lib", "C:\\src", "src\0bad"]) {
      await expect(handleQuery(storage, { query: "cache", path })).rejects.toThrow("path");
    }
    for (const maxBytes of [511, 65537, Number.NaN, 1024.5]) {
      await expect(handleQuery(storage, { query: "cache", maxBytes })).rejects.toThrow("maxBytes");
    }
  });

  it("uses the same exact-first ranking when a search seeds a call map", async () => {
    const exact = symbol("cache", "temporary storage");
    for (let i = 0; i < 6; i++) await storage.upsertNode(symbol(`cacheHelper${i}`, "cache cache cache", 1));
    await storage.upsertNode(exact);
    const result = await handleMap(storage, { query: "cache" }, "/repo");
    expect("startingPoints" in result && result.startingPoints[0].id).toBe(exact.id);
    await expect(handleMap(storage, { query: "cache", maxBytes: 1024 }, "/repo")).rejects.toThrow("cannot be combined");
  });
});
