/**
 * codemesh_source — Return source code for a single symbol by ID.
 */
import type { StorageBackend } from "../graph/storage.js";
export interface SourceInput {
    id: string;
}
export interface SourceOutput {
    symbol: string;
    id: string;
    filePath: string;
    absolutePath: string;
    kind: string;
    signature: string;
    summary: string | null;
    lineStart: number;
    lineEnd: number;
    source: string;
}
export declare function handleSource(storage: StorageBackend, input: SourceInput, projectRoot: string): Promise<SourceOutput>;
