import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseFile } from "./parser.js";
describe("QueryParser — Swift", () => {
    let tmp;
    beforeEach(() => {
        tmp = mkdtempSync(join(tmpdir(), "codemesh-swift-"));
    });
    afterEach(() => rmSync(tmp, { recursive: true, force: true }));
    it("extracts classes and methods from a Swift file", async () => {
        const file = join(tmp, "Model.swift");
        writeFileSync(file, [
            "import Foundation",
            "",
            "class Account {",
            "    func balance() -> Int { return 0 }",
            "    func deposit(_ amount: Int) { }",
            "}",
            "",
            "class Ledger {",
            "    func balance() -> Int { return 1 }",
            "}",
        ].join("\n"));
        const result = await parseFile(file, "Model.swift");
        const classes = result.symbols.filter((s) => s.kind === "class");
        const methods = result.symbols.filter((s) => s.kind === "method");
        expect(classes.map((c) => c.name).sort()).toEqual(["Account", "Ledger"]);
        // Methods emit bare name with scopePath [<className>]
        expect(methods
            .map((m) => ({ name: m.name, scope: m.scopePath }))
            .sort((a, b) => (a.scope[0] + a.name).localeCompare(b.scope[0] + b.name))).toEqual([
            { name: "balance", scope: ["Account"] },
            { name: "deposit", scope: ["Account"] },
            { name: "balance", scope: ["Ledger"] },
        ]);
        expect(result.imports).toContain("Foundation");
    });
    it("captures Swift function calls", async () => {
        const file = join(tmp, "call.swift");
        writeFileSync(file, ['func hello() { print("hi") }', "hello()"].join("\n"));
        const result = await parseFile(file, "call.swift");
        const callees = result.calls.map((c) => c.callee).sort();
        expect(callees).toContain("hello");
        // print may also appear, but require at least `hello`
    });
});
describe("QueryParser — Go", () => {
    let tmp;
    beforeEach(() => { tmp = mkdtempSync(join(tmpdir(), "codemesh-go-")); });
    afterEach(() => rmSync(tmp, { recursive: true, force: true }));
    it("extracts structs, methods, functions, and imports", async () => {
        const file = join(tmp, "main.go");
        writeFileSync(file, [
            "package main",
            "",
            "import (",
            "    \"fmt\"",
            "    \"strings\"",
            ")",
            "",
            "type Account struct { Balance int }",
            "",
            "func (a *Account) Deposit(amount int) { a.Balance += amount }",
            "",
            "func greet(name string) { fmt.Println(\"hi \" + name) }",
            "",
            "func main() { greet(strings.ToUpper(\"world\")) }",
        ].join("\n"));
        const result = await parseFile(file, "main.go");
        const kinds = result.symbols.map((s) => `${s.kind}:${s.name}`).sort();
        expect(kinds).toContain("class:Account"); // struct mapped to class
        expect(kinds).toContain("method:Deposit");
        expect(kinds).toContain("function:greet");
        expect(kinds).toContain("function:main");
        expect(result.imports).toContain("fmt");
        expect(result.imports).toContain("strings");
        const callees = result.calls.map((c) => c.callee);
        expect(callees).toContain("greet");
    });
});
describe("QueryParser — Rust", () => {
    let tmp;
    beforeEach(() => { tmp = mkdtempSync(join(tmpdir(), "codemesh-rust-")); });
    afterEach(() => rmSync(tmp, { recursive: true, force: true }));
    it("extracts structs, impls, functions, traits, and uses", async () => {
        const file = join(tmp, "lib.rs");
        writeFileSync(file, [
            "use std::collections::HashMap;",
            "",
            "pub struct Account { balance: i64 }",
            "",
            "impl Account {",
            "    pub fn deposit(&mut self, amount: i64) { self.balance += amount; }",
            "    pub fn balance(&self) -> i64 { self.balance }",
            "}",
            "",
            "pub trait Greet { fn hello(&self); }",
            "",
            "pub fn main_entry() { let a = Account { balance: 0 }; a.balance(); }",
        ].join("\n"));
        const result = await parseFile(file, "lib.rs");
        const kinds = result.symbols.map((s) => `${s.kind}:${s.name}`).sort();
        expect(kinds).toContain("class:Account"); // struct => class
        expect(kinds).toContain("method:deposit");
        expect(kinds).toContain("method:balance");
        expect(kinds).toContain("interface:Greet"); // trait => interface
        expect(kinds).toContain("function:main_entry");
        // use std::collections::HashMap — capture at least `std` as the top-level module.
        const joined = result.imports.join("\n");
        expect(joined).toMatch(/std/);
    });
});
//# sourceMappingURL=query-parser.test.js.map