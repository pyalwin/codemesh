/**
 * codemesh_status — Return graph statistics.
 */

import type { StorageBackend } from "../graph/storage.js";

export interface StatusOutput {
  nodeCount: Record<string, number>;
  edgeCount: Record<string, number>;
  staleCount: number;
  lastIndexedAt: string | null;
}

export async function handleStatus(
  storage: StorageBackend,
): Promise<StatusOutput> {
  return storage.getStats();
}
