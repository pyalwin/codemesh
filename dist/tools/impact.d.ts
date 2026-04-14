/**
 * codemesh_impact — Find all reverse dependencies of a file or symbol.
 */
import type { StorageBackend } from "../graph/storage.js";
import type { GraphNode } from "../graph/types.js";
export interface ImpactInput {
    path: string;
    symbol?: string;
}
export interface ImpactOutput {
    dependents: Array<{
        node: GraphNode;
        relationship: string;
    }>;
    total: number;
}
export declare function handleImpact(storage: StorageBackend, input: ImpactInput): Promise<ImpactOutput>;
