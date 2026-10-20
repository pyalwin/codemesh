/** Deterministic, one-hop navigation over the indexed code graph. */
import type { StorageBackend } from "../graph/storage.js";
import type { GraphNode } from "../graph/types.js";
export type NavigationRelation = "contains" | "calls" | "called_by" | "imports" | "imported_by" | "extends";
export interface NavigationInput {
    node?: string;
    relation?: NavigationRelation;
    limit?: number;
    cursor?: string;
    /** Hard bound on the serialized JSON response; complete entries only. */
    maxBytes?: number;
}
export interface NavigationEntry {
    id: string;
    name: string;
    type: "repo" | "dir" | GraphNode["type"];
    path?: string;
    signature?: string;
    hasChildren?: boolean;
}
export interface NavigationOutput {
    node: NavigationEntry;
    entries: NavigationEntry[];
    nextCursor?: string;
    truncated?: boolean;
    omitted?: {
        entries: number;
    };
    maxBytes?: number;
    hint?: string;
}
/**
 * Expand one hierarchy level or one graph edge type. Navigation returns IDs,
 * names and signatures only; exact code remains behind codemesh_source.
 */
export declare function handleNavigation(storage: StorageBackend, input: NavigationInput): Promise<NavigationOutput>;
