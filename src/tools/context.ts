/**
 * codemesh_context — Get full context for a file or symbol.
 *
 * Returns METADATA only — signatures, line numbers, edges, concepts, workflows.
 * Does NOT return source code. The agent reads specific files/functions it needs.
 * This keeps responses small and lets the agent choose what to read.
 *
 * When an LSP client is available, symbol resolution is enhanced with:
 * - Precise definition locations (definedAt)
 * - Reference counts (referencedBy)
 * - Disambiguation of multiple symbol matches
 */

import { join } from "node:path";
import type { StorageBackend } from "../graph/storage.js";
import type { GraphNode, GraphEdge, SymbolNode } from "../graph/types.js";
import type { LspClient } from "./lsp-client.js";

export interface ContextInput {
  path?: string;
  paths?: string[];
  symbol?: string;
}

/** Symbol metadata — enough for the agent to decide whether to Read it */
export interface SymbolInfo {
  name: string;
  kind: string;
  signature: string;
  lineStart: number;
  lineEnd: number;
  lineCount: number;
  /** What this symbol calls (outgoing call edges) */
  calls: string[];
  /** What calls this symbol (incoming call edges) */
  calledBy: string[];
  /** Full call chain reachable from this symbol (depth 5) — shows the complete graph path */
  callChain: string[];
  /** Where this symbol is defined (via LSP), if available */
  definedAt?: { uri: string; line: number; character: number };
  /** Number of references to this symbol (via LSP), if available */
  referencedBy?: number;
}

export interface ContextOutput {
  file: {
    path: string;
    absolutePath: string;
    name: string;
  } | null;
  /** Symbols with rich metadata — signature, line range, calls, calledBy */
  symbols: SymbolInfo[];
  /** Files this file imports */
  imports: string[];
  /** Files that import this file */
  importedBy: string[];
  /** Agent-written summaries about this file */
  concepts: Array<{ summary: string; lastUpdatedBy: string }>;
  /** Known workflows that traverse this file */
  workflows: Array<{ name: string; description: string; files: string[] }>;
  /** Git hotspot data — change frequency and last changed date */
  hotspot?: { changeCount: number; lastChanged: string };
  /** Files that frequently change together with this file */
  coChanges: string[];
}

/**
 * Merge multiple ContextOutput results, deduplicating symbols, imports, concepts, and workflows.
 */
function mergeContextResults(results: ContextOutput[]): ContextOutput {
  const allSymbols: SymbolInfo[] = [];
  const allImports = new Set<string>();
  const allImportedBy = new Set<string>();
  const conceptSummaries = new Set<string>();
  const allConcepts: ContextOutput["concepts"] = [];
  const workflowNames = new Set<string>();
  const allWorkflows: ContextOutput["workflows"] = [];
  const allCoChanges = new Set<string>();

  // Collect the first non-null file entry for the merged file field
  // For multi-path, we create a synthetic file entry
  const files: Array<{ path: string; absolutePath: string; name: string }> = [];
  const symbolNames = new Set<string>();
  let bestHotspot: ContextOutput["hotspot"] | undefined;

  for (const result of results) {
    if (result.file) {
      files.push(result.file);
    }

    for (const sym of result.symbols) {
      const key = `${sym.name}:${sym.lineStart}:${sym.lineEnd}`;
      if (!symbolNames.has(key)) {
        symbolNames.add(key);
        allSymbols.push(sym);
      }
    }

    for (const imp of result.imports) allImports.add(imp);
    for (const imp of result.importedBy) allImportedBy.add(imp);

    for (const concept of result.concepts) {
      if (!conceptSummaries.has(concept.summary)) {
        conceptSummaries.add(concept.summary);
        allConcepts.push(concept);
      }
    }

    for (const wf of result.workflows) {
      if (!workflowNames.has(wf.name)) {
        workflowNames.add(wf.name);
        allWorkflows.push(wf);
      }
    }

    if (result.hotspot) {
      if (!bestHotspot || result.hotspot.changeCount > bestHotspot.changeCount) {
        bestHotspot = result.hotspot;
      }
    }

    for (const cc of result.coChanges) allCoChanges.add(cc);
  }

  return {
    file: files.length === 1 ? files[0] : (files.length > 0 ? files[0] : null),
    symbols: allSymbols,
    imports: Array.from(allImports),
    importedBy: Array.from(allImportedBy),
    concepts: allConcepts,
    workflows: allWorkflows,
    hotspot: bestHotspot,
    coChanges: Array.from(allCoChanges),
  };
}

