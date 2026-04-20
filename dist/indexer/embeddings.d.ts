/**
 * LanceDB semantic search with local HuggingFace transformer embeddings.
 *
 * At index time (opt-in via --with-embeddings), generates embeddings for
 * symbol names + signatures using a local model. Stores in LanceDB.
 * At query time, does semantic vector search alongside FTS5.
 *
 * Zero API cost — all inference runs locally.
 */
export declare function generateEmbedding(text: string): Promise<number[]>;
/**
 * Build the text to embed for a symbol node.
 * Richer text → better semantic alignment with natural-language queries.
 *
 * Format: [module: {filePath}]\n{name} {signature}\n{summary OR sourceLines}
 * Summary is preferred over source because it's denser signal.
 */
export declare function buildEmbeddingText(sym: {
    name: string;
    signature: string;
    filePath: string;
    summary?: string;
    sourceLines?: string;
}): string;
/**
 * Reset the cached LanceDB connection (useful for tests or re-initialization).
 */
export declare function resetLanceDb(): void;
/**
 * Reset the cached embedder (useful for tests or model switching).
 */
export declare function resetEmbedder(): void;
/**
 * Stream-index symbol embeddings into LanceDB.
 *
 * Inference is serial per-call (one tensor set in flight at a time); `batchSize`
 * only controls the SQLite-write batch size and the disk-I/O fan-out for reading
 * source lines, not the inference fan-out. This caps peak RSS independent of
 * batchSize.
 */
export declare function indexEmbeddings(projectRoot: string, symbols: Array<{
    id: string;
    name: string;
    signature: string;
    filePath: string;
    lineStart?: number;
    lineEnd?: number;
    summary?: string;
}>, options?: {
    batchSize?: number;
    onBatch?: (completed: number, total: number) => void;
}): Promise<{
    count: number;
    durationMs: number;
}>;
export declare function semanticSearch(projectRoot: string, query: string, limit?: number): Promise<Array<{
    id: string;
    name: string;
    filePath: string;
    score: number;
}>>;
/**
 * Delete embedding rows whose id matches any value in `ids`.
 * Returns the number of rows deleted. If the table does not exist
 * (e.g., embeddings never enabled for this project), returns 0 silently.
 * Throws if the table exists but the delete itself fails (bad predicate / IO).
 *
 * Inputs are chunked to keep predicate strings bounded — LanceDB's SQL layer
 * degrades on very large IN lists.
 */
export declare function deleteEmbeddings(projectRoot: string, ids: string[]): Promise<number>;
/**
 * Delete all embedding rows whose filePath matches one of the given paths.
 * Used by the indexer when a file is changed or deleted.
 *
 * Chunks input to keep predicate strings bounded — LanceDB's SQL layer
 * degrades on very large IN lists.
 * Throws if the table exists but the delete itself fails (bad predicate / IO).
 */
export declare function deleteEmbeddingsByFilePaths(projectRoot: string, filePaths: string[]): Promise<number>;
