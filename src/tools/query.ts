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

export interface QueryResultItem {
  node: SearchResult["node"] & { source_code?: string | null };
  rank: number;
  matchedField: string;
}

export interface QueryOutput {
  results: QueryResultItem[];
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
  let results: SearchResult[];
  if (scope === "all") {
    results = allResults;
  } else if (scope === "files") {
    results = allResults.filter((r) => r.node.type === "file");
  } else if (scope === "symbols") {
    results = allResults.filter((r) => r.node.type === "symbol");
  } else if (scope === "workflows") {
    results = allResults.filter((r) => r.node.type === "workflow");
  } else {
    results = allResults;
  }

  // Enrich symbol results with source code
  const enriched: QueryResultItem[] = results.map((r) => {
    if (projectRoot && r.node.type === "symbol") {
      const sym = r.node as SymbolNode;
      return {
        ...r,
        node: {
          ...r.node,
          source_code: readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd, 30),
        },
      };
    }
    return r;
  });

  return {
    results: enriched,
    total: enriched.length,
  };
}
