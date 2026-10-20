/** Retrieve attributed cross-session knowledge without treating it as code fact. */
import type { StorageBackend } from "../graph/storage.js";
export interface RecallInput {
    id?: string;
    path?: string;
    symbol?: string;
    query?: string;
    limit?: number;
}
export interface RecalledNote {
    id: string;
    kind: "concept" | "workflow";
    summary: string;
    status: "hypothesis" | "verified" | "stale";
    targets: string[];
    author?: string;
    updatedAt: string;
}
export declare function handleRecall(storage: StorageBackend, input: RecallInput, projectRoot?: string): Promise<{
    notes: RecalledNote[];
}>;
