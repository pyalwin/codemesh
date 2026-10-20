/**
 * codemesh_query — Search the SQLite knowledge graph using FTS5 + trigram.
 *
 * Returns metadata only — signatures, line numbers, file paths.
 * Exact identifiers precede lexical matches, followed by trigram fallbacks.
 * Query relevance ranks each stage; PageRank breaks relevance ties by default.
 * Exact matches can opt into a weak path-depth tie-break before PageRank.
 */
import type { StorageBackend } from "../graph/storage.js";
import type { SearchResult } from "../graph/types.js";
export interface QueryInput {
    query: string;
    scope?: "files" | "symbols" | "workflows" | "all";
    limit?: number;
    /** Project-relative file or directory; directory matches respect path boundaries. */
    path?: string;
    maxBytes?: number;
    /** Optional structural hint for tied exact matches; path depth does not establish canonicality. */
    exactTieBreak?: "pagerank" | "path-depth";
}
interface SlimResult {
    id: string;
    name: string;
    type: string;
    filePath?: string;
    kind?: string;
    signature?: string;
    lineStart?: number;
    lineEnd?: number;
    summary?: string;
    matchedField: string;
    pagerankScore?: number;
}
export interface QueryOutput {
    results: SlimResult[];
    /** Matching candidates after scope/path filtering, before output limits. */
    total: number;
    truncated?: boolean;
    omitted?: number;
    limits?: {
        maxBytes: number;
        limit: number;
    };
}
/** Shared deterministic relevance order for search pages and graph starting points. */
export declare function rankedQuery(storage: StorageBackend, input: Pick<QueryInput, "query" | "scope" | "path" | "exactTieBreak">): Promise<SearchResult[]>;
export declare function handleQuery(storage: StorageBackend, input: QueryInput, _projectRoot?: string): Promise<QueryOutput>;
export {};
