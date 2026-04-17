/**
 * Shared tree builder for codemesh_map and codemesh_answer.
 *
 * BFS through calls edges with a visited set, no depth limit.
 * Returns a recursive tree with summary/kind/filePath/id/pagerank on each node.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { SymbolNode } from "../graph/types.js";

export interface MapNode {
  symbol: string;
  id: string;
  filePath: string;
  kind: string;
  signature?: string;
  lineStart?: number;
  lineEnd?: number;
  summary: string | null;
  relationship?: string;
  pagerank: number | null;
  children: MapNode[];
  /** Immediate (depth-1) callers — flat list, not recursive. Capped at 8. */
  calledBy?: Array<{ symbol: string; id: string; filePath: string }>;
}

const MAX_CALLERS_PER_NODE = 8;

export async function buildMapTree(
  storage: StorageBackend,
  startNodeIds: string[],
): Promise<{ nodes: MapNode[]; totalSymbols: number }> {
  const visited = new Set<string>();
  const roots: MapNode[] = [];

  for (const startId of startNodeIds) {
    if (visited.has(startId)) continue;
    const node = await buildNode(storage, startId, visited);
    if (node) roots.push(node);
  }

  return { nodes: roots, totalSymbols: visited.size };
}

async function buildNode(
  storage: StorageBackend,
  nodeId: string,
  visited: Set<string>,
  relationship?: string,
): Promise<MapNode | null> {
  if (visited.has(nodeId)) return null;
  visited.add(nodeId);

  const node = await storage.getNode(nodeId);
  if (!node || node.type !== "symbol") return null;

  const sym = node as SymbolNode;

  // Get outgoing calls edges and build children recursively
  const callEdges = await storage.getEdges(nodeId, "out", ["calls"]);
  const children: MapNode[] = [];

  for (const edge of callEdges) {
    const child = await buildNode(storage, edge.toId, visited, edge.type);
    if (child) children.push(child);
  }

  // Collect immediate callers (flat, non-recursive) — the inverse of `children`
  const incomingCallEdges = await storage.getEdges(nodeId, "in", ["calls"]);
  const calledBy: NonNullable<MapNode["calledBy"]> = [];
  for (const edge of incomingCallEdges.slice(0, MAX_CALLERS_PER_NODE)) {
    const caller = await storage.getNode(edge.fromId);
    if (caller && caller.type === "symbol") {
      const sCaller = caller as SymbolNode;
      calledBy.push({
        symbol: sCaller.name,
        id: sCaller.id,
        filePath: sCaller.filePath,
      });
    }
  }

  return {
    symbol: sym.name,
    id: sym.id,
    filePath: sym.filePath,
    kind: sym.kind,
    signature: sym.signature,
    lineStart: sym.lineStart,
    lineEnd: sym.lineEnd,
    summary: sym.summary ?? null,
    ...(relationship ? { relationship } : {}),
    pagerank: sym.pagerankScore ?? null,
    children,
    ...(calledBy.length > 0 ? { calledBy } : {}),
  };
}
