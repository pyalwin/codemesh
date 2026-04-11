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
  file: GraphNode | null;
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

  // Add absolute path to file for easy Read access regardless of CWD
  const enrichedFile = projectRoot
    ? { ...file, absolutePath: join(projectRoot, input.path) }
    : file;

  // If a specific symbol is requested, find it and return context for it
  const targetId = input.symbol
    ? `symbol:${input.path}:${input.symbol}`
    : file.id;

  const targetNode = input.symbol
    ? await storage.getNode(targetId)
    : file;

  if (!targetNode) {
    return {
      file: enrichedFile,
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
      const symbolWithSource: SymbolWithSource = { ...node };
      if (projectRoot && node.type === "symbol") {
        const sym = node as SymbolNode;
        symbolWithSource.source_code = readSourceLines(
          projectRoot,
          sym.filePath,
          sym.lineStart,
          sym.lineEnd,
        );
      }
      symbols.push(symbolWithSource);
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

  return {
    file: enrichedFile,
    symbols,
    incomingEdges,
    outgoingEdges,
    concepts,
    workflows,
  };
}
