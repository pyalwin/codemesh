import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  detectLanguageServer,
  getLspClient,
  shutdownAllLspClients,
  __test_reapIdle,
  __test_stopHeartbeat,
} from "./lsp-client.js";

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

describe("LSP idle reaping", () => {
  const origIdleMs = process.env.CODEMESH_LSP_IDLE_MS;

  beforeEach(() => {
    process.env.CODEMESH_LSP_IDLE_MS = "100"; // 100ms for test speed
  });

  afterEach(async () => {
    if (origIdleMs === undefined) delete process.env.CODEMESH_LSP_IDLE_MS;
    else process.env.CODEMESH_LSP_IDLE_MS = origIdleMs;
    __test_stopHeartbeat();
    await shutdownAllLspClients();
  });

  it("removes the client from the cache after idle threshold", async () => {
    const client = await getLspClient("foo.ts", process.cwd());
    if (!client) return; // no LSP installed on this machine — skip

    await new Promise((r) => setTimeout(r, 150));
    await __test_reapIdle();

    const again = await getLspClient("foo.ts", process.cwd());
    if (again) {
      expect(again).not.toBe(client);
    }
  });
});
