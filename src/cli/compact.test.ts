import { describe, expect, it } from "vitest";
import { compactExploreResult } from "./compact.js";
import type { SourceOutput } from "../tools/source.js";

describe("compact CLI observations", () => {
  it("keeps exact inspect/hydrate source and freshness decisions within original budget", () => {
    const source: SourceOutput = {
      status: "fresh", symbol: "check", id: "symbol:src/a.ts:check",
      filePath: "src/a.ts", absolutePath: "/workspace/src/a.ts", kind: "function",
      signature: "function check()", summary: "checks", lineStart: 4, lineEnd: 6,
      mode: "full", sourceHash: "a".repeat(64), bodyHash: "b".repeat(64),
      indexRevision: "rev", source: "function check() {\n  return true;\n}",
    };
    const envelope = {
      status: "partial", sources: [source], calls: [{ id: "symbol:src/a.ts:helper", resolution: "unverified" }],
      references: [{ index: 0, id: source.id, status: "fresh", sourceIndex: 0 },
        { index: 1, status: "changed", reason: "saved_body_hash_mismatch" }],
      omitted: { sources: 0 }, limits: { maxBytes: 24576, maxSymbols: 4 },
    };
    for (const command of ["inspect", "hydrate"]) {
      const compact = compactExploreResult(command, envelope) as typeof envelope;
      expect(compact.sources[0].source).toBe(source.source);
      expect(compact.sources[0].sourceHash).toBe(source.sourceHash);
      expect(compact.sources[0].bodyHash).toBe(source.bodyHash);
      expect(compact.sources[0].indexRevision).toBe(source.indexRevision);
      expect(compact.references).toEqual(envelope.references);
      expect(compact.calls).toEqual(envelope.calls);
      expect(Buffer.byteLength(JSON.stringify(compact))).toBeLessThanOrEqual(Buffer.byteLength(JSON.stringify(envelope)));
    }
  });

  it("keeps source bytes, revision reference, and omitted spans without duplicate metadata", () => {
    const source: SourceOutput = {
      status: "fresh", symbol: "check", id: "symbol:src/a.ts:check",
      filePath: "src/a.ts", absolutePath: "/workspace/src/a.ts", kind: "function",
      signature: "function check()", summary: "checks", lineStart: 4, lineEnd: 8,
      mode: "preview", sourceHash: "abc", bodyHash: "def", indexRevision: "rev",
      source: "function check() {\n…\n}", omittedRanges: [{ start: 5, end: 7 }],
    };
    const compact = compactExploreResult("source", source) as Record<string, unknown>;
    expect(compact).toEqual({ status: "fresh", id: source.id, path: source.filePath,
      lines: [4, 8], mode: "preview", hash: "abc", source: source.source,
      omittedRanges: [{ start: 5, end: 7 }] });
    expect(JSON.stringify(compact)).not.toContain("absolutePath");
  });

  it("keeps search IDs, short summaries, and match reasons while dropping rank", () => {
    const compact = compactExploreResult("search", { total: 1, results: [{
      id: "symbol:src/a.ts:check", name: "check", type: "symbol", filePath: "src/a.ts",
      kind: "function", summary: "long repeated summary", matchedField: "name",
      pagerankScore: 0.5, signature: "function check()", lineStart: 4, lineEnd: 8,
    }] });
    expect(compact).toEqual({ total: 1, results: [{ id: "symbol:src/a.ts:check",
      name: "check", kind: "function", signature: "function check()", lines: [4, 8],
      summary: "long repeated summary", why: "name" }] });
    const long = compactExploreResult("search", { total: 1, results: [{
      id: "symbol:src/a.ts:check", name: "check", type: "symbol",
      matchedField: "summary", summary: "x".repeat(900),
    }] }) as { results: Array<{ summary: string }> };
    expect(long.results[0].summary.length).toBe(160);
    expect(long.results[0].summary.endsWith("…")).toBe(true);
  });

  it("preserves omission counts and next-page recovery while compacting", () => {
    const search = { results: [], total: 8, truncated: true, omitted: 8, limits: { maxBytes: 512, limit: 20 } };
    expect(compactExploreResult("search", search)).toEqual(search);
    const map = { node: { id: "repo", name: "repo", type: "repo" }, entries: [], nextCursor: "0",
      truncated: true, omitted: { entries: 3 }, maxBytes: 512, hint: "Increase budget" };
    expect(compactExploreResult("map", map)).toEqual(map);
  });
});
