/**
 * codemesh_query — Search the knowledge graph using FTS5.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { SearchResult, SymbolNode } from "../graph/types.js";
import { readSourceLines } from "./source-reader.js";

export interface QueryInput {
  query: string;
  scope?: "files" | "symbols" | "workflows" | "all";
}

export interface EnrichedSearchResult extends SearchResult {
  source_code?: string | null;
  containedSymbols?: string[];
}

export interface QueryOutput {
  results: EnrichedSearchResult[];
  total: number;
}

export async function handleQuery(
  storage: StorageBackend,
  input: QueryInput,
  projectRoot?: string,
): Promise<QueryOutput> {
  const scope = input.scope ?? "all";

  const allResults = await storage.search(input.query, scope);

  // Filter by scope if not "all"
  let filtered: SearchResult[];
  if (scope === "all") {
    filtered = allResults;
  } else if (scope === "files") {
    filtered = allResults.filter((r) => r.node.type === "file");
  } else if (scope === "symbols") {
    filtered = allResults.filter((r) => r.node.type === "symbol");
  } else if (scope === "workflows") {
    filtered = allResults.filter((r) => r.node.type === "workflow");
  } else {
    filtered = allResults;
  }

  // Enrich results with source code
  const results: EnrichedSearchResult[] = [];
  for (const r of filtered) {
    const enriched: EnrichedSearchResult = { ...r };

    if (projectRoot && r.node.type === "symbol") {
      const sym = r.node as SymbolNode;
      enriched.source_code = readSourceLines(
        projectRoot,
        sym.filePath,
        sym.lineStart,
        sym.lineEnd,
        30,
      );
    } else if (r.node.type === "file") {
      // List contained symbols for file nodes
      const containsEdges = await storage.getEdges(r.node.id, "out", ["contains"]);
      const symbolNames: string[] = [];
      for (const edge of containsEdges) {
        const node = await storage.getNode(edge.toId);
        if (node) symbolNames.push(node.name);
      }
      if (symbolNames.length > 0) {
        enriched.containedSymbols = symbolNames;
      }
    }

    results.push(enriched);
  }

  return {
    results,
    total: results.length,
  };
}
