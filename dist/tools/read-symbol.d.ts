/**
 * codemesh read — Read source code for a specific symbol by name.
 *
 * Returns just the source body of a single symbol, avoiding full-file reads.
 * Uses the same multi-strategy symbol finder as trace.
 */
import type { StorageBackend } from "../graph/storage.js";
export interface ReadSymbolInput {
    symbol: string;
}
export interface ReadSymbolOutput {
    symbol: string;
    filePath: string;
    kind: string;
    signature: string;
    lines: string;
    source: string | null;
}
export declare function handleReadSymbol(storage: StorageBackend, input: ReadSymbolInput, projectRoot: string): Promise<ReadSymbolOutput | {
    error: string;
}>;
