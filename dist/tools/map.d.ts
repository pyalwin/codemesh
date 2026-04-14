/**
 * codemesh_map — Map the call graph from a query or symbol.
 *
 * Returns a summary-enriched tree of symbols with no source code.
 * Use codemesh_source to read the code of specific symbols.
 */
import type { StorageBackend } from "../graph/storage.js";
import { type MapNode } from "./map-tree.js";
export interface MapInput {
    query: string;
    symbol?: string;
}
export interface MapOutput {
    startingPoints: MapNode[];
    totalSymbols: number;
}
export declare function handleMap(storage: StorageBackend, input: MapInput, projectRoot: string): Promise<MapOutput>;
