import { describe, expect, it, vi } from "vitest";
import type { StorageBackend } from "../graph/storage.js";
import { handleMap } from "./map.js";

describe("handleMap navigation dispatch", () => {
  const storage = {
    queryFilePaths: vi.fn(async () => ["src/a.ts", "src/b.ts", "test.ts"]),
    queryNodesByFilePaths: vi.fn(async () => []),
  } as unknown as StorageBackend;

  it("opens repository root without a query and pages one hierarchy level", async () => {
    const first = await handleMap(storage, { limit: 1 }, "/repo");
    expect(first).toEqual({
      node: { id: "repo", name: "repo", type: "repo" },
      entries: [{ id: "dir:src", name: "src", type: "dir", path: "src", hasChildren: true }],
      nextCursor: "1",
    });
    const second = await handleMap(storage, { limit: 1, cursor: "1" }, "/repo");
    expect(second).toMatchObject({ entries: [{ id: "file:test.ts" }] });
  });

  it("rejects mixed legacy search and deterministic navigation", async () => {
    await expect(handleMap(storage, { query: "foo", node: "repo" }, "/repo"))
      .rejects.toThrow("cannot be combined");
  });

  it("rejects an empty legacy query", async () => {
    await expect(handleMap(storage, { query: "  " }, "/repo"))
      .rejects.toThrow("query must not be empty");
  });
});
