/** Restore saved references as bounded, exact, live-verified source. */
import type { StorageBackend } from "../graph/storage.js";
import { type SourceMode, type SourceOutput } from "./source.js";
export interface HydrateReference {
    /** A canonical ID, or an unambiguous name accepted by source. */
    id: string;
    /** Expected hash of the saved rendered view, not necessarily the full body. */
    hash?: string;
    /** View to use when checking hash; defaults to full. Delivered code is always full. */
    mode?: SourceMode;
    fileHash?: string;
    bodyHash?: string;
}
export interface HydrateInput {
    references: HydrateReference[];
    maxSymbols?: number;
    /** UTF-8 bytes of JSON.stringify(output), including its metadata envelope. */
    maxBytes?: number;
}
export interface HydrateReferenceResult {
    /** Input position remains available even when long diagnostic IDs are omitted. */
    index: number;
    requestedId?: string;
    id?: string;
    status: "fresh" | "changed" | "stale" | "missing" | "ambiguous" | "omitted" | "error";
    reason?: string;
    detail?: string;
    sourceIndex?: number;
}
export interface HydrateOutput {
    status: "complete" | "partial";
    sources: SourceOutput[];
    references: HydrateReferenceResult[];
    /** Reference entries whose verified source was excluded by a bundle limit. */
    omitted: {
        sources: number;
    };
    limits: {
        maxBytes: number;
        maxSymbols: number;
    };
}
/**
 * changed: a fresh live body/view/file differs from an explicit saved hash.
 * stale: the index cannot verify current source, including a concurrent edit.
 * Neither condition returns source bytes for that reference. A hash of a saved
 * preview only checks that preview; callers needing whole-body continuity must
 * also save bodyHash or fileHash. Missing expected hashes simply request current
 * live source and do not certify that a note's earlier interpretation is valid.
 */
export declare function handleHydrate(storage: StorageBackend, input: HydrateInput, projectRoot: string): Promise<HydrateOutput>;
