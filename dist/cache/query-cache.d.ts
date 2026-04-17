/**
 * LRU query result cache with version-tagged, normalized keys.
 *
 * Keys are prefixed with graph version so stale entries become
 * unreachable (not matched) when the graph is re-indexed — no
 * explicit purge needed. LRU eviction handles cleanup over time.
 *
 * Key format: `v{version}:{tool}:{query.trim().toLowerCase()}`
 */
export declare class QueryCache<T> {
    private cache;
    private readonly maxEntries;
    constructor(maxEntries?: number);
    private buildKey;
    get(tool: string, version: string, query: string): T | undefined;
    set(tool: string, version: string, query: string, value: T): void;
    get size(): number;
    clear(): void;
}
