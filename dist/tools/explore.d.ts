/**
 * codemesh_explore — The "mega-tool". Takes a task description, searches the graph,
 * traverses ALL connected nodes to completion, and returns full source code for
 * every symbol in the connected subgraph. One call, complete picture.
 */
import type { StorageBackend } from "../graph/storage.js";
export interface ExploreInput {
    task: string;
    includeSource?: boolean;
    maxDepth?: number;
    maxSymbols?: number;
}
export interface ExploreSymbol {
    name: string;
    kind: string;
    filePath: string;
    absolutePath: string;
    lineStart: number;
    lineEnd: number;
    signature: string;
    source: string | null;
    calls: string[];
    calledBy: string[];
}
export interface ExploreFile {
    path: string;
    absolutePath: string;
    symbols: string[];
    imports: string[];
    importedBy: string[];
    concepts: string[];
}
export interface ExploreOutput {
    task: string;
    entryPoints: string[];
    symbols: ExploreSymbol[];
    files: ExploreFile[];
    workflows: Array<{
        name: string;
        description: string;
        files: string[];
    }>;
    stats: {
        symbolCount: number;
        fileCount: number;
        traversalDepth: number;
        searchHits: number;
    };
}
export declare function handleExplore(storage: StorageBackend, input: ExploreInput, projectRoot: string): Promise<ExploreOutput>;
