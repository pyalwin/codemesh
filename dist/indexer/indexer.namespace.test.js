import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SqliteBackend } from "../graph/sqlite.js";
import { Indexer } from "./indexer.js";
describe("Indexer — namespace-aware IDs", () => {
    let root;
    let backend;
    beforeEach(async () => {
        root = mkdtempSync(join(tmpdir(), "codemesh-ns-"));
        mkdirSync(join(root, ".codemesh"), { recursive: true });
        backend = new SqliteBackend(join(root, ".codemesh", "codemesh.db"));
        await backend.initialize();
    });
    afterEach(async () => {
        await backend.close();
        rmSync(root, { recursive: true, force: true });
    });
    it("creates distinct nodes for two classes with the same method name", async () => {
        writeFileSync(join(root, "file.ts"), [
            "export class A { foo() { return 1; } }",
            "export class B { foo() { return 2; } }",
        ].join("\n"));
        const indexer = new Indexer(backend, root);
        await indexer.index({ withEmbeddings: false });
        const symbols = await backend.queryNodes({ type: "symbol" });
        const ids = symbols.map((s) => s.id).sort();
        expect(ids).toContain("symbol:file.ts:A.foo");
        expect(ids).toContain("symbol:file.ts:B.foo");
        // No ID ending in just `:foo` — qualified IDs only
        expect(ids.filter((id) => id.endsWith(":foo"))).toHaveLength(0);
    });
    it("keeps top-level symbols addressable as symbol:<path>:<name>", async () => {
        writeFileSync(join(root, "top.ts"), "export function topFn() { return 1; }\n");
        const indexer = new Indexer(backend, root);
        await indexer.index({ withEmbeddings: false });
        const top = await backend.getNode("symbol:top.ts:topFn");
        expect(top).not.toBeNull();
    });
    it("disambiguates same-name top-level duplicates with a line suffix", async () => {
        writeFileSync(join(root, "dup.ts"), [
            "function dup() { return 1; }",
            "function dup() { return 2; }",
        ].join("\n"));
        const indexer = new Indexer(backend, root);
        await indexer.index({ withEmbeddings: false });
        const symbols = await backend.queryNodes({ type: "symbol" });
        const dupIds = symbols
            .map((s) => s.id)
            .filter((id) => id.startsWith("symbol:dup.ts:dup"));
        expect(dupIds.length).toBe(2);
        for (const id of dupIds) {
            expect(id).toMatch(/@L\d+$/);
        }
    });
    it("resolves in-class calls to the class's own method, not another class's method", async () => {
        writeFileSync(join(root, "scope.ts"), [
            "export class A {",
            "  foo() { return 1; }",
            "  bar() {",
            "    return this.foo();",
            "  }",
            "}",
            "export class B {",
            "  foo() { return 2; }",
            "}",
        ].join("\n"));
        const indexer = new Indexer(backend, root);
        await indexer.index({ withEmbeddings: false });
        const barId = "symbol:scope.ts:A.bar";
        const edges = await backend.getEdges(barId, "out", ["calls"]);
        const targets = edges.map((e) => e.toId);
        expect(targets).toContain("symbol:scope.ts:A.foo");
        expect(targets).not.toContain("symbol:scope.ts:B.foo");
    });
});
//# sourceMappingURL=indexer.namespace.test.js.map