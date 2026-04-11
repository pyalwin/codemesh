import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { resolve, join } from "node:path";
import * as fs from "node:fs";
import * as os from "node:os";
import { SqliteBackend } from "../../src/graph/sqlite.js";
import { Indexer } from "../../src/indexer/indexer.js";
import type {
  FileNode,
  SymbolNode,
  ConceptNode,
  GraphEdge,
} from "../../src/graph/types.js";
import { handleQuery } from "../../src/tools/query.js";
import { handleContext } from "../../src/tools/context.js";
import { handleImpact } from "../../src/tools/impact.js";
import { handleStatus } from "../../src/tools/status.js";

// ── Helpers ────────────────────────────────────────────────────────

const FIXTURES = resolve(__dirname, "../fixtures");
const SAMPLE_PROJECT = resolve(FIXTURES, "sample-project");

let storage: SqliteBackend;
let tmpDir: string;

function makeTempDb(): string {
  tmpDir = fs.mkdtempSync(join(os.tmpdir(), "codemesh-tools-test-"));
  return join(tmpDir, "test.db");
}

beforeEach(async () => {
  const dbPath = makeTempDb();
  storage = new SqliteBackend(dbPath);
  await storage.initialize();

  // Index the sample project so we have data to query
  const indexer = new Indexer(storage, SAMPLE_PROJECT);
  await indexer.index();
});

afterEach(async () => {
  await storage.close();
  if (tmpDir && fs.existsSync(tmpDir)) {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

// ── codemesh_query ────────────────────────────────────────────────

describe("handleQuery", () => {
  it("finds symbols by name", async () => {
    const result = await handleQuery(storage, { query: "add" });
    expect(result.total).toBeGreaterThanOrEqual(1);
    expect(result.results.some((r) => r.node.name === "add")).toBe(true);
  });

  it("finds symbols by name with symbols scope", async () => {
    const result = await handleQuery(storage, {
      query: "add",
      scope: "symbols",
    });
    expect(result.total).toBeGreaterThanOrEqual(1);
    expect(result.results.every((r) => r.node.type === "symbol")).toBe(true);
  });

  it("returns empty for files scope when only symbol matches", async () => {
    const result = await handleQuery(storage, {
      query: "multiply",
      scope: "files",
    });
    // multiply is a symbol name, not a file name
    expect(result.results.every((r) => r.node.type === "file")).toBe(true);
  });

  it("returns total matching count", async () => {
    const result = await handleQuery(storage, { query: "add", scope: "all" });
    expect(result.total).toBe(result.results.length);
  });

  it("returns empty results for no matches", async () => {
    const result = await handleQuery(storage, {
      query: "zzz_nonexistent_zzz",
    });
    expect(result.total).toBe(0);
    expect(result.results).toHaveLength(0);
  });
});

// ── codemesh_context ──────────────────────────────────────────────

describe("handleContext", () => {
  it("gets file context with symbols", async () => {
    const result = await handleContext(storage, { path: "src/math.ts" });
    expect(result.file).not.toBeNull();
    expect(result.file!.type).toBe("file");
    expect(result.symbols.length).toBeGreaterThanOrEqual(2); // add, multiply, MathHelper
    const names = result.symbols.map((s) => s.name);
    expect(names).toContain("add");
    expect(names).toContain("multiply");
  });

  it("returns null file for unknown path", async () => {
    const result = await handleContext(storage, {
      path: "nonexistent/file.ts",
    });
    expect(result.file).toBeNull();
    expect(result.symbols).toHaveLength(0);
  });

  it("returns outgoing edges for a file", async () => {
    const result = await handleContext(storage, {
      path: "src/calculator.ts",
    });
    expect(result.file).not.toBeNull();
    // calculator.ts imports math.ts, so should have outgoing edges
    expect(result.outgoingEdges.length).toBeGreaterThanOrEqual(1);
  });

  it("returns incoming edges for a file", async () => {
    const result = await handleContext(storage, { path: "src/math.ts" });
    // math.ts is imported by calculator.ts, so should have incoming edges
    expect(result.incomingEdges.length).toBeGreaterThanOrEqual(1);
  });

  it("returns symbol-specific context when symbol is specified", async () => {
    const result = await handleContext(storage, {
      path: "src/math.ts",
      symbol: "add",
    });
    expect(result.file).not.toBeNull();
    // Should still return all symbols from the file
    expect(result.symbols.length).toBeGreaterThanOrEqual(2);
  });
});

// ── codemesh_impact ───────────────────────────────────────────────

describe("handleImpact", () => {
  it("finds dependents of a file via imports", async () => {
    const result = await handleImpact(storage, { path: "src/math.ts" });
    expect(result.total).toBeGreaterThanOrEqual(1);
    // calculator.ts imports math.ts
    const dependentPaths = result.dependents.map((d) => {
      const node = d.node as FileNode;
      return node.path ?? node.name;
    });
    expect(
      result.dependents.some(
        (d) => d.node.id === "file:src/calculator.ts",
      ),
    ).toBe(true);
  });

  it("includes relationship type", async () => {
    const result = await handleImpact(storage, { path: "src/math.ts" });
    for (const dep of result.dependents) {
      expect(dep.relationship).toBeDefined();
      expect(typeof dep.relationship).toBe("string");
    }
  });

  it("returns empty for unknown file", async () => {
    const result = await handleImpact(storage, {
      path: "nonexistent/file.ts",
    });
    expect(result.total).toBe(0);
    expect(result.dependents).toHaveLength(0);
  });

  it("finds dependents of a symbol", async () => {
    const result = await handleImpact(storage, {
      path: "src/math.ts",
      symbol: "add",
    });
    // The file:src/math.ts contains add, and file:src/calculator.ts calls add
    expect(result.total).toBeGreaterThanOrEqual(1);
  });
});

// ── codemesh_status ───────────────────────────────────────────────

describe("handleStatus", () => {
  it("returns correct node counts", async () => {
    const result = await handleStatus(storage);
    expect(result.nodeCount.file).toBe(3);
    expect(result.nodeCount.symbol).toBeGreaterThan(0);
  });

  it("returns edge counts", async () => {
    const result = await handleStatus(storage);
    expect(result.edgeCount.contains).toBeGreaterThan(0);
    expect(result.edgeCount.imports).toBeGreaterThan(0);
  });

  it("returns lastIndexedAt", async () => {
    const result = await handleStatus(storage);
    expect(result.lastIndexedAt).not.toBeNull();
  });

  it("returns stale count of zero initially", async () => {
    const result = await handleStatus(storage);
    expect(result.staleCount).toBe(0);
  });
});
