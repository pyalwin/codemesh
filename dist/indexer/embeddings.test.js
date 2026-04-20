import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildEmbeddingText, indexEmbeddings, deleteEmbeddings, resetLanceDb, } from "./embeddings.js";
describe("buildEmbeddingText", () => {
    it("includes module path and name+signature", () => {
        const text = buildEmbeddingText({
            name: "handleApproval",
            signature: "(invoice: Invoice): Promise<void>",
            filePath: "src/services/approval.ts",
        });
        expect(text).toContain("[module: src/services/approval.ts]");
        expect(text).toContain("handleApproval (invoice: Invoice): Promise<void>");
    });
    it("includes summary when present", () => {
        const text = buildEmbeddingText({
            name: "handleApproval",
            signature: "(invoice: Invoice): Promise<void>",
            filePath: "src/services/approval.ts",
            summary: "Approves an invoice and notifies stakeholders",
        });
        expect(text).toContain("Approves an invoice and notifies stakeholders");
    });
    it("includes source lines when no summary", () => {
        const text = buildEmbeddingText({
            name: "handleApproval",
            signature: "(invoice: Invoice): Promise<void>",
            filePath: "src/services/approval.ts",
            sourceLines: "const result = await db.approve(invoice.id);",
        });
        expect(text).toContain("const result = await db.approve(invoice.id);");
    });
    it("prefers summary over source lines when both present", () => {
        const text = buildEmbeddingText({
            name: "fn",
            signature: "()",
            filePath: "src/foo.ts",
            summary: "The summary",
            sourceLines: "const x = 1;",
        });
        expect(text).toContain("The summary");
        expect(text).not.toContain("const x = 1;");
    });
    it("handles empty signature gracefully", () => {
        const text = buildEmbeddingText({
            name: "MyClass",
            signature: "",
            filePath: "src/models/my-class.ts",
        });
        expect(text).toContain("[module: src/models/my-class.ts]");
        expect(text).toContain("MyClass");
    });
    it("module path appears before name+signature in output", () => {
        const text = buildEmbeddingText({
            name: "myFn",
            signature: "(x: number): void",
            filePath: "src/utils.ts",
        });
        const moduleIndex = text.indexOf("[module: src/utils.ts]");
        const nameIndex = text.indexOf("myFn (x: number): void");
        expect(moduleIndex).toBeGreaterThanOrEqual(0);
        expect(nameIndex).toBeGreaterThan(moduleIndex);
    });
    it("returns two-line output when no summary or sourceLines", () => {
        const text = buildEmbeddingText({
            name: "bareFunction",
            signature: "(): void",
            filePath: "src/bare.ts",
        });
        const lines = text.split("\n");
        expect(lines).toHaveLength(2);
        expect(lines[0]).toBe("[module: src/bare.ts]");
        expect(lines[1]).toBe("bareFunction (): void");
    });
});
describe("deleteEmbeddings", () => {
    let projectRoot;
    beforeEach(() => {
        projectRoot = mkdtempSync(join(tmpdir(), "codemesh-emb-"));
        resetLanceDb();
    });
    afterEach(() => {
        rmSync(projectRoot, { recursive: true, force: true });
        resetLanceDb();
    });
    it("removes the rows with the given ids and leaves others", async () => {
        await indexEmbeddings(projectRoot, [
            { id: "symbol:a.ts:foo", name: "foo", signature: "()", filePath: "a.ts" },
            { id: "symbol:b.ts:bar", name: "bar", signature: "()", filePath: "b.ts" },
            { id: "symbol:c.ts:baz", name: "baz", signature: "()", filePath: "c.ts" },
        ]);
        const removed = await deleteEmbeddings(projectRoot, [
            "symbol:a.ts:foo",
            "symbol:c.ts:baz",
        ]);
        expect(removed).toBe(2);
        // Sanity: the surviving row is still searchable
        const { semanticSearch } = await import("./embeddings.js");
        const results = await semanticSearch(projectRoot, "bar", 5);
        const ids = results.map((r) => r.id);
        expect(ids).toContain("symbol:b.ts:bar");
        expect(ids).not.toContain("symbol:a.ts:foo");
        expect(ids).not.toContain("symbol:c.ts:baz");
    }, 60_000);
    it("returns 0 when ids list is empty", async () => {
        await indexEmbeddings(projectRoot, [
            { id: "symbol:a.ts:foo", name: "foo", signature: "()", filePath: "a.ts" },
        ]);
        const removed = await deleteEmbeddings(projectRoot, []);
        expect(removed).toBe(0);
    }, 60_000);
    it("returns 0 silently when table does not exist", async () => {
        const removed = await deleteEmbeddings(projectRoot, ["symbol:a.ts:foo"]);
        expect(removed).toBe(0);
    });
});
describe("deleteEmbeddingsByFilePaths", () => {
    let projectRoot;
    beforeEach(() => {
        projectRoot = mkdtempSync(join(tmpdir(), "codemesh-emb-"));
        resetLanceDb();
    });
    afterEach(() => {
        rmSync(projectRoot, { recursive: true, force: true });
        resetLanceDb();
    });
    it("deletes all rows whose filePath is in the list", async () => {
        const { indexEmbeddings, deleteEmbeddingsByFilePaths } = await import("./embeddings.js");
        await indexEmbeddings(projectRoot, [
            { id: "symbol:a.ts:one", name: "one", signature: "()", filePath: "a.ts" },
            { id: "symbol:a.ts:two", name: "two", signature: "()", filePath: "a.ts" },
            { id: "symbol:b.ts:three", name: "three", signature: "()", filePath: "b.ts" },
        ]);
        const removed = await deleteEmbeddingsByFilePaths(projectRoot, ["a.ts"]);
        expect(removed).toBe(2);
    }, 120_000);
});
describe("indexEmbeddings — incremental behaviour", () => {
    let projectRoot;
    beforeEach(() => {
        projectRoot = mkdtempSync(join(tmpdir(), "codemesh-emb-"));
        resetLanceDb();
    });
    afterEach(() => {
        rmSync(projectRoot, { recursive: true, force: true });
        resetLanceDb();
    });
    it("preserves embeddings for symbols not passed in the second call", async () => {
        const { semanticSearch } = await import("./embeddings.js");
        await indexEmbeddings(projectRoot, [
            { id: "symbol:a.ts:alpha", name: "alpha", signature: "()", filePath: "a.ts" },
            { id: "symbol:b.ts:beta", name: "beta", signature: "()", filePath: "b.ts" },
        ]);
        // Simulate incremental re-index of just b.ts
        await indexEmbeddings(projectRoot, [
            { id: "symbol:b.ts:beta", name: "beta", signature: "()", filePath: "b.ts" },
        ]);
        const results = await semanticSearch(projectRoot, "alpha", 5);
        const ids = results.map((r) => r.id);
        expect(ids).toContain("symbol:a.ts:alpha");
    }, 120_000);
    it("updates embeddings for symbols whose text changed", async () => {
        const { semanticSearch } = await import("./embeddings.js");
        await indexEmbeddings(projectRoot, [
            { id: "symbol:a.ts:target", name: "oldName", signature: "()", filePath: "a.ts" },
        ]);
        const beforeResults = await semanticSearch(projectRoot, "newName", 5);
        const beforeTarget = beforeResults.find((r) => r.id === "symbol:a.ts:target");
        await indexEmbeddings(projectRoot, [
            { id: "symbol:a.ts:target", name: "newName", signature: "()", filePath: "a.ts" },
        ]);
        const afterResults = await semanticSearch(projectRoot, "newName", 5);
        const afterTarget = afterResults.find((r) => r.id === "symbol:a.ts:target");
        expect(afterTarget).toBeDefined();
        // Score should improve — `newName` matches the updated row better than the old one.
        if (beforeTarget && afterTarget) {
            expect(afterTarget.score).toBeLessThanOrEqual(beforeTarget.score);
        }
    }, 120_000);
});
//# sourceMappingURL=embeddings.test.js.map