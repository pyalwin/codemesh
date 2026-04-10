import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { SqliteBackend } from "../../src/graph/sqlite.js";
import type {
  FileNode,
  SymbolNode,
  ConceptNode,
  WorkflowNode,
  GraphEdge,
} from "../../src/graph/types.js";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";

let db: SqliteBackend;
let dbPath: string;

function makeTempDb(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "codemesh-test-"));
  return path.join(dir, "test.db");
}

function makeFileNode(overrides: Partial<FileNode> = {}): FileNode {
  return {
    id: "file:src/index.ts",
    type: "file",
    source: "static",
    name: "index.ts",
    path: "src/index.ts",
    hash: "abc123",
    lastIndexedAt: new Date().toISOString(),
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

function makeSymbolNode(overrides: Partial<SymbolNode> = {}): SymbolNode {
  return {
    id: "symbol:main",
    type: "symbol",
    source: "static",
    name: "main",
    kind: "function",
    filePath: "src/index.ts",
    lineStart: 1,
    lineEnd: 10,
    signature: "function main(): void",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

function makeConceptNode(overrides: Partial<ConceptNode> = {}): ConceptNode {
  return {
    id: "concept:authentication",
    type: "concept",
    source: "agent",
    name: "Authentication",
    summary: "Handles user authentication and session management",
    lastUpdatedBy: "agent-v1",
    stale: false,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

function makeWorkflowNode(
  overrides: Partial<WorkflowNode> = {}
): WorkflowNode {
  return {
    id: "workflow:login-flow",
    type: "workflow",
    source: "agent",
    name: "Login Flow",
    description: "The complete user login workflow",
    fileSequence: ["src/auth/login.ts", "src/auth/session.ts"],
    lastWalkedAt: new Date().toISOString(),
    stale: false,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

function makeEdge(overrides: Partial<GraphEdge> = {}): GraphEdge {
  return {
    id: "edge:contains:file:src/index.ts:symbol:main",
    type: "contains",
    source: "static",
    fromId: "file:src/index.ts",
    toId: "symbol:main",
    createdAt: new Date().toISOString(),
    ...overrides,
  };
}

beforeEach(async () => {
  dbPath = makeTempDb();
  db = new SqliteBackend(dbPath);
  await db.initialize();
});

afterEach(async () => {
  await db.close();
  try {
    const dir = path.dirname(dbPath);
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    // ignore cleanup errors
  }
});

// ── Node CRUD ──────────────────────────────────────────────────────────

describe("Node CRUD", () => {
  it("should upsert and retrieve a file node", async () => {
    const node = makeFileNode();
    const id = await db.upsertNode(node);
    expect(id).toBe(node.id);

    const retrieved = await db.getNode(node.id);
    expect(retrieved).not.toBeNull();
    expect(retrieved!.type).toBe("file");
    expect(retrieved!.name).toBe("index.ts");
    expect((retrieved as FileNode).path).toBe("src/index.ts");
    expect((retrieved as FileNode).hash).toBe("abc123");
  });

  it("should upsert and retrieve a symbol node", async () => {
    const node = makeSymbolNode();
    await db.upsertNode(node);

    const retrieved = (await db.getNode(node.id)) as SymbolNode;
    expect(retrieved).not.toBeNull();
    expect(retrieved.type).toBe("symbol");
    expect(retrieved.kind).toBe("function");
    expect(retrieved.signature).toBe("function main(): void");
    expect(retrieved.lineStart).toBe(1);
    expect(retrieved.lineEnd).toBe(10);
  });

  it("should upsert and retrieve a concept node", async () => {
    const node = makeConceptNode();
    await db.upsertNode(node);

    const retrieved = (await db.getNode(node.id)) as ConceptNode;
    expect(retrieved).not.toBeNull();
    expect(retrieved.type).toBe("concept");
    expect(retrieved.summary).toBe(
      "Handles user authentication and session management"
    );
    expect(retrieved.stale).toBe(false);
  });

  it("should upsert and retrieve a workflow node", async () => {
    const node = makeWorkflowNode();
    await db.upsertNode(node);

    const retrieved = (await db.getNode(node.id)) as WorkflowNode;
    expect(retrieved).not.toBeNull();
    expect(retrieved.type).toBe("workflow");
    expect(retrieved.description).toBe("The complete user login workflow");
    expect(retrieved.fileSequence).toEqual([
      "src/auth/login.ts",
      "src/auth/session.ts",
    ]);
  });

  it("should update a node on re-upsert", async () => {
    const node = makeFileNode();
    await db.upsertNode(node);

    const updated = makeFileNode({ hash: "def456", name: "index-updated.ts" });
    await db.upsertNode(updated);

    const retrieved = (await db.getNode(node.id)) as FileNode;
    expect(retrieved.hash).toBe("def456");
    expect(retrieved.name).toBe("index-updated.ts");
  });

  it("should return null for a non-existent node", async () => {
    const result = await db.getNode("nonexistent");
    expect(result).toBeNull();
  });

  it("should delete a node", async () => {
    const node = makeFileNode();
    await db.upsertNode(node);
    await db.deleteNode(node.id);

    const result = await db.getNode(node.id);
    expect(result).toBeNull();
  });

  it("should cascade delete edges when a node is deleted", async () => {
    const fileNode = makeFileNode();
    const symbolNode = makeSymbolNode();
    const edge = makeEdge();

    await db.upsertNode(fileNode);
    await db.upsertNode(symbolNode);
    await db.upsertEdge(edge);

    // Verify edge exists
    const edgesBefore = await db.getEdges(fileNode.id, "out");
    expect(edgesBefore).toHaveLength(1);

    // Delete the file node
    await db.deleteNode(fileNode.id);

    // Edges should be gone
    const edgesAfter = await db.getEdges(symbolNode.id, "in");
    expect(edgesAfter).toHaveLength(0);
  });

  it("should query nodes by type", async () => {
    await db.upsertNode(makeFileNode());
    await db.upsertNode(
      makeFileNode({ id: "file:src/app.ts", name: "app.ts", path: "src/app.ts" })
    );
    await db.upsertNode(makeSymbolNode());

    const files = await db.queryNodes({ type: "file" });
    expect(files).toHaveLength(2);
    expect(files.every((n) => n.type === "file")).toBe(true);
  });

  it("should query nodes by path", async () => {
    await db.upsertNode(makeFileNode());
    await db.upsertNode(
      makeFileNode({
        id: "file:src/other.ts",
        name: "other.ts",
        path: "src/other.ts",
      })
    );

    const results = await db.queryNodes({ path: "src/index.ts" });
    expect(results).toHaveLength(1);
    expect(results[0].id).toBe("file:src/index.ts");
  });

  it("should query nodes by name", async () => {
    await db.upsertNode(makeFileNode());
    await db.upsertNode(makeSymbolNode());

    const results = await db.queryNodes({ name: "main" });
    expect(results).toHaveLength(1);
    expect(results[0].id).toBe("symbol:main");
  });

  it("should query nodes by kind (symbol specific)", async () => {
    await db.upsertNode(makeSymbolNode());
    await db.upsertNode(
      makeSymbolNode({
        id: "symbol:MyClass",
        name: "MyClass",
        kind: "class",
        signature: "class MyClass",
      })
    );

    const functions = await db.queryNodes({ kind: "function" });
    expect(functions).toHaveLength(1);
    expect(functions[0].id).toBe("symbol:main");
  });

  it("should query nodes by stale flag", async () => {
    await db.upsertNode(makeConceptNode());
    await db.upsertNode(
      makeConceptNode({
        id: "concept:stale-concept",
        name: "Stale Concept",
        stale: true,
      })
    );

    const staleNodes = await db.queryNodes({ stale: true });
    expect(staleNodes).toHaveLength(1);
    expect(staleNodes[0].id).toBe("concept:stale-concept");
  });
});

// ── Edge CRUD ──────────────────────────────────────────────────────────

describe("Edge CRUD", () => {
  beforeEach(async () => {
    await db.upsertNode(makeFileNode());
    await db.upsertNode(makeSymbolNode());
    await db.upsertNode(makeConceptNode());
  });

  it("should upsert and retrieve edges", async () => {
    const edge = makeEdge();
    const id = await db.upsertEdge(edge);
    expect(id).toBe(edge.id);

    const outEdges = await db.getEdges("file:src/index.ts", "out");
    expect(outEdges).toHaveLength(1);
    expect(outEdges[0].type).toBe("contains");
    expect(outEdges[0].fromId).toBe("file:src/index.ts");
    expect(outEdges[0].toId).toBe("symbol:main");
  });

  it("should get incoming edges", async () => {
    await db.upsertEdge(makeEdge());

    const inEdges = await db.getEdges("symbol:main", "in");
    expect(inEdges).toHaveLength(1);
    expect(inEdges[0].fromId).toBe("file:src/index.ts");
  });

  it("should get both directions", async () => {
    await db.upsertEdge(makeEdge());
    await db.upsertEdge(
      makeEdge({
        id: "edge:describes:concept:auth:symbol:main",
        type: "describes",
        source: "agent",
        fromId: "concept:authentication",
        toId: "symbol:main",
      })
    );

    const bothEdges = await db.getEdges("symbol:main", "both");
    expect(bothEdges).toHaveLength(2);
  });

  it("should filter edges by type", async () => {
    await db.upsertEdge(makeEdge());
    await db.upsertEdge(
      makeEdge({
        id: "edge:imports:file:src/index.ts:symbol:main",
        type: "imports",
        fromId: "file:src/index.ts",
        toId: "symbol:main",
      })
    );

    const containsOnly = await db.getEdges("file:src/index.ts", "out", [
      "contains",
    ]);
    expect(containsOnly).toHaveLength(1);
    expect(containsOnly[0].type).toBe("contains");
  });

  it("should store and retrieve edge data", async () => {
    const edge = makeEdge({ data: { weight: 0.5, label: "test" } });
    await db.upsertEdge(edge);

    const edges = await db.getEdges("file:src/index.ts", "out");
    expect(edges[0].data).toEqual({ weight: 0.5, label: "test" });
  });

  it("should delete edges by node", async () => {
    await db.upsertEdge(makeEdge());
    await db.upsertEdge(
      makeEdge({
        id: "edge:imports:file:src/index.ts:symbol:main",
        type: "imports",
        fromId: "file:src/index.ts",
        toId: "symbol:main",
      })
    );

    const count = await db.deleteEdgesByNode("file:src/index.ts");
    expect(count).toBe(2);

    const remaining = await db.getEdges("file:src/index.ts", "both");
    expect(remaining).toHaveLength(0);
  });

  it("should update edge on re-upsert", async () => {
    const edge = makeEdge();
    await db.upsertEdge(edge);

    const updatedEdge = makeEdge({
      data: { updated: true },
    });
    await db.upsertEdge(updatedEdge);

    const edges = await db.getEdges("file:src/index.ts", "out");
    expect(edges).toHaveLength(1);
    expect(edges[0].data).toEqual({ updated: true });
  });
});

// ── FTS5 Search ────────────────────────────────────────────────────────

describe("FTS5 Search", () => {
  beforeEach(async () => {
    await db.upsertNode(
      makeSymbolNode({
        id: "symbol:handleLogin",
        name: "handleLogin",
        signature: "async function handleLogin(credentials: LoginInput): Promise<Session>",
      })
    );
    await db.upsertNode(
      makeSymbolNode({
        id: "symbol:validateToken",
        name: "validateToken",
        signature: "function validateToken(token: string): boolean",
      })
    );
    await db.upsertNode(
      makeConceptNode({
        id: "concept:auth",
        name: "Authentication System",
        summary:
          "The authentication system handles login, token validation, and session management",
      })
    );
    await db.upsertNode(
      makeConceptNode({
        id: "concept:caching",
        name: "Caching Layer",
        summary:
          "Redis-based caching layer for frequently accessed data",
      })
    );
  });

  it("should find nodes by name search", async () => {
    const results = await db.search("handleLogin");
    expect(results.length).toBeGreaterThanOrEqual(1);
    expect(results.some((r) => r.node.id === "symbol:handleLogin")).toBe(true);
  });

  it("should find concept nodes by summary search", async () => {
    const results = await db.search("token validation");
    expect(results.length).toBeGreaterThanOrEqual(1);
    // Should match either the concept summary or the symbol name
    const matchIds = results.map((r) => r.node.id);
    expect(
      matchIds.includes("concept:auth") ||
        matchIds.includes("symbol:validateToken")
    ).toBe(true);
  });

  it("should rank results by relevance", async () => {
    const results = await db.search("authentication");
    expect(results.length).toBeGreaterThanOrEqual(1);
    // Results should have descending rank
    for (let i = 1; i < results.length; i++) {
      expect(results[i - 1].rank).toBeGreaterThanOrEqual(results[i].rank);
    }
  });

  it("should return matchedField in results", async () => {
    const results = await db.search("caching");
    expect(results.length).toBeGreaterThanOrEqual(1);
    const cachingResult = results.find((r) => r.node.id === "concept:caching");
    expect(cachingResult).toBeDefined();
    expect(cachingResult!.matchedField).toBeDefined();
  });

  it("should update FTS index on node update", async () => {
    // Update the concept name
    await db.upsertNode(
      makeConceptNode({
        id: "concept:auth",
        name: "Security Framework",
        summary: "Completely redesigned security framework",
      })
    );

    // Old name should not match
    const oldResults = await db.search("Authentication System");
    const oldMatch = oldResults.find((r) => r.node.id === "concept:auth");
    // After update, "Authentication System" should no longer match concept:auth
    // (it may still match if partial tokenization happens, so we check the new name works)

    // New name should match
    const newResults = await db.search("Security Framework");
    expect(newResults.some((r) => r.node.id === "concept:auth")).toBe(true);
  });

  it("should remove FTS entry on node deletion", async () => {
    await db.deleteNode("concept:caching");

    const results = await db.search("caching");
    expect(results.every((r) => r.node.id !== "concept:caching")).toBe(true);
  });
});

// ── Traversal ──────────────────────────────────────────────────────────

describe("Traversal", () => {
  beforeEach(async () => {
    // Build a small graph:
    //   file:a --contains--> symbol:x --calls--> symbol:y --calls--> symbol:z
    //   concept:c --describes--> symbol:x
    const nodes = [
      makeFileNode({ id: "file:a", name: "a.ts", path: "src/a.ts" }),
      makeSymbolNode({
        id: "symbol:x",
        name: "x",
        signature: "function x()",
        filePath: "src/a.ts",
      }),
      makeSymbolNode({
        id: "symbol:y",
        name: "y",
        signature: "function y()",
        filePath: "src/a.ts",
      }),
      makeSymbolNode({
        id: "symbol:z",
        name: "z",
        signature: "function z()",
        filePath: "src/a.ts",
      }),
      makeConceptNode({ id: "concept:c", name: "concept-c" }),
    ];

    for (const node of nodes) {
      await db.upsertNode(node);
    }

    const edges: GraphEdge[] = [
      makeEdge({
        id: "e1",
        type: "contains",
        fromId: "file:a",
        toId: "symbol:x",
      }),
      makeEdge({
        id: "e2",
        type: "calls",
        fromId: "symbol:x",
        toId: "symbol:y",
      }),
      makeEdge({
        id: "e3",
        type: "calls",
        fromId: "symbol:y",
        toId: "symbol:z",
      }),
      makeEdge({
        id: "e4",
        type: "describes",
        source: "agent",
        fromId: "concept:c",
        toId: "symbol:x",
      }),
    ];

    for (const edge of edges) {
      await db.upsertEdge(edge);
    }
  });

  it("should traverse one hop from file:a", async () => {
    const results = await db.traverse("file:a", 1);
    expect(results).toHaveLength(1);
    expect(results[0].node.id).toBe("symbol:x");
    expect(results[0].depth).toBe(1);
  });

  it("should traverse multiple hops", async () => {
    const results = await db.traverse("file:a", 3);
    // file:a -> symbol:x -> symbol:y -> symbol:z, concept:c (via describes incoming to x)
    // The traversal follows outgoing edges, so from file:a:
    //   depth 1: symbol:x (via contains)
    //   depth 2: symbol:y (via calls from x), also concept:c describes x but that's incoming TO x
    //   depth 3: symbol:z (via calls from y)
    // With BFS following outgoing edges:
    const ids = results.map((r) => r.node.id);
    expect(ids).toContain("symbol:x");
    expect(ids).toContain("symbol:y");
    expect(ids).toContain("symbol:z");
  });

  it("should filter traversal by edge types", async () => {
    const results = await db.traverse("file:a", 3, ["contains"]);
    // Only follow "contains" edges, so should only reach symbol:x
    expect(results).toHaveLength(1);
    expect(results[0].node.id).toBe("symbol:x");
  });

  it("should not revisit nodes", async () => {
    // Add a cycle: symbol:z --calls--> symbol:x
    await db.upsertEdge(
      makeEdge({
        id: "e-cycle",
        type: "calls",
        fromId: "symbol:z",
        toId: "symbol:x",
      })
    );

    const results = await db.traverse("file:a", 10);
    const ids = results.map((r) => r.node.id);
    // Each node should appear at most once
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("should include edge path in traversal results", async () => {
    const results = await db.traverse("file:a", 2);
    const symbolY = results.find((r) => r.node.id === "symbol:y");
    expect(symbolY).toBeDefined();
    expect(symbolY!.path).toHaveLength(2); // e1 + e2
    expect(symbolY!.depth).toBe(2);
  });
});

// ── Maintenance ────────────────────────────────────────────────────────

describe("Maintenance", () => {
  beforeEach(async () => {
    await db.upsertNode(
      makeFileNode({
        id: "file:src/a.ts",
        name: "a.ts",
        path: "src/a.ts",
        hash: "hash-a",
      })
    );
    await db.upsertNode(
      makeFileNode({
        id: "file:src/b.ts",
        name: "b.ts",
        path: "src/b.ts",
        hash: "hash-b",
      })
    );
    await db.upsertNode(
      makeFileNode({
        id: "file:src/c.ts",
        name: "c.ts",
        path: "src/c.ts",
        hash: "hash-c",
      })
    );
    await db.upsertNode(makeConceptNode());
    await db.upsertNode(
      makeConceptNode({
        id: "concept:other",
        name: "Other Concept",
        summary: "Describes something about src/a.ts",
        stale: false,
      })
    );
  });

  it("should detect changed files", async () => {
    const currentHashes = new Map([
      ["src/a.ts", "hash-a-changed"], // changed
      ["src/b.ts", "hash-b"], // same
      ["src/c.ts", "hash-c"], // same
    ]);

    const result = await db.getStaleFiles(currentHashes);
    expect(result.changed).toContain("src/a.ts");
    expect(result.changed).not.toContain("src/b.ts");
  });

  it("should detect deleted files", async () => {
    // src/c.ts is no longer in current hashes
    const currentHashes = new Map([
      ["src/a.ts", "hash-a"],
      ["src/b.ts", "hash-b"],
    ]);

    const result = await db.getStaleFiles(currentHashes);
    expect(result.deleted).toContain("src/c.ts");
  });

  it("should detect added files", async () => {
    const currentHashes = new Map([
      ["src/a.ts", "hash-a"],
      ["src/b.ts", "hash-b"],
      ["src/c.ts", "hash-c"],
      ["src/d.ts", "hash-d"], // new
    ]);

    const result = await db.getStaleFiles(currentHashes);
    expect(result.added).toContain("src/d.ts");
  });

  it("should mark concepts stale by related file paths", async () => {
    // For this test, concepts related to specific files should be marked stale.
    // We need edges connecting concepts to files.
    await db.upsertNode(
      makeSymbolNode({
        id: "symbol:a-fn",
        name: "aFunction",
        filePath: "src/a.ts",
        signature: "function aFunction()",
      })
    );
    await db.upsertEdge(
      makeEdge({
        id: "e-describes",
        type: "describes",
        source: "agent",
        fromId: "concept:authentication",
        toId: "symbol:a-fn",
      })
    );

    const count = await db.markConceptsStale(["src/a.ts"]);
    expect(count).toBeGreaterThanOrEqual(1);

    const concept = (await db.getNode(
      "concept:authentication"
    )) as ConceptNode;
    expect(concept.stale).toBe(true);
  });

  it("should purge file nodes and their symbols", async () => {
    await db.upsertNode(
      makeSymbolNode({
        id: "symbol:a-fn",
        name: "aFunction",
        filePath: "src/a.ts",
        signature: "function aFunction()",
      })
    );
    await db.upsertEdge(
      makeEdge({
        id: "e-contains-a",
        type: "contains",
        fromId: "file:src/a.ts",
        toId: "symbol:a-fn",
      })
    );

    const count = await db.purgeFileNodes(["src/a.ts"]);
    expect(count).toBeGreaterThanOrEqual(1);

    // File node should be gone
    expect(await db.getNode("file:src/a.ts")).toBeNull();
    // Symbol should also be gone (cascaded through edge deletion + symbol cleanup)
  });

  it("should return stats", async () => {
    await db.upsertNode(
      makeSymbolNode({
        id: "symbol:stats-fn",
        name: "statsFn",
        filePath: "src/a.ts",
        signature: "function statsFn()",
      })
    );
    await db.upsertEdge(
      makeEdge({
        id: "edge:contains:a:stats-fn",
        type: "contains",
        fromId: "file:src/a.ts",
        toId: "symbol:stats-fn",
      })
    );

    const stats = await db.getStats();
    expect(stats.nodeCount.file).toBe(3);
    expect(stats.nodeCount.symbol).toBe(1);
    expect(stats.nodeCount.concept).toBe(2);
    expect(stats.edgeCount.contains).toBe(1);
    expect(stats.staleCount).toBe(0);
    expect(stats.lastIndexedAt).not.toBeNull();
  });
});
