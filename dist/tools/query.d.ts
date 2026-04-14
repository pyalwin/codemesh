/**
 * codemesh_query — Search the knowledge graph using FTS5 + trigram + semantic search.
 *
 * Returns metadata only — signatures, line numbers, file paths.
 * Results ranked by PageRank when available.
 * Semantic search (LanceDB) supplements FTS5 when embeddings exist.
 */
import type { StorageBackend } from "../graph/storage.js";
export interface QueryInput {
    query: string;
    scope?: "files" | "symbols" | "workflows" | "all";
}
interface SlimResult {
    id: string;
    name: string;
    type: string;
    filePath?: string;
    kind?: string;
    summary?: string;
    matchedField: string;
    pagerankScore?: number;
}
export interface QueryOutput {
    results: SlimResult[];
    semanticResults?: Array<{
        id: string;
        name: string;
        filePath: string;
        score: number;
    }>;
    total: number;
}
export declare function handleQuery(storage: StorageBackend, input: QueryInput, projectRoot?: string): Promise<QueryOutput>;
export {};
