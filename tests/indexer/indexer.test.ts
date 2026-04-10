import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { resolve, join } from "node:path";
import * as fs from "node:fs";
import * as os from "node:os";
import { SqliteBackend } from "../../src/graph/sqlite.js";
import { Indexer } from "../../src/indexer/indexer.js";
import type { FileNode, SymbolNode, GraphEdge } from "../../src/graph/types.js";

// ── Helpers ────────────────────────────────────────────────────────

const FIXTURES = resolve(__dirname, "../fixtures");
const SAMPLE_PROJECT = resolve(FIXTURES, "sample-project");

let storage: SqliteBackend;
let tmpDir: string;

function makeTempDb(): string {
  tmpDir = fs.mkdtempSync(join(os.tmpdir(), "codemesh-indexer-test-"));
  return join(tmpDir, "test.db");
}

beforeEach(async () => {
  const dbPath = makeTempDb();
  storage = new SqliteBackend(dbPath);
  await storage.initialize();
});

afterEach(async () => {
  await storage.close();
  if (tmpDir && fs.existsSync(tmpDir)) {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

// ── Full indexing ─────────────────────────────────────────────────

describe("Indexer", () => {
  it("indexes all files in the sample project", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    const result = await indexer.index();

    // The sample project has 3 .ts files: math.ts, calculator.ts, index.ts
    expect(result.filesIndexed).toBe(3);
    expect(result.symbolsFound).toBeGreaterThan(0);
    expect(result.edgesCreated).toBeGreaterThan(0);
    expect(result.filesDeleted).toBe(0);
    expect(result.durationMs).toBeGreaterThanOrEqual(0);
  });

  it("creates file nodes for each source file", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    const fileNodes = await storage.queryNodes({ type: "file" });
    expect(fileNodes.length).toBe(3);

    const paths = fileNodes.map((n) => (n as FileNode).path).sort();
    expect(paths).toEqual([
      "src/calculator.ts",
      "src/index.ts",
      "src/math.ts",
    ]);
  });

  it("creates file nodes with correct hashes", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    const fileNodes = await storage.queryNodes({ type: "file" });
    for (const node of fileNodes) {
      const fileNode = node as FileNode;
      // Hash should be a 16-char hex string
      expect(fileNode.hash).toMatch(/^[0-9a-f]{16}$/);
    }
  });

  it("creates symbol nodes for add and multiply", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    const symbolNodes = await storage.queryNodes({ type: "symbol" });
    const symbolNames = symbolNodes.map((n) => n.name);

    expect(symbolNames).toContain("add");
    expect(symbolNames).toContain("multiply");
  });

  it("creates symbol nodes with correct metadata", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    const addNode = await storage.getNode("symbol:src/math.ts:add");
    expect(addNode).not.toBeNull();
    expect(addNode!.type).toBe("symbol");

    const sym = addNode as SymbolNode;
    expect(sym.kind).toBe("function");
    expect(sym.filePath).toBe("src/math.ts");
    expect(sym.lineStart).toBe(1);
    expect(sym.signature).toContain("add");
  });

  it("creates contains edges (file -> symbol)", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    const mathFileId = "file:src/math.ts";
    const edges = await storage.getEdges(mathFileId, "out", ["contains"]);

    expect(edges.length).toBeGreaterThanOrEqual(2); // add, multiply, MathHelper at minimum

    const targetIds = edges.map((e) => e.toId);
    expect(targetIds).toContain("symbol:src/math.ts:add");
    expect(targetIds).toContain("symbol:src/math.ts:multiply");
  });

  it("creates imports edges (calculator -> math)", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    const calcFileId = "file:src/calculator.ts";
    const edges = await storage.getEdges(calcFileId, "out", ["imports"]);

    // calculator.ts imports from ./math
    expect(edges.length).toBeGreaterThanOrEqual(1);

    const targetIds = edges.map((e) => e.toId);
    expect(targetIds).toContain("file:src/math.ts");
  });

  it("creates imports edge from index to calculator", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    const indexFileId = "file:src/index.ts";
    const edges = await storage.getEdges(indexFileId, "out", ["imports"]);

    expect(edges.length).toBeGreaterThanOrEqual(1);

    const targetIds = edges.map((e) => e.toId);
    expect(targetIds).toContain("file:src/calculator.ts");
  });

  it("creates calls edges for function calls", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    // calculator.ts calls add() and MathHelper.square()
    const calcFileId = "file:src/calculator.ts";
    const callEdges = await storage.getEdges(calcFileId, "out", ["calls"]);

    const calleeIds = callEdges.map((e) => e.toId);
    expect(calleeIds).toContain("symbol:src/math.ts:add");
  });

  it("creates correct edge IDs following the convention", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    const mathFileId = "file:src/math.ts";
    const containsEdges = await storage.getEdges(mathFileId, "out", [
      "contains",
    ]);

    for (const edge of containsEdges) {
      expect(edge.id).toMatch(/^edge:contains:file:.+:symbol:.+$/);
      expect(edge.source).toBe("static");
    }
  });

  // ── Incremental re-indexing ─────────────────────────────────────

  it("incremental re-index with no changes indexes 0 files", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);

    // First index
    const first = await indexer.index();
    expect(first.filesIndexed).toBe(3);

    // Second index - nothing changed
    const second = await indexer.index();
    expect(second.filesIndexed).toBe(0);
    expect(second.symbolsFound).toBe(0);
    expect(second.filesDeleted).toBe(0);
  });

  it("preserves all nodes after incremental re-index", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);

    // First index
    await indexer.index();
    const statsAfterFirst = await storage.getStats();
    const fileCountFirst = statsAfterFirst.nodeCount["file"] ?? 0;
    const symbolCountFirst = statsAfterFirst.nodeCount["symbol"] ?? 0;

    // Second index
    await indexer.index();
    const statsAfterSecond = await storage.getStats();
    const fileCountSecond = statsAfterSecond.nodeCount["file"] ?? 0;
    const symbolCountSecond = statsAfterSecond.nodeCount["symbol"] ?? 0;

    expect(fileCountSecond).toBe(fileCountFirst);
    expect(symbolCountSecond).toBe(symbolCountFirst);
  });

  // ── Stats ───────────────────────────────────────────────────────

  it("stats show correct counts after indexing", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    const stats = await storage.getStats();

    expect(stats.nodeCount["file"]).toBe(3);
    expect(stats.nodeCount["symbol"]).toBeGreaterThan(0);
    expect(stats.edgeCount["contains"]).toBeGreaterThan(0);
    expect(stats.lastIndexedAt).not.toBeNull();
  });

  it("stats show import and call edges", async () => {
    const indexer = new Indexer(storage, SAMPLE_PROJECT);
    await indexer.index();

    const stats = await storage.getStats();

    expect(stats.edgeCount["imports"]).toBeGreaterThan(0);
  });

  // ── Incremental with file changes ──────────────────────────────

  it("detects and re-indexes changed files", async () => {
    // Create a temp project to modify
    const tempProject = fs.mkdtempSync(
      join(os.tmpdir(), "codemesh-change-test-"),
    );
    const srcDir = join(tempProject, "src");
    fs.mkdirSync(srcDir);

    // Write initial file
    fs.writeFileSync(
      join(srcDir, "hello.ts"),
      'export function hello() { return "hi"; }\n',
    );

    const indexer = new Indexer(storage, tempProject);

    // First index
    const first = await indexer.index();
    expect(first.filesIndexed).toBe(1);
    expect(first.symbolsFound).toBe(1);

    // Modify the file
    fs.writeFileSync(
      join(srcDir, "hello.ts"),
      'export function hello() { return "hello world"; }\nexport function goodbye() { return "bye"; }\n',
    );

    // Re-index
    const second = await indexer.index();
    expect(second.filesIndexed).toBe(1);
    expect(second.symbolsFound).toBe(2); // hello + goodbye

    // Clean up
    fs.rmSync(tempProject, { recursive: true, force: true });
  });

  it("detects and removes deleted files", async () => {
    // Create a temp project with two files
    const tempProject = fs.mkdtempSync(
      join(os.tmpdir(), "codemesh-delete-test-"),
    );
    const srcDir = join(tempProject, "src");
    fs.mkdirSync(srcDir);

    fs.writeFileSync(
      join(srcDir, "a.ts"),
      "export function funcA() { return 1; }\n",
    );
    fs.writeFileSync(
      join(srcDir, "b.ts"),
      "export function funcB() { return 2; }\n",
    );

    const indexer = new Indexer(storage, tempProject);

    // First index
    const first = await indexer.index();
    expect(first.filesIndexed).toBe(2);

    // Delete one file
    fs.unlinkSync(join(srcDir, "b.ts"));

    // Re-index
    const second = await indexer.index();
    expect(second.filesDeleted).toBe(1);

    // Verify only one file node remains
    const fileNodes = await storage.queryNodes({ type: "file" });
    expect(fileNodes.length).toBe(1);
    expect((fileNodes[0] as FileNode).path).toBe("src/a.ts");

    // Clean up
    fs.rmSync(tempProject, { recursive: true, force: true });
  });

  // ── Ignore patterns ────────────────────────────────────────────

  it("ignores node_modules and .git directories", async () => {
    const tempProject = fs.mkdtempSync(
      join(os.tmpdir(), "codemesh-ignore-test-"),
    );

    // Create a file in the root
    fs.writeFileSync(
      join(tempProject, "main.ts"),
      "export const x = 1;\n",
    );

    // Create node_modules with a .ts file (should be ignored)
    fs.mkdirSync(join(tempProject, "node_modules", "pkg"), { recursive: true });
    fs.writeFileSync(
      join(tempProject, "node_modules", "pkg", "index.ts"),
      "export const y = 2;\n",
    );

    // Create .git with a file (should be ignored)
    fs.mkdirSync(join(tempProject, ".git"), { recursive: true });
    fs.writeFileSync(
      join(tempProject, ".git", "config.ts"),
      "export const z = 3;\n",
    );

    const indexer = new Indexer(storage, tempProject);
    const result = await indexer.index();

    // Only main.ts should be indexed
    expect(result.filesIndexed).toBe(1);

    const fileNodes = await storage.queryNodes({ type: "file" });
    expect(fileNodes.length).toBe(1);
    expect((fileNodes[0] as FileNode).path).toBe("main.ts");

    // Clean up
    fs.rmSync(tempProject, { recursive: true, force: true });
  });

  it("respects .gitignore patterns", async () => {
    const tempProject = fs.mkdtempSync(
      join(os.tmpdir(), "codemesh-gitignore-test-"),
    );

    // Create .gitignore
    fs.writeFileSync(join(tempProject, ".gitignore"), "ignored/\n*.generated.ts\n");

    // Create a normal file
    fs.writeFileSync(
      join(tempProject, "main.ts"),
      "export const x = 1;\n",
    );

    // Create an ignored file
    fs.writeFileSync(
      join(tempProject, "foo.generated.ts"),
      "export const y = 2;\n",
    );

    // Create an ignored directory with a file
    fs.mkdirSync(join(tempProject, "ignored"), { recursive: true });
    fs.writeFileSync(
      join(tempProject, "ignored", "secret.ts"),
      "export const z = 3;\n",
    );

    const indexer = new Indexer(storage, tempProject);
    const result = await indexer.index();

    expect(result.filesIndexed).toBe(1);

    const fileNodes = await storage.queryNodes({ type: "file" });
    expect(fileNodes.length).toBe(1);
    expect((fileNodes[0] as FileNode).path).toBe("main.ts");

    // Clean up
    fs.rmSync(tempProject, { recursive: true, force: true });
  });

  it("only processes files with supported extensions", async () => {
    const tempProject = fs.mkdtempSync(
      join(os.tmpdir(), "codemesh-ext-test-"),
    );

    fs.writeFileSync(
      join(tempProject, "app.ts"),
      "export const x = 1;\n",
    );
    fs.writeFileSync(join(tempProject, "readme.md"), "# Hello\n");
    fs.writeFileSync(
      join(tempProject, "data.json"),
      '{"key": "value"}\n',
    );

    const indexer = new Indexer(storage, tempProject);
    const result = await indexer.index();

    expect(result.filesIndexed).toBe(1);

    // Clean up
    fs.rmSync(tempProject, { recursive: true, force: true });
  });
});
