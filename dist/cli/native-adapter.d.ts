/** Conservative adapter for observations produced by native agent tools. */
import type { StorageBackend } from "../graph/storage.js";
export interface AdaptedObservation {
    output: string;
    transformed: boolean;
    reason: string;
}
/** Fold repeated rg paths while preserving every matched line and its order. */
export declare function foldNativeSearch(output: string): AdaptedObservation;
export declare function outlineNativeRead(storage: StorageBackend, projectRoot: string, path: string, output: string, intent: "navigate" | "edit"): Promise<AdaptedObservation>;
