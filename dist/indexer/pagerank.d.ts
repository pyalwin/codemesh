/**
 * PageRank scoring via graphology.
 *
 * Builds an in-memory directed graph from SQLite nodes + edges,
 * computes PageRank centrality, and returns scores keyed by node ID.
 * Higher scores indicate more "important" nodes — ones that many
 * other nodes point to (via imports, calls, contains, etc.).
 */
import type { StorageBackend } from "../graph/storage.js";
export declare function computePageRank(storage: StorageBackend): Promise<Map<string, number>>;
