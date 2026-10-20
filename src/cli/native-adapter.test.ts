import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SqliteBackend } from "../graph/sqlite.js";
import { Indexer } from "../indexer/indexer.js";
import { foldNativeSearch, outlineNativeRead } from "./native-adapter.js";

describe("native observation adapter", () => {
  it("folds complete rg lines and preserves every path, line number, and match", () => {
    const original = Array.from({ length: 8 }, (_, i) => `src/example.ts:${i + 1}:match ${i}\n`).join("");
    const result = foldNativeSearch(original);
    expect(result.transformed).toBe(true);
    expect(result.output.length).toBeLessThan(original.length);
    const unfolded = result.output.trimEnd().split("\n").reduce<{ path: string; lines: string[] }>((state, line) => {
      if (!line.startsWith("  ")) state.path = line.slice(0, -1);
      else state.lines.push(`${state.path}:${line.slice(2)}`);
      return state;
    }, { path: "", lines: [] }).lines.join("\n") + "\n";
    expect(unfolded).toBe(original);
    expect(foldNativeSearch("src/a.ts:1:ok\nerror\n").output).toBe("src/a.ts:1:ok\nerror\n");
    expect(foldNativeSearch("  odd.ts:1:ok\n  odd.ts:2:ok\n").transformed).toBe(false);
  });

  let root: string;
  let storage: SqliteBackend;
  const path = "src/example.ts";
  const body = `export function check(x: number) {\n  let y = x;\n${"  y += 1;\n".repeat(80)}  return y;\n}\n`;

  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), "codemesh-adapt-"));
    mkdirSync(join(root, "src"));
    writeFileSync(join(root, path), body);
    storage = new SqliteBackend(join(root, "index.db"));
    await storage.initialize();
    await new Indexer(storage, root).index({ withEmbeddings: false });
  });

  afterEach(async () => {
    await storage.close();
    rmSync(root, { recursive: true, force: true });
  });

  it("outlines a verified full read for navigation and preserves edit or partial reads", async () => {
    const outline = await outlineNativeRead(storage, root, path, body, "navigate");
    expect(outline.reason).toBe("verified_read_outline");
    expect(outline.output).toContain("symbol:src/example.ts:check");
    expect(outline.output).toContain("bodies omitted");
    expect(await outlineNativeRead(storage, root, path, body, "edit"))
      .toMatchObject({ output: body, transformed: false });
    expect(await outlineNativeRead(storage, root, path, body.slice(0, 200), "navigate"))
      .toMatchObject({ transformed: false, reason: "partial_or_modified_read" });
    writeFileSync(join(root, path), `${body}// changed\n`);
    expect(await outlineNativeRead(storage, root, path, `${body}// changed\n`, "navigate"))
      .toMatchObject({ transformed: false, reason: "stale_index" });
  });
});
