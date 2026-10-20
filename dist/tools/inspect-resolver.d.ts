import type { StorageBackend } from "../graph/storage.js";
import { type SourceOutput } from "./source.js";
export interface CallBinding {
    expression: string;
    line: number;
    resolution: "same_file_function" | "imported_module_function" | "imported_function" | "unresolved" | "ambiguous" | "unverified_graph";
    targetId?: string;
    candidateIds?: string[];
    reason?: string;
}
export declare function resolveInspectCalls(storage: StorageBackend, root: SourceOutput, projectRoot: string): Promise<{
    calls: CallBinding[];
    snapshots: Array<{
        path: string;
        hash: string;
    }>;
}>;
/** Incoming discovery is a bounded graph hint set, never an exhaustive call graph. */
export interface IncomingCoverage {
    discovery: "indexed_call_edges";
    complete: false;
    candidateCount: number;
    checked: number;
    verified: number;
    unverified: number;
    omitted: number;
    reason: "candidate_limit" | "graph_may_miss_callers";
}
export interface IncomingCallBinding extends CallBinding {
    sourceId: string;
    failureStatus?: "stale" | "deleted";
}
/**
 * Graph edges select candidates only. Re-resolve the candidate's actual current
 * Python calls and accept only exact target IDs. This deliberately does not
 * scan the repository: aliases missing from the indexed graph remain a stated
 * coverage limitation until a call-site/import-reference index is available.
 */
export declare function resolveInspectIncoming(storage: StorageBackend, root: SourceOutput, projectRoot: string, maxCandidates: number): Promise<{
    calls: IncomingCallBinding[];
    sources: SourceOutput[];
    snapshots: Array<{
        path: string;
        hash: string;
    }>;
    coverage: IncomingCoverage;
}>;
