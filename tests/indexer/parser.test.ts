import { describe, it, expect } from "vitest";
import { resolve } from "node:path";
import { parseFile } from "../../src/indexer/parser.js";
import {
  getLanguageConfig,
  getSupportedExtensions,
} from "../../src/indexer/languages.js";

// ── Helpers ────────────────────────────────────────────────────────

const FIXTURES = resolve(__dirname, "../fixtures");
const TS_DIR = resolve(FIXTURES, "sample-project/src");
const PY_DIR = resolve(FIXTURES, "python-project");

// ── Language registry tests ────────────────────────────────────────

describe("languages", () => {
  it("returns config for known TypeScript extensions", () => {
    expect(getLanguageConfig("foo.ts")).not.toBeNull();
    expect(getLanguageConfig("foo.tsx")).not.toBeNull();
    expect(getLanguageConfig("foo.ts")!.name).toBe("typescript");
  });

  it("returns config for known Python extensions", () => {
    expect(getLanguageConfig("foo.py")).not.toBeNull();
    expect(getLanguageConfig("foo.py")!.name).toBe("python");
  });

  it("returns null for unsupported extensions", () => {
    expect(getLanguageConfig("foo.md")).toBeNull();
    expect(getLanguageConfig("foo.json")).toBeNull();
    expect(getLanguageConfig("foo.txt")).toBeNull();
  });

  it("lists supported extensions", () => {
    const exts = getSupportedExtensions();
    expect(exts).toContain(".ts");
    expect(exts).toContain(".py");
    expect(exts).toContain(".js");
    expect(exts).toContain(".go");
    expect(exts).toContain(".rs");
    expect(exts).toContain(".java");
  });
});

// ── TypeScript parsing tests ───────────────────────────────────────

describe("parseFile — TypeScript", () => {
  it("extracts functions from a TypeScript file", async () => {
    const result = await parseFile(
      resolve(TS_DIR, "math.ts"),
      "src/math.ts",
    );

    const names = result.symbols.map((s) => s.name);
    expect(names).toContain("add");
    expect(names).toContain("multiply");
    expect(names).toContain("MathHelper");

    // Check the 'add' function symbol
    const addSymbol = result.symbols.find((s) => s.name === "add");
    expect(addSymbol).toBeDefined();
    expect(addSymbol!.kind).toBe("function");
    expect(addSymbol!.lineStart).toBeGreaterThan(0);
    expect(addSymbol!.signature).toContain("add");

    // Check the class
    const classSymbol = result.symbols.find((s) => s.name === "MathHelper");
    expect(classSymbol).toBeDefined();
    expect(classSymbol!.kind).toBe("class");
  });

  it("extracts methods inside a class", async () => {
    const result = await parseFile(
      resolve(TS_DIR, "math.ts"),
      "src/math.ts",
    );

    const methodSymbol = result.symbols.find(
      (s) => s.name === "MathHelper.square",
    );
    expect(methodSymbol).toBeDefined();
    expect(methodSymbol!.kind).toBe("method");
  });

  it("extracts imports from a TypeScript file", async () => {
    const result = await parseFile(
      resolve(TS_DIR, "calculator.ts"),
      "src/calculator.ts",
    );

    expect(result.imports).toContain("./math");
  });

  it("extracts call relationships from TypeScript", async () => {
    const result = await parseFile(
      resolve(TS_DIR, "calculator.ts"),
      "src/calculator.ts",
    );

    const calleeNames = result.calls.map((c) => c.callee);
    expect(calleeNames).toContain("add");
    expect(calleeNames).toContain("MathHelper.square");
  });

  it("extracts call with line numbers", async () => {
    const result = await parseFile(
      resolve(TS_DIR, "math.ts"),
      "src/math.ts",
    );

    const multiplyCall = result.calls.find((c) => c.callee === "multiply");
    expect(multiplyCall).toBeDefined();
    expect(multiplyCall!.lineNumber).toBeGreaterThan(0);
  });

  it("extracts new expressions as calls", async () => {
    const result = await parseFile(
      resolve(TS_DIR, "index.ts"),
      "src/index.ts",
    );

    const calleeNames = result.calls.map((c) => c.callee);
    expect(calleeNames).toContain("new Calculator");
  });

  it("extracts imports from index.ts", async () => {
    const result = await parseFile(
      resolve(TS_DIR, "index.ts"),
      "src/index.ts",
    );

    expect(result.imports).toContain("./calculator");
  });
});

// ── Python parsing tests ───────────────────────────────────────────

describe("parseFile — Python", () => {
  it("extracts functions from a Python file", async () => {
    const result = await parseFile(
      resolve(PY_DIR, "utils.py"),
      "utils.py",
    );

    const names = result.symbols.map((s) => s.name);
    expect(names).toContain("format_currency");
    expect(names).toContain("Logger");

    const funcSymbol = result.symbols.find(
      (s) => s.name === "format_currency",
    );
    expect(funcSymbol).toBeDefined();
    expect(funcSymbol!.kind).toBe("function");

    const classSymbol = result.symbols.find((s) => s.name === "Logger");
    expect(classSymbol).toBeDefined();
    expect(classSymbol!.kind).toBe("class");
  });

  it("extracts methods inside Python classes", async () => {
    const result = await parseFile(
      resolve(PY_DIR, "utils.py"),
      "utils.py",
    );

    const methodNames = result.symbols
      .filter((s) => s.kind === "method")
      .map((s) => s.name);
    expect(methodNames).toContain("Logger.__init__");
    expect(methodNames).toContain("Logger.log");
  });

  it("extracts imports from a Python file", async () => {
    const result = await parseFile(
      resolve(PY_DIR, "app.py"),
      "app.py",
    );

    expect(result.imports).toContain("utils");
  });

  it("extracts call relationships from Python", async () => {
    const result = await parseFile(
      resolve(PY_DIR, "app.py"),
      "app.py",
    );

    const calleeNames = result.calls.map((c) => c.callee);
    expect(calleeNames).toContain("Logger");
    expect(calleeNames).toContain("logger.log");
    expect(calleeNames).toContain("format_currency");
  });
});

// ── Unsupported file types ─────────────────────────────────────────

describe("parseFile — unsupported", () => {
  it("returns empty result for .md files", async () => {
    const result = await parseFile("/fake/path/readme.md", "readme.md");
    expect(result.symbols).toHaveLength(0);
    expect(result.imports).toHaveLength(0);
    expect(result.calls).toHaveLength(0);
  });

  it("returns empty result for .json files", async () => {
    const result = await parseFile("/fake/path/data.json", "data.json");
    expect(result.symbols).toHaveLength(0);
    expect(result.imports).toHaveLength(0);
    expect(result.calls).toHaveLength(0);
  });
});
