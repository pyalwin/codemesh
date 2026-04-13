/**
 * codemesh_map — Map the call graph from a query or symbol.
 *
 * Returns a summary-enriched tree of symbols with no source code.
 * Use codemesh_source to read the code of specific symbols.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { SymbolNode } from "../graph/types.js";
import { buildMapTree, type MapNode } from "./map-tree.js";
import { findSymbol } from "./trace.js";
import { semanticSearch } from "../indexer/embeddings.js";

export interface MapInput {
  query: string;
  symbol?: string;
}

export interface MapOutput {
  startingPoints: MapNode[];
  totalSymbols: number;
}

export async function handleMap(
  storage: StorageBackend,
  input: MapInput,
  projectRoot: string,
): Promise<MapOutput> {
  let startNodeIds: string[] = [];

  if (input.symbol) {
    // Direct symbol lookup
    const nodes = await findSymbol(storage, input.symbol);
    startNodeIds = nodes.slice(0, 3).map(n => n.id);
  } else {
    // Semantic + FTS5 search to find starting points
    const ftsResults = await storage.search(input.query, "symbols");
    const ftsIds = ftsResults.slice(0, 5).map(r => r.node.id);

    let semanticIds: string[] = [];
    try {
      const semResults = await semanticSearch(projectRoot, input.query, 5);
      semanticIds = semResults
        .filter(r => !new Set(ftsIds).has(r.id))
        .map(r => r.id);
    } catch {
      // Semantic search unavailable — continue with FTS5 only
    }

    // Merge and deduplicate, FTS first
    const seen = new Set<string>();
    for (const id of [...ftsIds, ...semanticIds]) {
      if (!seen.has(id)) {
        seen.add(id);
        startNodeIds.push(id);
      }
    }

    // Limit starting points
    startNodeIds = startNodeIds.slice(0, 5);
  }

  if (startNodeIds.length === 0) {
    return { startingPoints: [], totalSymbols: 0 };
  }

  const { nodes, totalSymbols } = await buildMapTree(storage, startNodeIds);

  return {
    startingPoints: nodes,
    totalSymbols,
  };
}
