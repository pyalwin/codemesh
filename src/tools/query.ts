/**
 * codemesh_query — Search the knowledge graph using FTS5 + trigram + semantic search.
 *
 * Returns metadata only — signatures, line numbers, file paths.
 * Results ranked by PageRank when available.
 * Semantic search (LanceDB) supplements FTS5 when embeddings exist.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { SearchResult, SymbolNode } from "../graph/types.js";
import { semanticSearch } from "../indexer/embeddings.js";

export interface QueryInput {
  query: string;
  scope?: "files" | "symbols" | "workflows" | "all";
}

export interface QueryOutput {
  results: SearchResult[];
  semanticResults?: Array<{ id: string; name: string; filePath: string; score: number }>;
  total: number;
}

export async function handleQuery(
  storage: StorageBackend,
  input: QueryInput,
  projectRoot?: string,
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

  // Sort by PageRank when available (higher = more important)
  results.sort((a, b) => {
    const aPr = (a.node as any).pagerankScore ?? 0;
    const bPr = (b.node as any).pagerankScore ?? 0;
    if (bPr !== aPr) return bPr - aPr;
    return a.rank - b.rank; // fallback to FTS rank
  });

  // Supplement with semantic search if embeddings exist
  let semanticResults: QueryOutput["semanticResults"];
  if (projectRoot) {
    try {
      const semResults = await semanticSearch(projectRoot, input.query, 10);
      if (semResults.length > 0) {
        // Filter out results already in FTS5 results
        const ftsIds = new Set(results.map((r) => r.node.id));
        semanticResults = semResults.filter((r) => !ftsIds.has(r.id));
      }
    } catch {
      // No embeddings or LanceDB not available — skip silently
    }
  }

  return {
    results,
    semanticResults: semanticResults && semanticResults.length > 0 ? semanticResults : undefined,
    total: results.length + (semanticResults?.length ?? 0),
  };
}
