/**
 * codemesh_explore — The "mega-tool". Takes a task description, searches the graph,
 * traverses ALL connected nodes to completion, and returns full source code for
 * every symbol in the connected subgraph. One call, complete picture.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { GraphNode, SymbolNode, FileNode, ConceptNode, WorkflowNode } from "../graph/types.js";
import { readSourceLines } from "./source-reader.js";

// ── Types ────────────────────────────────────────────────────────────

export interface ExploreInput {
  task: string;            // Natural language task description
  includeSource?: boolean; // Include actual source code? (default true). Set false for map-only.
  maxDepth?: number;       // Safety valve — max traversal depth (default 10)
  maxSymbols?: number;     // Safety valve — max symbols to include (default 200)
}

export interface ExploreSymbol {
  name: string;
  kind: string;
  filePath: string;
  lineStart: number;
  lineEnd: number;
  signature: string;
  source: string | null;
  calls: string[];      // outgoing call targets
  calledBy: string[];   // incoming callers
}

export interface ExploreFile {
  path: string;
  symbols: string[];    // symbol names in this file
  imports: string[];    // files this file imports
  importedBy: string[]; // files that import this file
  concepts: string[];   // agent-written summaries
}

export interface ExploreOutput {
  task: string;
  entryPoints: string[];        // symbols that matched the initial search
  symbols: ExploreSymbol[];     // every symbol in the traversed subgraph
  files: ExploreFile[];         // every file touched
  workflows: Array<{ name: string; description: string; files: string[] }>;
  stats: {
    symbolCount: number;
    fileCount: number;
    traversalDepth: number;
    searchHits: number;
  };
}

// ── Implementation ───────────────────────────────────────────────────

export async function handleExplore(
  storage: StorageBackend,
  input: ExploreInput,
  projectRoot: string,
): Promise<ExploreOutput> {
  const maxDepth = input.maxDepth ?? 10;
  const maxSymbols = input.maxSymbols ?? 200;
  const includeSource = input.includeSource !== false; // default true

  // Phase 1: Search the graph for entry points
  const searchResults = await storage.search(input.task, "all");
  const entryNodeIds: string[] = [];
  const entryNames: string[] = [];

  for (const result of searchResults.slice(0, 15)) {
    entryNodeIds.push(result.node.id);
    entryNames.push(result.node.name);

    // If entry is a file, also add its contained symbols as entry points
    if (result.node.type === "file") {
      const containsEdges = await storage.getEdges(result.node.id, "out", ["contains"]);
      for (const edge of containsEdges) {
        if (!entryNodeIds.includes(edge.toId)) {
          entryNodeIds.push(edge.toId);
        }
      }
    }
  }

  // Phase 2: BFS traverse ALL edges from entry points until leaf or visited
  const visitedNodes = new Set<string>();
  const symbolMap = new Map<string, ExploreSymbol>();
  const fileSet = new Set<string>();
  let actualMaxDepth = 0;

  const queue: Array<{ nodeId: string; depth: number }> = entryNodeIds.map(
    (id) => ({ nodeId: id, depth: 0 })
  );

  while (queue.length > 0 && symbolMap.size < maxSymbols) {
    const { nodeId, depth } = queue.shift()!;

    if (visitedNodes.has(nodeId) || depth > maxDepth) continue;
    visitedNodes.add(nodeId);
    actualMaxDepth = Math.max(actualMaxDepth, depth);

    const node = await storage.getNode(nodeId);
    if (!node) continue;

    if (node.type === "symbol") {
      const sym = node as SymbolNode;
      fileSet.add(sym.filePath);

      // Get outgoing calls
      const outEdges = await storage.getEdges(nodeId, "out", ["calls"]);
      const callNames: string[] = [];
      for (const edge of outEdges) {
        const target = await storage.getNode(edge.toId);
        if (target) {
          callNames.push(target.name);
          // Continue traversal through calls
          if (!visitedNodes.has(edge.toId)) {
            queue.push({ nodeId: edge.toId, depth: depth + 1 });
          }
        }
      }

      // Get incoming callers
      const inEdges = await storage.getEdges(nodeId, "in", ["calls"]);
      const callerNames: string[] = [];
      for (const edge of inEdges) {
        const caller = await storage.getNode(edge.fromId);
        if (caller) {
          callerNames.push(caller.name);
          // Also traverse callers (upstream context)
          if (!visitedNodes.has(edge.fromId)) {
            queue.push({ nodeId: edge.fromId, depth: depth + 1 });
          }
        }
      }

      symbolMap.set(nodeId, {
        name: sym.name,
        kind: sym.kind,
        filePath: sym.filePath,
        lineStart: sym.lineStart,
        lineEnd: sym.lineEnd,
        signature: sym.signature,
        source: includeSource
          ? readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd, 100)
          : null,
        calls: callNames,
        calledBy: callerNames,
      });
    } else if (node.type === "file") {
      const file = node as FileNode;
      fileSet.add(file.path);

      // Traverse into contained symbols
      const containsEdges = await storage.getEdges(nodeId, "out", ["contains"]);
      for (const edge of containsEdges) {
        if (!visitedNodes.has(edge.toId)) {
          queue.push({ nodeId: edge.toId, depth: depth + 1 });
        }
      }

      // Traverse imports (connected files)
      const importEdges = await storage.getEdges(nodeId, "out", ["imports"]);
      for (const edge of importEdges) {
        if (!visitedNodes.has(edge.toId)) {
          queue.push({ nodeId: edge.toId, depth: depth + 1 });
        }
      }
    }
  }

  // Phase 3: Assemble file information
  const fileDetails: ExploreFile[] = [];
  for (const filePath of fileSet) {
    const fileId = `file:${filePath}`;
    const fileNode = await storage.getNode(fileId);
    if (!fileNode) continue;

    // Symbols in this file
    const containsEdges = await storage.getEdges(fileId, "out", ["contains"]);
    const symbolNames: string[] = [];
    for (const edge of containsEdges) {
      const sym = await storage.getNode(edge.toId);
      if (sym) symbolNames.push(sym.name);
    }

    // Imports
    const importEdges = await storage.getEdges(fileId, "out", ["imports"]);
    const imports: string[] = [];
    for (const edge of importEdges) {
      const target = await storage.getNode(edge.toId);
      if (target && target.type === "file") imports.push((target as FileNode).path);
    }

    // Imported by
    const importedByEdges = await storage.getEdges(fileId, "in", ["imports"]);
    const importedBy: string[] = [];
    for (const edge of importedByEdges) {
      const source = await storage.getNode(edge.fromId);
      if (source && source.type === "file") importedBy.push((source as FileNode).path);
    }

    // Concepts
    const conceptEdges = await storage.getEdges(fileId, "in", ["describes"]);
    const concepts: string[] = [];
    for (const edge of conceptEdges) {
      const concept = await storage.getNode(edge.fromId);
      if (concept && concept.type === "concept") {
        concepts.push((concept as ConceptNode).summary);
      }
    }

    fileDetails.push({
      path: filePath,
      symbols: symbolNames,
      imports,
      importedBy,
      concepts,
    });
  }

  // Phase 4: Collect relevant workflows
  const workflows: Array<{ name: string; description: string; files: string[] }> = [];
  for (const filePath of fileSet) {
    const fileId = `file:${filePath}`;
    const traverseEdges = await storage.getEdges(fileId, "in", ["traverses"]);
    for (const edge of traverseEdges) {
      const wfNode = await storage.getNode(edge.fromId);
      if (wfNode && wfNode.type === "workflow") {
        const wf = wfNode as WorkflowNode;
        // Avoid duplicates
        if (!workflows.some((w) => w.name === wf.name)) {
          workflows.push({
            name: wf.name,
            description: wf.description,
            files: wf.fileSequence,
          });
        }
      }
    }
  }

  return {
    task: input.task,
    entryPoints: entryNames,
    symbols: Array.from(symbolMap.values()),
    files: fileDetails,
    workflows,
    stats: {
      symbolCount: symbolMap.size,
      fileCount: fileDetails.length,
      traversalDepth: actualMaxDepth,
      searchHits: searchResults.length,
    },
  };
}
