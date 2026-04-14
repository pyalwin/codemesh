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
 * Reset the cached LanceDB connection (useful for tests or re-initialization).
 */
export declare function resetLanceDb(): void;
export declare function indexEmbeddings(projectRoot: string, symbols: Array<{
    id: string;
    name: string;
    signature: string;
    filePath: string;
    summary?: string;
}>): Promise<{
    count: number;
    durationMs: number;
}>;
export declare function semanticSearch(projectRoot: string, query: string, limit?: number): Promise<Array<{
    id: string;
    name: string;
    filePath: string;
    score: number;
}>>;
