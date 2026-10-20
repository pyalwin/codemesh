/** Revision-aware, exact symbol source retrieval shared by CLI and MCP. */
import type { StorageBackend } from "../graph/storage.js";
export type SourceMode = "fold" | "preview" | "full" | "auto";
export interface SourceInput {
    /** Canonical symbol ID, or a symbol name that resolves without ambiguity. */
    id: string;
    /** Defaults to exact full source. Auto bounds navigation views; force does not expand them. */
    mode?: SourceMode;
    ifHash?: string;
    force?: boolean;
    sessionId?: string;
    contextEpoch?: string;
}
export interface SourceOutput {
    status: "fresh" | "unchanged" | "stale" | "deleted";
    symbol: string;
    id: string;
    filePath: string;
    absolutePath: string;
    kind: string;
    signature: string;
    summary: string | null;
    lineStart: number;
    lineEnd: number;
    /** Requested mode; auto may be full or bounded, as indicated by omittedRanges. */
    mode: SourceMode;
    /** Hash of the exact rendered source field for this mode. */
    sourceHash: string | null;
    /** Hash of the full current symbol body when the index is fresh. */
    bodyHash: string | null;
    indexRevision: string | null;
    source: string | null;
    omittedRanges?: Array<{
        start: number;
        end: number;
    }>;
    hint?: string;
}
export declare function handleSource(storage: StorageBackend, input: SourceInput, projectRoot: string): Promise<SourceOutput>;
