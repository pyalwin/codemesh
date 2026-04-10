/**
 * codemesh_query — Search the knowledge graph using FTS5.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { SearchResult } from "../graph/types.js";

export interface QueryInput {
  query: string;
  scope?: "files" | "symbols" | "workflows" | "all";
}

export interface QueryOutput {
  results: SearchResult[];
  total: number;
}

export async function handleQuery(
  storage: StorageBackend,
  input: QueryInput,
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

  return {
    results,
    total: results.length,
  };
}
