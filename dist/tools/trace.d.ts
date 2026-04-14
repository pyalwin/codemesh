/**
 * codemesh_trace — Trace a call chain from a symbol and return source code.
 */
import type { StorageBackend } from "../graph/storage.js";
import type { GraphNode } from "../graph/types.js";
/**
 * Multi-strategy symbol finder.
 * Handles: exact name, Class.method splitting, fuzzy FTS search.
 * Returns matching nodes, best match first.
 */
export declare function findSymbol(storage: StorageBackend, query: string): Promise<GraphNode[]>;
export interface TraceInput {
    symbol: string;
    depth?: number;
    compact?: boolean;
}
export interface TraceStep {
    symbol: string;
    filePath: string;
    kind: string;
    signature: string;
    source: string | null;
    lines?: string;
    calls: string[];
    pagerankScore?: number;
}
export interface TraceOutput {
    startSymbol: string;
    steps: TraceStep[];
    depth: number;
}
export declare function handleTrace(storage: StorageBackend, input: TraceInput, projectRoot: string): Promise<TraceOutput>;
