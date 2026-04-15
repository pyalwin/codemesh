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

export class QueryCache<T> {
  private cache = new Map<string, T>();
  private readonly maxEntries: number;

  constructor(maxEntries = DEFAULT_MAX_ENTRIES) {
    this.maxEntries = maxEntries;
  }

  private buildKey(tool: string, version: string, query: string): string {
    return `v${version}:${tool}:${query.trim().toLowerCase()}`;
  }

  get(tool: string, version: string, query: string): T | undefined {
    const key = this.buildKey(tool, version, query);
    if (!this.cache.has(key)) return undefined;
    // Promote to most-recently-used by re-inserting at end
    const value = this.cache.get(key)!;
    this.cache.delete(key);
    this.cache.set(key, value);
    return value;
  }

  set(tool: string, version: string, query: string, value: T): void {
    const key = this.buildKey(tool, version, query);
    if (this.cache.has(key)) {
      this.cache.delete(key);
    } else if (this.cache.size >= this.maxEntries) {
      // Evict least recently used (first key in insertion-order Map)
      this.cache.delete(this.cache.keys().next().value!);
    }
    this.cache.set(key, value);
  }

  get size(): number {
    return this.cache.size;
  }

  clear(): void {
    this.cache.clear();
  }
}
