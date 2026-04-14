import type { GraphNode, GraphEdge, NodeFilter, SearchResult, TraversalResult } from "./types.js";
export interface StorageBackend {
    initialize(): Promise<void>;
    close(): Promise<void>;
    upsertNode(node: GraphNode): Promise<string>;
    getNode(id: string): Promise<GraphNode | null>;
    queryNodes(filter: NodeFilter): Promise<GraphNode[]>;
    deleteNode(id: string): Promise<void>;
    upsertEdge(edge: GraphEdge): Promise<string>;
    getEdges(nodeId: string, direction: "in" | "out" | "both", edgeTypes?: string[]): Promise<GraphEdge[]>;
    deleteEdgesByNode(nodeId: string): Promise<number>;
    traverse(startId: string, depth: number, edgeTypes?: string[]): Promise<TraversalResult[]>;
    search(query: string, scope?: string): Promise<SearchResult[]>;
    beginTransaction(): Promise<void>;
    commitTransaction(): Promise<void>;
    rollbackTransaction(): Promise<void>;
    getStaleFiles(currentHashes: Map<string, string>): Promise<{
        changed: string[];
        deleted: string[];
        added: string[];
    }>;
    markConceptsStale(filePaths: string[]): Promise<number>;
    purgeFileNodes(filePaths: string[]): Promise<number>;
    getStats(): Promise<{
        nodeCount: Record<string, number>;
        edgeCount: Record<string, number>;
        staleCount: number;
        lastIndexedAt: string | null;
    }>;
}
