import { describe, it, expect } from "vitest";
import { buildEmbeddingText } from "./embeddings.js";

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
});
