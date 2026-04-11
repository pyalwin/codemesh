/**
 * codemesh_trace — Trace a call chain from a symbol and return source code.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { SymbolNode } from "../graph/types.js";
import { readSourceLines } from "./source-reader.js";

export interface TraceInput {
  symbol: string; // symbol name to start tracing from
  depth?: number; // max depth (default 3)
}

export interface TraceStep {
  symbol: string;
  filePath: string;
  kind: string;
  signature: string;
  source: string | null;
  calls: string[]; // names of symbols this one calls
}

export interface TraceOutput {
  startSymbol: string;
  steps: TraceStep[];
  depth: number;
}

export async function handleTrace(
  storage: StorageBackend,
  input: TraceInput,
  projectRoot: string,
): Promise<TraceOutput> {
  const maxDepth = input.depth ?? 3;
  const steps: TraceStep[] = [];
  const visited = new Set<string>();

  // Find the starting symbol
  let startNodes = await storage.queryNodes({ type: "symbol", name: input.symbol });
  if (startNodes.length === 0) {
    // Try fuzzy search
    const searchResults = await storage.search(input.symbol, "symbols");
    if (searchResults.length > 0) {
      startNodes = [searchResults[0].node];
    }
  }

  if (startNodes.length === 0) {
    return { startSymbol: input.symbol, steps: [], depth: maxDepth };
  }

  // BFS through call chain
  const queue: Array<{ nodeId: string; currentDepth: number }> = [
    { nodeId: startNodes[0].id, currentDepth: 0 },
  ];

  while (queue.length > 0) {
    const { nodeId, currentDepth } = queue.shift()!;

    if (visited.has(nodeId) || currentDepth > maxDepth) continue;
    visited.add(nodeId);

    const node = await storage.getNode(nodeId);
    if (!node || node.type !== "symbol") continue;

    const sym = node as SymbolNode;

    // Get outgoing calls edges
    const callEdges = await storage.getEdges(nodeId, "out", ["calls"]);
    const calleeNames: string[] = [];

    for (const edge of callEdges) {
      const callee = await storage.getNode(edge.toId);
      if (callee) {
        calleeNames.push(callee.name);
        if (currentDepth + 1 <= maxDepth) {
          queue.push({ nodeId: edge.toId, currentDepth: currentDepth + 1 });
        }
      }
    }

    steps.push({
      symbol: sym.name,
      filePath: sym.filePath,
      kind: sym.kind,
      signature: sym.signature,
      source: readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd, 50),
      calls: calleeNames,
    });
  }

  return { startSymbol: input.symbol, steps, depth: maxDepth };
}
