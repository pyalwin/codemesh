import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseFile } from "./parser.js";
describe("parser — scopePath", () => {
    let tmp;
    beforeEach(() => {
        tmp = mkdtempSync(join(tmpdir(), "codemesh-parse-"));
    });
    afterEach(() => {
        rmSync(tmp, { recursive: true, force: true });
    });
    it("reports empty scopePath for top-level TS symbols", async () => {
        const file = join(tmp, "a.ts");
        writeFileSync(file, "export function topFn() { return 1; }\n");
        const result = await parseFile(file, "a.ts");
        const top = result.symbols.find((s) => s.name === "topFn");
        expect(top).toBeDefined();
        expect(top.scopePath).toEqual([]);
    });
    it("reports the enclosing class in scopePath for TS methods", async () => {
        const file = join(tmp, "b.ts");
        writeFileSync(file, "export class A { foo() { return 1; } }\nexport class B { foo() { return 2; } }\n");
        const result = await parseFile(file, "b.ts");
        const methods = result.symbols.filter((s) => s.kind === "method");
        expect(methods).toHaveLength(2);
        // methods emit the bare name — scopePath provides qualification
        expect(methods.every((m) => m.name === "foo")).toBe(true);
        expect(methods.map((m) => m.scopePath)).toEqual([["A"], ["B"]]);
    });
    it("reports class name in scopePath for Python methods", async () => {
        const file = join(tmp, "c.py");
        writeFileSync(file, "class A:\n    def foo(self): return 1\nclass B:\n    def foo(self): return 2\n");
        const result = await parseFile(file, "c.py");
        const methods = result.symbols.filter((s) => s.kind === "method");
        expect(methods.map((m) => m.name)).toEqual(["foo", "foo"]);
        expect(methods.map((m) => m.scopePath)).toEqual([["A"], ["B"]]);
    });
    it("emits symbols only for named top-level consts, not anonymous expressions", async () => {
        const file = join(tmp, "anon.ts");
        writeFileSync(file, [
            "const namedArrow = (x: number) => x + 1;",
            "const plain = 42;",
            "(() => {})();",
        ].join("\n"));
        const result = await parseFile(file, "anon.ts");
        const names = result.symbols.map((s) => s.name).sort();
        expect(names).toEqual(["namedArrow", "plain"]);
    });
});
describe("parser — scopePath on calls", () => {
    let tmp;
    beforeEach(() => {
        tmp = mkdtempSync(join(tmpdir(), "codemesh-parse-"));
    });
    afterEach(() => {
        rmSync(tmp, { recursive: true, force: true });
    });
    it("records the enclosing class/method in the call's scopePath", async () => {
        const file = join(tmp, "scope.ts");
        writeFileSync(file, [
            "export class A {",
            "  foo() { return 1; }",
            "  bar() { return this.foo(); }",
            "}",
        ].join("\n"));
        const result = await parseFile(file, "scope.ts");
        const fooCall = result.calls.find((c) => c.callee === "this.foo");
        expect(fooCall).toBeDefined();
        // The call happens inside method A.bar — scopePath reflects that.
        expect(fooCall.scopePath).toEqual(["A"]);
    });
});
//# sourceMappingURL=parser.test.js.map