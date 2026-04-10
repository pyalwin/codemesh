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
  dependents: Array<{ node: GraphNode; relationship: string }>;
  total: number;
}

export async function handleImpact(
  storage: StorageBackend,
  input: ImpactInput,
): Promise<ImpactOutput> {
  // Determine the target node ID
  const targetId = input.symbol
    ? `symbol:${input.path}:${input.symbol}`
    : `file:${input.path}`;

  // Verify the target exists
  const targetNode = await storage.getNode(targetId);
  if (!targetNode) {
    return { dependents: [], total: 0 };
  }

  // Get all incoming edges (things that point TO this target)
  const incomingEdges = await storage.getEdges(targetId, "in");

  const dependents: Array<{ node: GraphNode; relationship: string }> = [];
  const seen = new Set<string>();

  for (const edge of incomingEdges) {
    if (seen.has(edge.fromId)) continue;
    seen.add(edge.fromId);

    const node = await storage.getNode(edge.fromId);
    if (!node) continue;

    dependents.push({
      node,
      relationship: edge.type,
    });
  }

  return {
    dependents,
    total: dependents.length,
  };
}
