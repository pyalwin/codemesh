import type { StorageBackend } from "../graph/storage.js";
import { type SourceOutput } from "./source.js";
import { type CallBinding, type IncomingCoverage } from "./inspect-resolver.js";
export interface InspectInput {
    id: string;
    depth?: 0 | 1;
    maxSymbols?: number;
    direction?: "outgoing" | "incoming" | "both";
    /** Maximum indexed caller candidates whose live binding is checked. */
    maxCandidates?: number;
    /** UTF-8 bytes of JSON.stringify(output), including the metadata envelope. */
    maxBytes?: number;
}
export interface InspectCall extends CallBinding {
    /** True caller -> callee provenance; direction is relative to rootId. */
    sourceId: string;
    direction: "outgoing" | "incoming";
    relation: "calls";
    /** Body bundled for this relationship (caller for incoming, callee for outgoing). */
    neighborId?: string;
    status: "included" | "omitted" | "stale" | "deleted" | "unresolved" | "ambiguous";
    sourceIndex?: number;
}
export interface InspectOutput {
    status: "fresh" | "stale" | "deleted" | "budget_exceeded";
    rootId: string;
    sources: SourceOutput[];
    calls: InspectCall[];
    omitted: {
        sources: number;
        calls: number;
    };
    limits: {
        maxBytes: number;
        maxSymbols: number;
        depth: 0 | 1;
        direction: "outgoing" | "incoming" | "both";
        maxCandidates: number;
    };
    incoming?: IncomingCoverage;
    hint?: string;
}
export declare function handleInspect(storage: StorageBackend, input: InspectInput, projectRoot: string): Promise<InspectOutput>;
