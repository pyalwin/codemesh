import { describe, it, expect } from "vitest";
import { detectLanguageServer } from "./lsp-client.js";

describe("detectLanguageServer", () => {
  it("returns binary+args separately for typescript", () => {
    const result = detectLanguageServer("foo.ts");
    // Environment-dependent — only run assertions when a server is on PATH.
    if (result === null) return;
    expect(result).toHaveProperty("binary");
    expect(result).toHaveProperty("args");
    expect(Array.isArray(result.args)).toBe(true);
  });

  it("returns null for an unsupported extension", () => {
    expect(detectLanguageServer("foo.xyz")).toBe(null);
  });
});
