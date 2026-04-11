/**
 * codemesh_query — Search the knowledge graph using FTS5.
 *
 * Returns metadata only — signatures, line numbers, file paths.
 * No source code. The agent decides what to Read based on the metadata.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { SearchResult, SymbolNode } from "../graph/types.js";

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
  _projectRoot?: string,
): Promise<QueryOutput> {
  const scope = input.scope ?? "all";
  const allResults = await storage.search(input.query, scope);

  // Filter by scope since the storage layer may not do it
  let results = allResults;
  if (scope === "files") {
    results = allResults.filter((r) => r.node.type === "file");
  } else if (scope === "symbols") {
    results = allResults.filter((r) => r.node.type === "symbol");
  } else if (scope === "workflows") {
    results = allResults.filter((r) => r.node.type === "workflow");
  }

  return {
    results,
    total: results.length,
  };
}
