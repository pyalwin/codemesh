import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseFile } from "./parser.js";

describe("QueryParser — Swift", () => {
  let tmp: string;
  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "codemesh-swift-"));
  });
  afterEach(() => rmSync(tmp, { recursive: true, force: true }));

  it("extracts classes and methods from a Swift file", async () => {
    const file = join(tmp, "Model.swift");
    writeFileSync(
      file,
      [
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
      ].join("\n"),
    );

    const result = await parseFile(file, "Model.swift");
    const classes = result.symbols.filter((s) => s.kind === "class");
    const methods = result.symbols.filter((s) => s.kind === "method");

    expect(classes.map((c) => c.name).sort()).toEqual(["Account", "Ledger"]);
    // Methods emit bare name with scopePath [<className>]
    expect(
      methods
        .map((m) => ({ name: m.name, scope: m.scopePath }))
        .sort((a, b) =>
          (a.scope[0]! + a.name).localeCompare(b.scope[0]! + b.name),
        ),
    ).toEqual([
      { name: "balance", scope: ["Account"] },
      { name: "deposit", scope: ["Account"] },
      { name: "balance", scope: ["Ledger"] },
    ]);
    expect(result.imports).toContain("Foundation");
  });

  it("captures Swift function calls", async () => {
    const file = join(tmp, "call.swift");
    writeFileSync(
      file,
      ['func hello() { print("hi") }', "hello()"].join("\n"),
    );
    const result = await parseFile(file, "call.swift");
    const callees = result.calls.map((c) => c.callee).sort();
    expect(callees).toContain("hello");
    // print may also appear, but require at least `hello`
  });
});
