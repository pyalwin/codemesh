/**
 * codemesh_answer — One-call context assembly.
 *
 * Takes a natural language question, searches the graph, follows call chains,
 * and assembles everything into one structured response. The agent gets a
 * complete context package in ONE call.
 */

import { join } from "node:path";
import type { StorageBackend } from "../graph/storage.js";
import type { GraphNode, SymbolNode, FileNode, SearchResult } from "../graph/types.js";

export interface AnswerInput {
  question: string;
}

export interface AnswerOutput {
  question: string;
  relevantFiles: Array<{
    path: string;
    absolutePath: string;
    why: string;
    symbols: Array<{
      name: string;
      kind: string;
      signature: string;
      lineStart: number;
      lineEnd: number;
    }>;
  }>;
  callChains: Array<{
    from: string;
    path: string[];
  }>;
  concepts: Array<{
    summary: string;
    file: string;
  }>;
  workflows: Array<{
    name: string;
    description: string;
    files: string[];
  }>;
  suggestedReads: Array<{
    file: string;
    absolutePath: string;
    lines: string;
    reason: string;
  }>;
}

export async function handleAnswer(
  storage: StorageBackend,
  input: AnswerInput,
  projectRoot: string,
): Promise<AnswerOutput> {
  // Step 1: Search the graph with the question (FTS5 + trigram fallback)
  const searchResults = await storage.search(input.question, "all");
  const topResults = searchResults.slice(0, 10);

  // Collect unique files and symbols from results
  const fileMap = new Map<string, {
    node: FileNode;
    why: string;
    symbols: Array<{
      name: string;
      kind: string;
      signature: string;
      lineStart: number;
      lineEnd: number;
    }>;
  }>();
  const symbolResults: Array<{ sym: SymbolNode; rank: number; matchedField: string }> = [];

  for (const result of topResults) {
    const node = result.node;

    if (node.type === "file") {
      const fileNode = node as FileNode;
      if (!fileMap.has(fileNode.path)) {
        fileMap.set(fileNode.path, {
          node: fileNode,
          why: `Matched via ${result.matchedField} search (rank ${result.rank.toFixed(2)})`,
          symbols: [],
        });
      }
    } else if (node.type === "symbol") {
      const sym = node as SymbolNode;
      symbolResults.push({ sym, rank: result.rank, matchedField: result.matchedField });

      // Also track the file this symbol belongs to
      if (!fileMap.has(sym.filePath)) {
        const fileId = `file:${sym.filePath}`;
        const fileNode = await storage.getNode(fileId);
        if (fileNode && fileNode.type === "file") {
          fileMap.set(sym.filePath, {
            node: fileNode as FileNode,
            why: `Contains matching symbol '${sym.name}'`,
            symbols: [],
          });
        }
      }
    }
  }

  // Step 2: For each file result, get its symbols and imports
  for (const [filePath, entry] of fileMap) {
    const containsEdges = await storage.getEdges(entry.node.id, "out", ["contains"]);
    for (const edge of containsEdges) {
      const node = await storage.getNode(edge.toId);
      if (!node || node.type !== "symbol") continue;
      const sym = node as SymbolNode;
      entry.symbols.push({
        name: sym.name,
        kind: sym.kind,
        signature: sym.signature,
        lineStart: sym.lineStart,
        lineEnd: sym.lineEnd,
      });
    }

    // Follow imports to discover more relevant files
    const importEdges = await storage.getEdges(entry.node.id, "out", ["imports"]);
    for (const edge of importEdges) {
      const target = await storage.getNode(edge.toId);
      if (target && target.type === "file" && !fileMap.has((target as FileNode).path)) {
        const importedFile = target as FileNode;
        // Only add imported files if they are not already tracked (limit expansion)
        if (fileMap.size < 15) {
          const containsEdgesImported = await storage.getEdges(importedFile.id, "out", ["contains"]);
          const importedSymbols: typeof entry.symbols = [];
          for (const ce of containsEdgesImported) {
            const sNode = await storage.getNode(ce.toId);
            if (sNode && sNode.type === "symbol") {
              const s = sNode as SymbolNode;
              importedSymbols.push({
                name: s.name,
                kind: s.kind,
                signature: s.signature,
                lineStart: s.lineStart,
                lineEnd: s.lineEnd,
              });
            }
          }
          fileMap.set(importedFile.path, {
            node: importedFile,
            why: `Imported by '${filePath}'`,
            symbols: importedSymbols,
          });
        }
      }
    }
  }

  // Step 3: For each symbol result, follow outgoing call edges (depth 3)
  const callChains: AnswerOutput["callChains"] = [];
  for (const { sym } of symbolResults) {
    const chain = await buildCallChain(storage, sym.id, sym.name, 3);
    if (chain.path.length > 0) {
      callChains.push(chain);
    }
  }

  // Step 4: Collect concepts for matched files
  const concepts: AnswerOutput["concepts"] = [];
  const seenConcepts = new Set<string>();
  for (const [filePath, entry] of fileMap) {
    const incomingEdges = await storage.getEdges(entry.node.id, "in", ["describes"]);
    for (const edge of incomingEdges) {
      if (seenConcepts.has(edge.fromId)) continue;
      seenConcepts.add(edge.fromId);
      const conceptNode = await storage.getNode(edge.fromId);
      if (conceptNode && conceptNode.type === "concept") {
        concepts.push({
          summary: (conceptNode as any).summary ?? "",
          file: filePath,
        });
      }
    }
  }

  // Step 5: Collect workflows that traverse matched files
  const workflows: AnswerOutput["workflows"] = [];
  const seenWorkflows = new Set<string>();
  for (const [, entry] of fileMap) {
    const incomingEdges = await storage.getEdges(entry.node.id, "in", ["traverses"]);
    for (const edge of incomingEdges) {
      if (seenWorkflows.has(edge.fromId)) continue;
      seenWorkflows.add(edge.fromId);
      const wfNode = await storage.getNode(edge.fromId);
      if (wfNode && wfNode.type === "workflow") {
        workflows.push({
          name: wfNode.name,
          description: (wfNode as any).description ?? "",
          files: (wfNode as any).fileSequence ?? [],
        });
      }
    }
  }

  // Step 6: Build relevantFiles output
  const relevantFiles: AnswerOutput["relevantFiles"] = [];
  for (const [filePath, entry] of fileMap) {
    relevantFiles.push({
      path: filePath,
      absolutePath: join(projectRoot, filePath),
      why: entry.why,
      symbols: entry.symbols,
    });
  }

  // Step 7: Build suggestedReads — top 5 most relevant symbols by search rank
  const suggestedReads: AnswerOutput["suggestedReads"] = [];
  const rankedSymbols = symbolResults
    .sort((a, b) => b.rank - a.rank)
    .slice(0, 5);

  for (const { sym, matchedField } of rankedSymbols) {
    suggestedReads.push({
      file: sym.filePath,
      absolutePath: join(projectRoot, sym.filePath),
      lines: `${sym.lineStart}-${sym.lineEnd}`,
      reason: `${sym.name} (${sym.kind}) — matched via ${matchedField}`,
    });
  }

  // If we have fewer than 5 suggested reads, fill from file symbols
  if (suggestedReads.length < 5) {
    const suggestedPaths = new Set(suggestedReads.map(s => `${s.file}:${s.lines}`));
    for (const [, entry] of fileMap) {
      if (suggestedReads.length >= 5) break;
      for (const sym of entry.symbols) {
        const key = `${(entry.node as FileNode).path}:${sym.lineStart}-${sym.lineEnd}`;
        if (suggestedPaths.has(key)) continue;
        suggestedPaths.add(key);
        suggestedReads.push({
          file: (entry.node as FileNode).path,
          absolutePath: join(projectRoot, (entry.node as FileNode).path),
          lines: `${sym.lineStart}-${sym.lineEnd}`,
          reason: `${sym.name} (${sym.kind}) — in relevant file`,
        });
        if (suggestedReads.length >= 5) break;
      }
    }
  }

  return {
    question: input.question,
    relevantFiles,
    callChains,
    concepts,
    workflows,
    suggestedReads,
  };
}

/**
 * Build a call chain from a starting symbol node, following outgoing "calls" edges.
 */
async function buildCallChain(
  storage: StorageBackend,
  startId: string,
  startName: string,
  maxDepth: number,
): Promise<{ from: string; path: string[] }> {
  const path: string[] = [];
  const visited = new Set<string>([startId]);
  const queue: Array<{ id: string; depth: number }> = [];

  // Seed with direct callees
  const startEdges = await storage.getEdges(startId, "out", ["calls"]);
  for (const edge of startEdges) {
    queue.push({ id: edge.toId, depth: 1 });
  }

  while (queue.length > 0) {
    const { id, depth } = queue.shift()!;
    if (visited.has(id) || depth > maxDepth) continue;
    visited.add(id);

    const node = await storage.getNode(id);
    if (!node) continue;

    path.push(node.name);

    if (depth < maxDepth) {
      const edges = await storage.getEdges(id, "out", ["calls"]);
      for (const edge of edges) {
        if (!visited.has(edge.toId)) {
          queue.push({ id: edge.toId, depth: depth + 1 });
        }
      }
    }
  }

  return { from: startName, path };
}