export async function handleContext(
  storage: StorageBackend,
  input: ContextInput,
  projectRoot?: string,
  lspClient?: LspClient | null,
): Promise<ContextOutput> {
  // Multi-path support: if paths is provided, handle each path and merge
  if (input.paths && input.paths.length > 0) {
    const allResults = await Promise.all(
      input.paths.map(p => handleContext(storage, { path: p, symbol: input.symbol }, projectRoot, lspClient))
    );
    return mergeContextResults(allResults);
  }

  if (!input.path) {
    return { file: null, symbols: [], imports: [], importedBy: [], concepts: [], workflows: [], coChanges: [] };
  }

  const fileNodes = await storage.queryNodes({ type: "file", path: input.path });
  const file = fileNodes.length > 0 ? fileNodes[0] : null;

  if (!file) {
    return { file: null, symbols: [], imports: [], importedBy: [], concepts: [], workflows: [], coChanges: [] };
  }

  const absPath = projectRoot ? join(projectRoot, input.path) : input.path;

  // Get symbols with rich metadata
  const containsEdges = await storage.getEdges(file.id, "out", ["contains"]);
  const symbols: SymbolInfo[] = [];

  for (const edge of containsEdges) {
    const node = await storage.getNode(edge.toId);
    if (!node || node.type !== "symbol") continue;
    const sym = node as SymbolNode;

    // If a specific symbol was requested, filter
    if (input.symbol) {
      const match = sym.name === input.symbol
        || sym.name.includes(input.symbol)
        || input.symbol.includes(sym.name);
      if (!match) continue;
    }

    // Get calls (outgoing)
    const callEdges = await storage.getEdges(node.id, "out", ["calls"]);
    const calls: string[] = [];
    for (const ce of callEdges) {
      const target = await storage.getNode(ce.toId);
      if (target) calls.push(target.name);
    }

    // Get calledBy (incoming)
    const callerEdges = await storage.getEdges(node.id, "in", ["calls"]);
    const calledBy: string[] = [];
    for (const ce of callerEdges) {
      const caller = await storage.getNode(ce.fromId);
      if (caller) calledBy.push(caller.name);
    }

    // BFS to get the full call chain reachable from this symbol (depth 5)
    const callChain: string[] = [];
    const visited = new Set<string>([node.id]);
    const queue: Array<{ id: string; depth: number }> = calls.length > 0
      ? (await Promise.all(callEdges.map(async (ce) => {
          const t = await storage.getNode(ce.toId);
          return t ? { id: ce.toId, depth: 1 } : null;
        }))).filter((x): x is { id: string; depth: number } => x !== null)
      : [];

    while (queue.length > 0) {
      const { id: nid, depth } = queue.shift()!;
      if (visited.has(nid) || depth > 5) continue;
      visited.add(nid);
      const n = await storage.getNode(nid);
      if (!n) continue;
      const nSym = n.type === "symbol" ? n as SymbolNode : null;
      callChain.push(nSym ? `${n.name} (${nSym.filePath}:${nSym.lineStart})` : n.name);
      const nextEdges = await storage.getEdges(nid, "out", ["calls"]);
      for (const ne of nextEdges) {
        if (!visited.has(ne.toId)) {
          queue.push({ id: ne.toId, depth: depth + 1 });
        }
      }
    }

    symbols.push({
      name: sym.name,
      kind: sym.kind,
      signature: sym.signature,
      lineStart: sym.lineStart,
      lineEnd: sym.lineEnd,
      lineCount: sym.lineEnd - sym.lineStart + 1,
      calls,
      calledBy,
      callChain,
    });
  }

  // ── LSP Enhancement ──────────────────────────────────────────────
  // If we have an LSP client and a specific symbol was requested with multiple
  // matches, use LSP to disambiguate to the exact definition.
  if (lspClient && input.symbol && symbols.length > 1) {
    try {
      // Use the first candidate's line to ask LSP for the true definition
      const resolved = await lspClient.getDefinition(input.path, symbols[0].lineStart, 0);
      if (resolved) {
        const filtered = symbols.filter(c => c.lineStart === resolved.line);
        if (filtered.length > 0) {
          symbols.splice(0, symbols.length, ...filtered);
        }
      }
    } catch {
      // LSP failed — keep all candidates, no harm done
    }
  }

  // Enrich each symbol with LSP definition and reference count
  if (lspClient) {
    for (const sym of symbols) {
      try {
        const def = await lspClient.getDefinition(input.path, sym.lineStart, 0);
        if (def) {
          sym.definedAt = def;
        }
      } catch {
        // LSP failed — skip definition enrichment
      }

      try {
        const refs = await lspClient.getReferences(input.path, sym.lineStart, 0);
        if (refs.length > 0) {
          sym.referencedBy = refs.length;
        }
      } catch {
        // LSP failed — skip reference count
      }
    }
  }

  // Get imports (outgoing import edges)
  const importEdges = await storage.getEdges(file.id, "out", ["imports"]);
  const imports: string[] = [];
  for (const edge of importEdges) {
    const target = await storage.getNode(edge.toId);
    if (target && "path" in target) imports.push((target as any).path);
  }

  // Get importedBy (incoming import edges)
  const importedByEdges = await storage.getEdges(file.id, "in", ["imports"]);
  const importedBy: string[] = [];
  for (const edge of importedByEdges) {
    const source = await storage.getNode(edge.fromId);
    if (source && "path" in source) importedBy.push((source as any).path);
  }

  // Get concepts
  const concepts: Array<{ summary: string; lastUpdatedBy: string }> = [];
  const conceptIds = new Set<string>();
  const allIncoming = await storage.getEdges(file.id, "in", ["describes"]);
  for (const edge of allIncoming) {
    const node = await storage.getNode(edge.fromId);
    if (node && node.type === "concept" && !conceptIds.has(node.id)) {
      conceptIds.add(node.id);
      concepts.push({
        summary: (node as any).summary ?? "",
        lastUpdatedBy: (node as any).lastUpdatedBy ?? "",
      });
    }
  }

  // Also get concepts on symbols
  for (const sym of symbols) {
    const symId = `symbol:${input.path}:${sym.name}`;
    const symIncoming = await storage.getEdges(symId, "in", ["describes"]);
    for (const edge of symIncoming) {
      const node = await storage.getNode(edge.fromId);
      if (node && node.type === "concept" && !conceptIds.has(node.id)) {
        conceptIds.add(node.id);
        concepts.push({
          summary: (node as any).summary ?? "",
          lastUpdatedBy: (node as any).lastUpdatedBy ?? "",
        });
      }
    }
  }

  // Get workflows
  const workflows: Array<{ name: string; description: string; files: string[] }> = [];
  const workflowIds = new Set<string>();
  const fileIncoming = await storage.getEdges(file.id, "in", ["traverses"]);
  for (const edge of fileIncoming) {
    const node = await storage.getNode(edge.fromId);
    if (node && node.type === "workflow" && !workflowIds.has(node.id)) {
      workflowIds.add(node.id);
      workflows.push({
        name: node.name,
        description: (node as any).description ?? "",
        files: (node as any).fileSequence ?? [],
      });
    }
  }

  // Get hotspot data from the file node's data field
  const hotspotData = (file as any).hotspot as { changeCount: number; lastChanged: string } | undefined;

  // Get co-change pairs (outgoing and incoming co_changes edges)
  const coChanges: string[] = [];
  const coChangeEdgesOut = await storage.getEdges(file.id, "out", ["co_changes"]);
  for (const edge of coChangeEdgesOut) {
    const target = await storage.getNode(edge.toId);
    if (target && "path" in target) coChanges.push((target as any).path);
  }
  const coChangeEdgesIn = await storage.getEdges(file.id, "in", ["co_changes"]);
  for (const edge of coChangeEdgesIn) {
    const source = await storage.getNode(edge.fromId);
    if (source && "path" in source) coChanges.push((source as any).path);
  }

  return {
    file: { path: input.path, absolutePath: absPath, name: file.name },
    symbols,
    imports,
    importedBy,
    concepts,
    workflows,
    hotspot: hotspotData,
    coChanges,
  };
}
