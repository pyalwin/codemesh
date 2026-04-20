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
    /** Run semantic embedding generation. Defaults to true; set to false to skip. */
    withEmbeddings?: boolean;
    withSummaries?: boolean;
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
     * 7. Embeddings (on by default; pass withEmbeddings: false to skip)
     */
    index(options?: IndexOptions): Promise<IndexResult>;
    /**
     * Load .gitignore rules from the project root (if present)
     * and add the always-ignored patterns.
     */
    private loadIgnoreRules;
    /**
     * Recursively walk the directory tree, collecting file paths and their
     * stat-based change keys (mtime + size). This avoids reading file content
     * for the ~90% of files that haven't changed between index runs.
     */
    private walkDirectory;
    /**
     * Resolve a relative import path (e.g., "./math") to an actual
     * project-relative file path, trying common extensions.
     */
    private resolveImportPath;
}
