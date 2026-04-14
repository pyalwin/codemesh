/**
 * Shared tree builder for codemesh_map and codemesh_answer.
 *
 * BFS through calls edges with a visited set, no depth limit.
 * Returns a recursive tree with summary/kind/filePath/id/pagerank on each node.
 */
import type { StorageBackend } from "../graph/storage.js";
export interface MapNode {
    symbol: string;
    id: string;
    filePath: string;
    kind: string;
    summary: string | null;
    relationship?: string;
    pagerank: number | null;
    children: MapNode[];
}
export declare function buildMapTree(storage: StorageBackend, startNodeIds: string[]): Promise<{
    nodes: MapNode[];
    totalSymbols: number;
}>;
