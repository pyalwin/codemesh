/**
 * codemesh_map — Navigate indexed code or map a call graph from a query.
 *
 * Without a query or symbol, returns one bounded, deterministic navigation step.
 * Legacy query/symbol inputs still return a summary-enriched call tree.
 * Use codemesh_source to read exact symbol code.
 */
import type { StorageBackend } from "../graph/storage.js";
import { type MapNode } from "./map-tree.js";
import { type NavigationInput, type NavigationOutput } from "./navigation.js";
export interface MapInput extends NavigationInput {
    query?: string;
    symbol?: string;
}
export interface MapOutput {
    startingPoints: MapNode[];
    totalSymbols: number;
    truncated?: boolean;
}
export declare function handleMap(storage: StorageBackend, input: MapInput, projectRoot: string): Promise<MapOutput | NavigationOutput>;
