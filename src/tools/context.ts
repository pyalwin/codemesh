/**
 * codemesh_context — Get full context for a file or symbol.
 */

import { join } from "node:path";
import type { StorageBackend } from "../graph/storage.js";
import type { GraphNode, GraphEdge, SymbolNode } from "../graph/types.js";
import { readSourceLines } from "./source-reader.js";

export interface ContextInput {
  path: string;
  symbol?: string;
}

export type SymbolWithSource = GraphNode & {
  source_code?: string | null;
};

export interface ContextOutput {
  file: (GraphNode & { absolutePath?: string }) | null;
  symbols: SymbolWithSource[];
  incomingEdges: GraphEdge[];
  outgoingEdges: GraphEdge[];
  concepts: GraphNode[];
  workflows: GraphNode[];
}

export async function handleContext(
  storage: StorageBackend,
  input: ContextInput,
  projectRoot?: string,
): Promise<ContextOutput> {
  // Find the file node by path
  const fileNodes = await storage.queryNodes({ type: "file", path: input.path });
  const file = fileNodes.length > 0 ? fileNodes[0] : null;

  if (!file) {
    return {
      file: null,
      symbols: [],
      incomingEdges: [],
      outgoingEdges: [],
      concepts: [],
      workflows: [],
    };
  }

  let targetId = file.id;
  let targetNode: GraphNode | null = file;

  // If a specific symbol is requested, find it using fuzzy matching
  if (input.symbol) {
    const containsEdges = await storage.getEdges(file.id, "out", ["contains"]);
    let matchedSymbol: GraphNode | null = null;
    
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
      targetNode = matchedSymbol;
    } else {
      // Fallback to exact match
      targetId = `symbol:${input.path}:${input.symbol}`;
      targetNode = await storage.getNode(targetId);
    }
  }

  if (!targetNode) {
    return {
      file: { ...file, ...(projectRoot ? { absolutePath: join(projectRoot, input.path) } : {}) },
      symbols: [],
      incomingEdges: [],
      outgoingEdges: [],
      concepts: [],
      workflows: [],
    };
  }

  // Get symbols via contains edges from the file
  const containsEdges = await storage.getEdges(file.id, "out", ["contains"]);
  const symbols: SymbolWithSource[] = [];
  for (const edge of containsEdges) {
    const node = await storage.getNode(edge.toId);
    if (node) {
      const entry: SymbolWithSource = { ...node };
      if (projectRoot && node.type === "symbol") {
        const sym = node as SymbolNode;
        entry.source_code = readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd);
      }
      symbols.push(entry);
    }
  }

  // Get incoming and outgoing edges for the target
  const incomingEdges = await storage.getEdges(targetId, "in");
  const outgoingEdges = await storage.getEdges(targetId, "out");

  // Find concepts via describes edges pointing to the target or its symbols
  const concepts: GraphNode[] = [];
  const conceptIds = new Set<string>();

  // Check describes edges pointing to the target
  for (const edge of incomingEdges) {
    if (edge.type === "describes") {
      const node = await storage.getNode(edge.fromId);
      if (node && node.type === "concept" && !conceptIds.has(node.id)) {
        concepts.push(node);
        conceptIds.add(node.id);
      }
    }
  }

  // Also check describes edges pointing to symbols of this file
  if (!input.symbol) {
    for (const sym of symbols) {
      const symIncoming = await storage.getEdges(sym.id, "in", ["describes"]);
      for (const edge of symIncoming) {
        const node = await storage.getNode(edge.fromId);
        if (node && node.type === "concept" && !conceptIds.has(node.id)) {
          concepts.push(node);
          conceptIds.add(node.id);
        }
      }
    }
  }

  // Find workflows via traverses edges pointing to the target file
  const workflows: GraphNode[] = [];
  const workflowIds = new Set<string>();

  // Check traverses edges pointing to the file
  const fileIncoming = await storage.getEdges(file.id, "in", ["traverses"]);
  for (const edge of fileIncoming) {
    const node = await storage.getNode(edge.fromId);
    if (node && node.type === "workflow" && !workflowIds.has(node.id)) {
      workflows.push(node);
      workflowIds.add(node.id);
    }
  }

  const fileOutput: ContextOutput["file"] = file
    ? { ...file, ...(projectRoot ? { absolutePath: join(projectRoot, input.path) } : {}) }
    : null;

  return {
    file: fileOutput,
    symbols,
    incomingEdges,
    outgoingEdges,
    concepts,
    workflows,
  };
}
