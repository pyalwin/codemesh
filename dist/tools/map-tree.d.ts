/**
 * Shared tree builder for codemesh_map and codemesh_answer.
 *
 * Bounded traversal through calls edges with a visited set.
 * Returns a recursive tree with summary/kind/filePath/id/pagerank on each node.
 */
import type { StorageBackend } from "../graph/storage.js";
export interface MapNode {
    symbol: string;
    id: string;
    filePath: string;
    kind: string;
    signature?: string;
    lineStart?: number;
    lineEnd?: number;
    summary: string | null;
    relationship?: string;
    pagerank: number | null;
    children: MapNode[];
    /** Immediate (depth-1) callers — flat list, not recursive. Capped at 8. */
    calledBy?: Array<{
        symbol: string;
        id: string;
        filePath: string;
    }>;
}
export declare function buildMapTree(storage: StorageBackend, startNodeIds: string[]): Promise<{
    nodes: MapNode[];
    totalSymbols: number;
    truncated: boolean;
}>;
