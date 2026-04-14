/**
 * codemesh_enrich — Create a concept node describing a file or symbol.
 */
import type { StorageBackend } from "../graph/storage.js";
export interface EnrichInput {
    path: string;
    symbol?: string;
    summary: string;
    related_files?: string[];
    sessionId: string;
}
export interface EnrichOutput {
    nodeId: string;
    success: boolean;
}
export declare function handleEnrich(storage: StorageBackend, input: EnrichInput): Promise<EnrichOutput>;
