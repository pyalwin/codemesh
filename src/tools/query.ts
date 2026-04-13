/**
 * codemesh_query — Search the knowledge graph using FTS5 + trigram + semantic search.
 *
 * Returns metadata only — signatures, line numbers, file paths.
 * Results ranked by PageRank when available.
 * Semantic search (LanceDB) supplements FTS5 when embeddings exist.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { SearchResult, SymbolNode, FileNode } from "../graph/types.js";
import { semanticSearch } from "../indexer/embeddings.js";

const MAX_RESULTS = 20;

export interface QueryInput {
  query: string;
  scope?: "files" | "symbols" | "workflows" | "all";
}

interface SlimResult {
  id: string;
  name: string;
  type: string;
  filePath?: string;
  kind?: string;
  summary?: string;
  matchedField: string;
  pagerankScore?: number;
}

export interface QueryOutput {
  results: SlimResult[];
  semanticResults?: Array<{ id: string; name: string; filePath: string; score: number }>;
  total: number;
}

function slimNode(result: SearchResult): SlimResult {
  const node = result.node;
  const base: SlimResult = {
    id: node.id,
    name: node.name,
    type: node.type,
    matchedField: result.matchedField,
  };
  if (node.type === "symbol") {
    const sym = node as SymbolNode;
    base.filePath = sym.filePath;
    base.kind = sym.kind;
    base.summary = sym.summary;
    base.pagerankScore = sym.pagerankScore;
  } else if (node.type === "file") {
    const file = node as FileNode;
    base.filePath = file.path;
    base.pagerankScore = file.pagerankScore;
  }
  return base;
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
    const aPr = (a.node as SymbolNode).pagerankScore ?? 0;
    const bPr = (b.node as SymbolNode).pagerankScore ?? 0;
    if (bPr !== aPr) return bPr - aPr;
    return a.rank - b.rank; // fallback to FTS rank
  });

  const totalMatches = results.length;
  results = results.slice(0, MAX_RESULTS);

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
    results: results.map(slimNode),
    semanticResults: semanticResults && semanticResults.length > 0 ? semanticResults : undefined,
    total: totalMatches + (semanticResults?.length ?? 0),
  };
}
