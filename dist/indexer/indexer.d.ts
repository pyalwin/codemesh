/**
 * Indexing pipeline that walks a project directory, parses each file
 * with tree-sitter, and stores the results in the graph database.
 */
import type { StorageBackend } from "../graph/storage.js";
export interface IndexResult {
    filesIndexed: number;
    symbolsFound: number;
    edgesCreated: number;
    filesDeleted: number;
    durationMs: number;
    pagerankScore?: {
        computed: number;
        topNodes: Array<{
            id: string;
            score: number;
        }>;
    };
    embeddings?: {
        count: number;
        durationMs: number;
    };
    summaries?: {
        generated: number;
        skipped: number;
    };
}
export interface IndexProgress {
    phase: "scan" | "purge" | "parse" | "resolve" | "git" | "pagerank" | "summaries" | "embeddings";
    completed: number;
    total: number;
    /**
     * Elapsed ms since the phase started. For per-batch phases like
     * `parse` and `embeddings` this is cumulative across batches, not
     * per-batch — i.e. it monotonically grows over the course of the phase.
     */
    elapsedMs: number;
}
export interface IndexOptions {
    /** Legacy opt-in semantic embeddings; the SQLite-only path is the default. */
    withEmbeddings?: boolean;
    withSummaries?: boolean;
    /** Reparse every tracked file without deleting agent knowledge. */
    force?: boolean;
    /**
     * Optional reporter invoked at phase boundaries so callers can surface
     * progress on long-running index runs. Exceptions thrown by the reporter
     * are caught and logged to stderr — they will not abort the index.
     */
    onProgress?: (p: IndexProgress) => void;
}
export declare class Indexer {
    private storage;
    private projectRoot;
    constructor(storage: StorageBackend, projectRoot: string);
    /**
     * Run the indexing pipeline:
     * 1. Walk project files (respecting ignores)
     * 2. Compute hashes and identify changed/new/deleted files
     * 3. Parse changed/new files and store in graph
     * 4. Remove deleted file nodes
     * 5. Git intelligence
     * 6. PageRank scoring
     * 7. Legacy embeddings only when explicitly requested
     */
    index(options?: IndexOptions): Promise<IndexResult>;
    /**
     * Load .gitignore rules from the project root (if present)
     * and add the always-ignored patterns.
     */
    private loadIgnoreRules;
    /**
     * Recursively walk the directory tree and hash supported source files.
     * Content hashes detect changes even when mtime and size are unchanged.
     */
    private walkDirectory;
    /**
     * Resolve a relative import path (e.g., "./math") to an actual
     * project-relative file path, trying common extensions.
     */
    private resolveImportPath;
}
