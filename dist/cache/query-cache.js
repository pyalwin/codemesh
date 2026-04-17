/**
 * LRU query result cache with version-tagged, normalized keys.
 *
 * Keys are prefixed with graph version so stale entries become
 * unreachable (not matched) when the graph is re-indexed — no
 * explicit purge needed. LRU eviction handles cleanup over time.
 *
 * Key format: `v{version}:{tool}:{query.trim().toLowerCase()}`
 */
const DEFAULT_MAX_ENTRIES = 500;
export class QueryCache {
    cache = new Map();
    maxEntries;
    constructor(maxEntries = DEFAULT_MAX_ENTRIES) {
        this.maxEntries = Math.max(1, maxEntries);
    }
    buildKey(tool, version, query) {
        return `v${version}:${tool}:${query.trim().toLowerCase()}`;
    }
    get(tool, version, query) {
        const key = this.buildKey(tool, version, query);
        if (!this.cache.has(key))
            return undefined;
        // Promote to most-recently-used by re-inserting at end
        const value = this.cache.get(key);
        this.cache.delete(key);
        this.cache.set(key, value);
        return value;
    }
    set(tool, version, query, value) {
        const key = this.buildKey(tool, version, query);
        if (this.cache.has(key)) {
            // Existing key: delete before re-inserting at end (size stays same, no eviction needed)
            this.cache.delete(key);
        }
        else if (this.cache.size >= this.maxEntries) {
            // Evict least recently used (first key in insertion-order Map)
            this.cache.delete(this.cache.keys().next().value);
        }
        this.cache.set(key, value);
    }
    get size() {
        return this.cache.size;
    }
    clear() {
        this.cache.clear();
    }
}
//# sourceMappingURL=query-cache.js.map