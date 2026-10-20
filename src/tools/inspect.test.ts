import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { createHash } from "node:crypto";
import { SqliteBackend } from "../graph/sqlite.js";
import { Indexer } from "../indexer/indexer.js";
import { handleInspect } from "./inspect.js";
import { handleSource } from "./source.js";

describe("bounded deterministic inspect", () => {
  let root: string;
  let storage: SqliteBackend;
  const write = (path: string, text: string) => {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), text);
  };
  const index = () => new Indexer(storage, root).index({ withEmbeddings: false });

  beforeEach(async () => {
    root = mkdtempSync(join(tmpdir(), "codemesh-inspect-"));
    storage = new SqliteBackend(join(root, "index.db"));
    await storage.initialize();
  });
  afterEach(async () => {
    await storage.close();
    rmSync(root, { recursive: true, force: true });
  });

  it("returns the root and a proved relative-package module callee, exactly one hop", async () => {
    write("pkg/__init__.py", "");
    write("pkg/_internal/__init__.py", "");
    write("pkg/_internal/_fields.py", "def leaf(x):\n    return x\n\ndef resolve_default_value(x):\n    return leaf(x)\n");
    write("pkg/fields.py", "from ._internal import _fields\n\nclass FieldInfo:\n    def get_default(self, x):\n        return _fields.resolve_default_value(x)\n");
    await index();
    const out = await handleInspect(storage, { id: "FieldInfo.get_default" }, root);
    expect(out.status).toBe("fresh");
    expect(out.sources.map(source => source.id)).toEqual([
      "symbol:pkg/fields.py:FieldInfo.get_default", "symbol:pkg/_internal/_fields.py:resolve_default_value",
    ]);
    expect(out.calls).toEqual([{ sourceId: "symbol:pkg/fields.py:FieldInfo.get_default",
      direction: "outgoing", relation: "calls", neighborId: "symbol:pkg/_internal/_fields.py:resolve_default_value", expression: "_fields.resolve_default_value", line: 5,
      resolution: "imported_module_function", targetId: "symbol:pkg/_internal/_fields.py:resolve_default_value",
      status: "included", sourceIndex: 1 }]);
    for (const source of out.sources) {
      expect(source.mode).toBe("full");
      expect(source.source).toBe(readFileSync(join(root, source.filePath), "utf-8")
        .split("\n").slice(source.lineStart - 1, source.lineEnd).join("\n"));
      expect(source).toEqual(await handleSource(storage, { id: source.id, mode: "full" }, root));
    }
    expect(out.omitted).toEqual({ sources: 0, calls: 0 });
  });

  it("resolves same-file functions, explicit imported functions, and module aliases without duplicate bodies", async () => {
    write("dep.py", "def work(x):\n    return x + 1\n");
    write("main.py", "from dep import work as imported\nimport dep as module\n\ndef helper(x):\n    return x\n\ndef entry(x):\n    return helper(x) + imported(x) + module.work(x)\n");
    await index();
    const out = await handleInspect(storage, { id: "entry" }, root);
    expect(out.sources.map(source => source.symbol)).toEqual(["entry", "helper", "work"]);
    expect(out.calls.map(call => call.resolution)).toEqual(["same_file_function", "imported_function", "imported_module_function"]);
    expect(out.calls.map(call => call.sourceIndex)).toEqual([1, 2, 2]);
  });

  it.each([
    ["parameter", "def entry(dep):\n    return dep.work()\n"],
    ["local assignment", "def entry():\n    dep = object()\n    return dep.work()\n"],
    ["late local assignment", "def entry():\n    dep.work()\n    dep = object()\n"],
    ["module assignment", "dep = object()\n\ndef entry():\n    return dep.work()\n"],
    ["attribute reassignment", "dep.work = lambda: 2\n\ndef entry():\n    return dep.work()\n"],
    ["tuple attribute reassignment", "dep.work, other = (lambda: 2), 0\n\ndef entry():\n    return dep.work()\n"],
    ["global reassignment", "def change():\n    global dep\n    dep = object()\n\ndef entry():\n    return dep.work()\n"],
    ["dynamic attribute", "def entry():\n    return get_module().work()\n"],
    ["closure", "def outer(dep):\n    def entry():\n        return dep.work()\n    return entry\n"],
  ])("does not promote a %s receiver to a proved target", async (_label, body) => {
    write("dep.py", "def work():\n    return 1\n");
    write("main.py", `import dep\n\n${body}`);
    await index();
    const matches = (await storage.queryNodes({ type: "symbol" })).filter(node => node.name === "entry" || node.name.endsWith(".entry"));
    expect(matches).toHaveLength(1);
    const out = await handleInspect(storage, { id: matches[0].id }, root);
    expect(out.sources).toHaveLength(1);
    expect(out.calls.some(call => call.expression.endsWith(".work") && call.status === "unresolved")).toBe(true);
    expect(out.calls.every(call => !call.targetId)).toBe(true);
  });

  it("does not resolve a package attribute that its init defines or a decorated callee", async () => {
    write("pkg/__init__.py", "dep = object()\n");
    write("pkg/dep.py", "def work():\n    return 1\n");
    write("main.py", "from pkg import dep\n\ndef entry():\n    return dep.work()\n");
    await index();
    const out = await handleInspect(storage, { id: "entry" }, root);
    expect(out.sources).toHaveLength(1);
    expect(out.calls[0].reason).toBe("package_attribute_binding_not_proven");
    write("main.py", "@replace\ndef helper():\n    return 1\n\ndef entry():\n    return helper()\n");
    await index();
    const decorated = await handleInspect(storage, { id: "entry" }, root);
    expect(decorated.sources).toHaveLength(1);
    expect(decorated.calls[0].reason).toBe("callee_binding_rebound_decorated_or_conditional");
  });

  it("lists every overload candidate instead of choosing the implementation", async () => {
    write("main.py", "from typing import overload\n\n@overload\ndef helper(x: int): ...\n@overload\ndef helper(x: str): ...\ndef helper(x):\n    return x\n\ndef entry(x):\n    return helper(x)\n");
    await index();
    const out = await handleInspect(storage, { id: "entry" }, root);
    expect(out.sources).toHaveLength(1);
    expect(out.calls[0].status).toBe("ambiguous");
    expect(out.calls[0].candidateIds).toEqual([
      "symbol:main.py:helper@L4", "symbol:main.py:helper@L6", "symbol:main.py:helper@L7",
    ]);
    await expect(handleInspect(storage, { id: "helper" }, root)).rejects.toThrow("Ambiguous symbol name: 3 matches");
  });

  it("checks ancestor package initializers for mutation of a dotted imported module", async () => {
    write("pkg/__init__.py", "from . import api\n\ndef replacement():\n    return 99\n\napi.helper = replacement\n");
    write("pkg/api.py", "def helper():\n    return 1\n");
    write("main.py", "from pkg.api import helper\n\ndef entry():\n    return helper()\n");
    await index();
    const out = await handleInspect(storage, { id: "entry" }, root);
    expect(out.sources).toHaveLength(1);
    expect(out.calls[0]).toMatchObject({ status: "unresolved", reason: "ancestor_package_binding_not_proven" });
  });

  it("does not treat a present unindexed package initializer as an absent namespace initializer", async () => {
    write("pkg/__init__.py", "");
    write("pkg/api.py", "def helper():\n    return 1\n");
    write("main.py", "from pkg.api import helper\n\ndef entry():\n    return helper()\n");
    await index();
    await storage.deleteNode("file:pkg/__init__.py");
    const out = await handleInspect(storage, { id: "entry" }, root);
    expect(out.sources).toHaveLength(1);
    expect(out.calls[0]).toMatchObject({ status: "unresolved", reason: "ancestor_package_binding_not_proven" });
  });

  it.each([
    ["from typing import TYPE_CHECKING", "TYPE_CHECKING"],
    ["from typing import TYPE_CHECKING as TC", "TC"],
    ["import typing as t", "t.TYPE_CHECKING"],
  ])("ignores runtime-dead wildcard imports guarded by %s", async (binding, condition) => {
    write("pkg/__init__.py", `${binding}\n\nif ${condition}:\n    from .unavailable_types import *\n`);
    write("pkg/api.py", "def helper():\n    return 1\n");
    write("main.py", "from pkg.api import helper\n\ndef entry():\n    return helper()\n");
    await index();
    const out = await handleInspect(storage, { id: "entry" }, root);
    expect(out.sources.map(source => source.symbol)).toEqual(["entry", "helper"]);
    expect(out.calls[0].status).toBe("included");
  });

  it.each([
    "from typing import TYPE_CHECKING\nTYPE_CHECKING = True\nif TYPE_CHECKING:\n    from .unknown import *\n",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass\nelse:\n    from .unknown import *\n",
    "import typing\ntyping.TYPE_CHECKING = True\nif typing.TYPE_CHECKING:\n    from .unknown import *\n",
  ])("retains runtime branch guards when the typing constant is rebound or the else branch is dynamic", async body => {
    write("pkg/__init__.py", body);
    write("pkg/api.py", "def helper():\n    return 1\n");
    write("main.py", "from pkg.api import helper\n\ndef entry():\n    return helper()\n");
    await index();
    const out = await handleInspect(storage, { id: "entry" }, root);
    expect(out.sources).toHaveLength(1);
    expect(out.calls[0]).toMatchObject({ status: "unresolved", reason: "ancestor_package_binding_not_proven" });
  });

  it("enforces symbol and full JSON UTF-8 budgets without truncating code or claiming undelivered bytes", async () => {
    const helper = `def helper():\n    return '${"😀".repeat(500)}'`;
    write("main.py", `${helper}\n\ndef entry():\n    return helper()\n`);
    await index();
    const metric = vi.spyOn(storage, "incrementMetric");
    const one = await handleInspect(storage, { id: "entry", maxSymbols: 1 }, root);
    expect(one.sources).toHaveLength(1);
    expect(one.calls[0]).toMatchObject({ status: "omitted", reason: "max_symbols" });
    expect(one.omitted.sources).toBe(1);
    const bounded = await handleInspect(storage, { id: "entry", maxBytes: 1800 }, root);
    expect(Buffer.byteLength(JSON.stringify(bounded))).toBeLessThanOrEqual(1800);
    expect(bounded.sources).toHaveLength(1);
    expect(bounded.calls[0]).toMatchObject({ status: "omitted", reason: "max_bytes" });
    const all = await handleInspect(storage, { id: "entry" }, root);
    expect(all.sources[1].source).toBe(helper);
    const huge = await handleInspect(storage, { id: "helper", maxBytes: 1024 }, root);
    expect(huge).toMatchObject({ status: "budget_exceeded", sources: [], omitted: { sources: 1 } });
    expect(Buffer.byteLength(JSON.stringify(huge))).toBeLessThanOrEqual(1024);
    expect(metric.mock.calls).toEqual([...one.sources, ...bounded.sources, ...all.sources]
      .map(source => ["source_bytes_delivered", Buffer.byteLength(source.source!)]));
  });

  it("depth zero returns only exact root source; graph candidates cannot be auto-included", async () => {
    write("main.ts", "export function helper() { return 1; }\nexport function entry() { return helper(); }\n");
    await index();
    const zero = await handleInspect(storage, { id: "entry", depth: 0 }, root);
    expect(zero.sources).toHaveLength(1);
    expect(zero.calls).toEqual([]);
    const one = await handleInspect(storage, { id: "entry" }, root);
    expect(one.sources).toHaveLength(1);
    expect(one.calls).toHaveLength(1);
    expect(one.calls[0]).toMatchObject({ resolution: "unverified_graph", status: "unresolved" });
  });

  it("never serves stale root or dependency source", async () => {
    write("dep.py", "def helper():\n    return 1\n");
    write("main.py", "from dep import helper\n\ndef entry():\n    return helper()\n");
    await index();
    write("dep.py", "def helper():\n    return 2\n");
    const staleCallee = await handleInspect(storage, { id: "entry" }, root);
    expect(staleCallee.sources).toHaveLength(1);
    expect(staleCallee.calls[0]).toMatchObject({ status: "unresolved", reason: "callee_file_stale_or_unavailable" });
    write("main.py", "def entry():\n    return 3\n");
    const staleRoot = await handleInspect(storage, { id: "entry" }, root);
    expect(staleRoot.status).toBe("stale");
    expect(staleRoot.sources[0].source).toBeNull();
    rmSync(join(root, "main.py"));
    expect((await handleInspect(storage, { id: "entry" }, root)).status).toBe("deleted");
  });

  it("rechecks caller and package proof snapshots after awaited callee reads", async () => {
    write("pkg/__init__.py", "");
    write("pkg/dep.py", "def helper():\n    return 1\n");
    write("main.py", "from pkg import dep\n\ndef entry():\n    return dep.helper()\n");
    await index();
    const getNode = storage.getNode.bind(storage);
    let mutated = false;
    vi.spyOn(storage, "getNode").mockImplementation(async id => {
      if (!mutated && id === "symbol:pkg/dep.py:helper") {
        mutated = true;
        write("pkg/__init__.py", "dep = object()\n");
      }
      return getNode(id);
    });
    const out = await handleInspect(storage, { id: "entry" }, root);
    expect(out.sources).toHaveLength(1);
    expect(out.calls[0]).toMatchObject({ status: "stale", reason: "binding_dependency_changed_during_inspection" });
  });

  it("bounds large call metadata and rejects invalid limits", async () => {
    write("main.py", `def entry():\n${Array.from({ length: 100 }, (_, index) => `    external${index}()`).join("\n")}\n`);
    await index();
    const out = await handleInspect(storage, { id: "entry", maxBytes: 4096 }, root);
    expect(out.omitted.calls).toBeGreaterThan(0);
    expect(out.calls.length + out.omitted.calls).toBe(100);
    expect(Buffer.byteLength(JSON.stringify(out))).toBeLessThanOrEqual(4096);
    for (const input of [{ maxBytes: 100 }, { maxSymbols: 0 }, { depth: 2 }]) {
      await expect(handleInspect(storage, { id: "entry", ...input } as never, root)).rejects.toThrow();
    }
  });

  it("enforces the byte cap after a longer budget_exceeded status, even for a long canonical ID", async () => {
    write("main.py", `def ${"a".repeat(742)}():\n    return 1\n`);
    await index();
    const out = await handleInspect(storage, { id: "a".repeat(742), maxBytes: 1024 }, root);
    expect(out.status).toBe("budget_exceeded");
    expect(Buffer.byteLength(JSON.stringify(out))).toBeLessThanOrEqual(1024);
  });

  it("rejects old body bytes when the file and indexed hash both change after the source read", async () => {
    write("main.py", "def entry():\n    return 1\n");
    await index();
    const getNode = storage.getNode.bind(storage);
    let reads = 0;
    vi.spyOn(storage, "getNode").mockImplementation(async id => {
      const node = await getNode(id);
      if (id === "file:main.py" && ++reads >= 2 && node?.type === "file") {
        const changed = "def entry():\n    return 2\n";
        write("main.py", changed);
        return { ...node, hash: createHash("sha256").update(changed).digest("hex") };
      }
      return node;
    });
    const out = await handleInspect(storage, { id: "entry", depth: 0 }, root);
    expect(out.status).toBe("stale");
    expect(out.sources[0].source).toBeNull();
  });

  it("rechecks after asynchronous metric writes and retracts undelivered source bytes", async () => {
    write("main.py", "def entry():\n    return 1\n");
    await index();
    const increment = storage.incrementMetric.bind(storage);
    vi.spyOn(storage, "incrementMetric").mockImplementation(async (name, value) => {
      await increment(name, value);
      if (value > 0) write("main.py", "def entry():\n    return 2\n");
    });
    const out = await handleInspect(storage, { id: "entry", depth: 0 }, root);
    expect(out.status).toBe("stale");
    expect(out.sources[0].source).toBeNull();
    expect((await storage.getStats()).metrics.source_bytes_delivered).toBe(0);
  });
  const hint = (fromId: string, toId: string) => storage.upsertEdge({
    id: `test:calls:${fromId}:${toId}`, type: "calls", source: "static", fromId, toId,
    createdAt: new Date().toISOString(),
  });

  it("bundles verified callers and callees with true caller-to-callee provenance at one hop", async () => {
    write("pkg/__init__.py", "");
    write("pkg/_internal/__init__.py", "");
    write("pkg/_internal/_fields.py", "def leaf(x):\n    return x\n\ndef resolve_default_value(x):\n    return leaf(x)\n");
    write("pkg/fields.py", "from ._internal import _fields\n\nclass FieldInfo:\n    def get_default(self, x):\n        return _fields.resolve_default_value(x)\n");
    await index();
    const caller = "symbol:pkg/fields.py:FieldInfo.get_default";
    const center = "symbol:pkg/_internal/_fields.py:resolve_default_value";
    const leaf = "symbol:pkg/_internal/_fields.py:leaf";
    await hint(caller, center); // A candidate hint must still pass live binding verification.
    const out = await handleInspect(storage, { id: center, direction: "both" }, root);
    expect(new Set(out.sources.map(source => source.id))).toEqual(new Set([center, caller, leaf]));
    expect(out.calls).toEqual(expect.arrayContaining([
      expect.objectContaining({ sourceId: center, targetId: leaf, direction: "outgoing", status: "included", neighborId: leaf }),
      expect.objectContaining({ sourceId: caller, targetId: center, direction: "incoming", status: "included", neighborId: caller }),
    ]));
    for (const call of out.calls) expect(out.sources[call.sourceIndex!].id).toBe(call.neighborId);
    for (const source of out.sources) expect(source).toEqual(await handleSource(storage, { id: source.id, mode: "full" }, root));
    expect(out.incoming).toMatchObject({ checked: 1, verified: 1, unverified: 0, omitted: 0, complete: false });
  });

  it("does not promote a shadowed parameter or a false same-name graph edge to a verified caller", async () => {
    write("dep.py", "def work():\n    return 1\n");
    write("other.py", "def work():\n    return 2\n");
    write("main.py", "from other import work\n\ndef shadowed(work):\n    return work()\n\ndef wrong_target():\n    return work()\n");
    await index();
    const target = "symbol:dep.py:work";
    await hint("symbol:main.py:shadowed", target);
    await hint("symbol:main.py:wrong_target", target);
    const out = await handleInspect(storage, { id: target, direction: "incoming" }, root);
    expect(out.sources.map(source => source.id)).toEqual([target]);
    expect(out.calls).toHaveLength(2);
    expect(out.calls.every(call => !call.targetId && call.status === "unresolved"
      && call.reason === "no_verified_call_to_target" && call.candidateIds?.[0] === target)).toBe(true);
    expect(out.incoming).toMatchObject({ checked: 2, verified: 0, unverified: 2 });
  });

  it("deduplicates bodies across cycles, repeated calls, and self recursion", async () => {
    write("main.py", "def first():\n    first()\n    second()\n    second()\n\ndef second():\n    return first()\n");
    await index();
    const out = await handleInspect(storage, { id: "first", direction: "both" }, root);
    expect(out.sources.map(source => source.symbol)).toEqual(["first", "second"]);
    expect(out.calls.filter(call => call.direction === "incoming").map(call => call.sourceId))
      .toEqual(["symbol:main.py:first", "symbol:main.py:second"]);
    expect(out.calls.every(call => call.status === "included")).toBe(true);
  });

  it("checks only the caller candidate budget and states incomplete coverage even when all hints are verified", async () => {
    write("main.py", "def target():\n    return 1\n\ndef a():\n    return target()\n\ndef b():\n    return target()\n\ndef c():\n    return target()\n");
    await index();
    const getNode = vi.spyOn(storage, "getNode");
    const out = await handleInspect(storage, { id: "target", direction: "incoming", maxCandidates: 1 }, root);
    expect(out.incoming).toEqual({ discovery: "indexed_call_edges", complete: false,
      candidateCount: 3, checked: 1, verified: 1, unverified: 0, omitted: 2, reason: "candidate_limit" });
    expect(out.sources.map(source => source.symbol)).toEqual(["target", "a"]);
    expect(getNode.mock.calls.some(([id]) => id === "symbol:main.py:b" || id === "symbol:main.py:c")).toBe(false);
    const all = await handleInspect(storage, { id: "target", direction: "incoming" }, root);
    expect(all.incoming).toMatchObject({ complete: false, omitted: 0, reason: "graph_may_miss_callers" });
    for (const input of [{ maxCandidates: 0 }, { maxCandidates: 129 }, { direction: "up" }]) {
      await expect(handleInspect(storage, { id: "target", ...input } as never, root)).rejects.toThrow();
    }
  });

  it("keeps caller bodies whole and preserves incoming endpoints under byte and symbol limits", async () => {
    write("main.py", `def target():\n    return 1\n\ndef caller():\n    unused = '${"😀".repeat(500)}'\n    return target()\n`);
    await index();
    const metric = vi.spyOn(storage, "incrementMetric");
    const small = await handleInspect(storage, { id: "target", direction: "incoming", maxBytes: 2000 }, root);
    expect(Buffer.byteLength(JSON.stringify(small))).toBeLessThanOrEqual(2000);
    expect(small.sources.map(source => source.symbol)).toEqual(["target"]);
    expect(small.calls[0]).toMatchObject({ sourceId: "symbol:main.py:caller", targetId: "symbol:main.py:target",
      neighborId: "symbol:main.py:caller", status: "omitted", reason: "max_bytes" });
    const one = await handleInspect(storage, { id: "target", direction: "incoming", maxSymbols: 1 }, root);
    expect(one.calls[0]).toMatchObject({ status: "omitted", reason: "max_symbols" });
    expect(metric.mock.calls).toEqual([...small.sources, ...one.sources]
      .map(source => ["source_bytes_delivered", Buffer.byteLength(source.source!)]));
  });

  it("does not claim missing graph callers were searched or that an empty incoming set is complete", async () => {
    write("dep.py", "def target():\n    return 1\n");
    write("main.py", "from dep import target as alias\n\ndef caller():\n    return alias()\n");
    await index();
    const target = "symbol:dep.py:target";
    const out = await handleInspect(storage, { id: target, direction: "incoming" }, root);
    expect(out.sources.map(source => source.id)).toEqual([target]);
    expect(out.incoming).toMatchObject({ discovery: "indexed_call_edges", complete: false, checked: 0,
      reason: "graph_may_miss_callers" });
    await hint("symbol:main.py:caller", target);
    const withHint = await handleInspect(storage, { id: target, direction: "incoming" }, root);
    expect(withHint.sources.map(source => source.symbol)).toEqual(["target", "caller"]);
  });

  it("marks stale callers unverified and performs no incoming lookups at depth zero", async () => {
    write("dep.py", "def target():\n    return 1\n");
    write("main.py", "from dep import target\n\ndef caller():\n    return target()\n");
    await index();
    write("main.py", "def caller():\n    return 7\n");
    const stale = await handleInspect(storage, { id: "target", direction: "incoming" }, root);
    expect(stale.sources.map(source => source.symbol)).toEqual(["target"]);
    expect(stale.calls[0]).toMatchObject({ status: "stale", reason: "caller_source_not_current" });
    expect(stale.incoming).toMatchObject({ verified: 0, unverified: 1 });
    const edges = vi.spyOn(storage, "getEdges");
    const zero = await handleInspect(storage, { id: "target", direction: "both", depth: 0 }, root);
    expect(zero.calls).toEqual([]);
    expect(zero.incoming).toBeUndefined();
    expect(edges).not.toHaveBeenCalled();
  });

  it("retracts caller source and verified coverage when a package proof changes during metric writes", async () => {
    write("pkg/__init__.py", "");
    write("pkg/dep.py", "def target():\n    return 1\n");
    write("main.py", "from pkg.dep import target\n\ndef caller():\n    return target()\n");
    await index();
    const increment = storage.incrementMetric.bind(storage);
    vi.spyOn(storage, "incrementMetric").mockImplementation(async (name, value) => {
      await increment(name, value);
      if (value > 0) write("pkg/__init__.py", "dep = object()\n");
    });
    const out = await handleInspect(storage, { id: "target", direction: "incoming" }, root);
    expect(out.sources.map(source => source.symbol)).toEqual(["target"]);
    expect(out.calls[0]).toMatchObject({ status: "stale", reason: "binding_dependency_changed_during_inspection" });
    expect(out.calls[0].sourceIndex).toBeUndefined();
    expect(out.incoming).toMatchObject({ verified: 0, unverified: 1 });
    expect((await storage.getStats()).metrics.source_bytes_delivered).toBe(Buffer.byteLength(out.sources[0].source!));
  });

  it("invalidates omitted caller proofs when dependencies change during metrics", async () => {
    write("pkg/__init__.py", "");
    write("pkg/dep.py", "def target():\n    return 1\n");
    write("main.py", "from pkg.dep import target\n\ndef caller():\n    return target()\n");
    await index();
    const increment = storage.incrementMetric.bind(storage);
    vi.spyOn(storage, "incrementMetric").mockImplementation(async (name, value) => {
      await increment(name, value);
      if (value > 0) write("pkg/__init__.py", "dep = object()\n");
    });
    const out = await handleInspect(storage, { id: "target", direction: "incoming", maxSymbols: 1 }, root);
    expect(out.sources.map(source => source.symbol)).toEqual(["target"]);
    expect(out.calls[0]).toMatchObject({ status: "stale", reason: "binding_dependency_changed_during_inspection" });
    expect(out.incoming).toMatchObject({ verified: 0, unverified: 1 });
  });

});
