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
  let targetId = `file:${input.path}`;

  // If a specific symbol is requested, find it using fuzzy matching
  if (input.symbol) {
    const fileId = `file:${input.path}`;
    let matchedSymbol: GraphNode | null = null;
    const containsEdges = await storage.getEdges(fileId, "out", ["contains"]);
    
    for (const edge of containsEdges) {
      const node = await storage.getNode(edge.toId);
      if (
        node &&
        (node.name === input.symbol ||
          node.name.includes(input.symbol) ||
          input.symbol.includes(node.name))
      ) {
        matchedSymbol = node;
        break;
      }
    }

    if (matchedSymbol) {
      targetId = matchedSymbol.id;
    } else {
      // Fallback to exact match
      targetId = `symbol:${input.path}:${input.symbol}`;
    }
  }

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

  // Sort dependents by PageRank (most important first)
  dependents.sort((a, b) => {
    const aPr = (a.node as any).pagerankScore ?? 0;
    const bPr = (b.node as any).pagerankScore ?? 0;
    return bPr - aPr;
  });

  return {
    dependents,
    total: dependents.length,
  };
}
