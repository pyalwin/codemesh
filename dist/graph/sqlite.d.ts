import type { GraphNode, GraphEdge, NodeFilter, SearchResult, TraversalResult } from "./types.js";
import type { StorageBackend } from "./storage.js";
/**
 * SQLite storage backend for the code knowledge graph.
 * Uses better-sqlite3 (synchronous API) with WAL mode and FTS5 full-text search.
 */
export declare class SqliteBackend implements StorageBackend {
    private db;
    private readonly dbPath;
    constructor(dbPath: string);
    initialize(): Promise<void>;
    close(): Promise<void>;
    private getDb;
    private createSchema;
    /**
     * Serialize a GraphNode into table columns.
     * Common fields go into dedicated columns; the rest into a JSON `data` blob.
     */
    private serializeNode;
    /**
     * Reconstruct a typed GraphNode from a database row.
     */
    private deserializeNode;
    upsertNode(node: GraphNode): Promise<string>;
    getNode(id: string): Promise<GraphNode | null>;
    queryNodes(filter: NodeFilter): Promise<GraphNode[]>;
    queryNodesByFilePaths(filePaths: string[]): Promise<GraphNode[]>;
    deleteNode(id: string): Promise<void>;
    upsertEdge(edge: GraphEdge): Promise<string>;
    getEdges(nodeId: string, direction: "in" | "out" | "both", edgeTypes?: string[]): Promise<GraphEdge[]>;
    deleteEdgesByNode(nodeId: string): Promise<number>;
    private deserializeEdge;
    traverse(startId: string, depth: number, edgeTypes?: string[]): Promise<TraversalResult[]>;
    search(query: string, _scope?: string): Promise<SearchResult[]>;
    /**
     * Search using trigram index for partial/substring symbol matching.
     * Finds nodes whose name or signature contains the query as a substring.
     */
    searchTrigrams(query: string): Promise<SearchResult[]>;
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
