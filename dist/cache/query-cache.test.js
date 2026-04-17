import { describe, it, expect } from "vitest";
import { QueryCache } from "./query-cache.js";
describe("QueryCache", () => {
    it("returns undefined on cold miss", () => {
        const cache = new QueryCache();
        expect(cache.get("answer", "v1", "how does approval work?")).toBeUndefined();
    });
    it("returns value on hit", () => {
        const cache = new QueryCache();
        cache.set("answer", "v1", "how does approval work?", "result");
        expect(cache.get("answer", "v1", "how does approval work?")).toBe("result");
    });
    it("normalizes query — trims whitespace", () => {
        const cache = new QueryCache();
        cache.set("answer", "v1", "  approval  ", "result");
        expect(cache.get("answer", "v1", "approval")).toBe("result");
    });
    it("normalizes query — lowercases", () => {
        const cache = new QueryCache();
        cache.set("answer", "v1", "Approval Flow", "result");
        expect(cache.get("answer", "v1", "approval flow")).toBe("result");
    });
    it("version mismatch is a miss", () => {
        const cache = new QueryCache();
        cache.set("answer", "v1", "approval", "old result");
        expect(cache.get("answer", "v2", "approval")).toBeUndefined();
    });
    it("tool mismatch is a miss", () => {
        const cache = new QueryCache();
        cache.set("answer", "v1", "approval", "result");
        expect(cache.get("map", "v1", "approval")).toBeUndefined();
    });
    it("evicts LRU entry when max size reached", () => {
        const cache = new QueryCache(3);
        cache.set("t", "v1", "a", "A");
        cache.set("t", "v1", "b", "B");
        cache.set("t", "v1", "c", "C");
        // "a" is LRU — adding "d" should evict it
        cache.set("t", "v1", "d", "D");
        expect(cache.get("t", "v1", "a")).toBeUndefined();
        expect(cache.get("t", "v1", "b")).toBe("B");
        expect(cache.get("t", "v1", "c")).toBe("C");
        expect(cache.get("t", "v1", "d")).toBe("D");
    });
    it("a get() promotes entry — prevents it from being evicted", () => {
        const cache = new QueryCache(3);
        cache.set("t", "v1", "a", "A");
        cache.set("t", "v1", "b", "B");
        cache.set("t", "v1", "c", "C");
        // Access "a" — promotes it, so "b" becomes LRU
        cache.get("t", "v1", "a");
        cache.set("t", "v1", "d", "D");
        expect(cache.get("t", "v1", "a")).toBe("A");
        expect(cache.get("t", "v1", "b")).toBeUndefined(); // evicted
    });
    it("size reflects entry count", () => {
        const cache = new QueryCache();
        expect(cache.size).toBe(0);
        cache.set("t", "v1", "a", 1);
        cache.set("t", "v1", "b", 2);
        expect(cache.size).toBe(2);
    });
    it("clear() empties the cache", () => {
        const cache = new QueryCache();
        cache.set("t", "v1", "a", "A");
        cache.clear();
        expect(cache.size).toBe(0);
        expect(cache.get("t", "v1", "a")).toBeUndefined();
    });
    it("updating an existing key replaces value without changing size", () => {
        const cache = new QueryCache();
        cache.set("t", "v1", "a", "A");
        cache.set("t", "v1", "a", "A2");
        expect(cache.get("t", "v1", "a")).toBe("A2");
        expect(cache.size).toBe(1);
    });
    it("maxEntries=0 is clamped to 1 — cache still holds one entry", () => {
        const cache = new QueryCache(0);
        cache.set("t", "v1", "a", "A");
        expect(cache.get("t", "v1", "a")).toBe("A");
        expect(cache.size).toBe(1);
    });
});
//# sourceMappingURL=query-cache.test.js.map