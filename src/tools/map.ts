/**
 * codemesh_map — Navigate indexed code or map a call graph from a query.
 *
 * Without a query or symbol, returns one bounded, deterministic navigation step.
 * Legacy query/symbol inputs still return a summary-enriched call tree.
 * Use codemesh_source to read exact symbol code.
 */

import type { StorageBackend } from "../graph/storage.js";
import { buildMapTree, type MapNode } from "./map-tree.js";
import { findSymbol } from "./trace.js";
import { handleNavigation, type NavigationInput, type NavigationOutput } from "./navigation.js";
import { rankedQuery } from "./query.js";

export interface MapInput extends NavigationInput {
  query?: string;
  symbol?: string;
}

export interface MapOutput {
  startingPoints: MapNode[];
  totalSymbols: number;
  truncated?: boolean;
}

export async function handleMap(
  storage: StorageBackend,
  input: MapInput,
  projectRoot: string,
): Promise<MapOutput | NavigationOutput> {
  const legacy = input.query !== undefined || input.symbol !== undefined;
  if (!legacy) {
    return handleNavigation(storage, input);
  }
  if (input.node !== undefined || input.relation !== undefined || input.limit !== undefined || input.cursor !== undefined || input.maxBytes !== undefined) {
    throw new Error("query/symbol cannot be combined with node/relation/limit/cursor/maxBytes");
  }
  if (input.symbol !== undefined && !input.symbol.trim()) {
    throw new Error("symbol must not be empty");
  }
  if (input.query !== undefined && !input.query.trim() && !input.symbol) {
    throw new Error("query must not be empty");
  }
  if (!input.symbol && !input.query) {
    throw new Error("query or symbol is required for legacy map search");
  }

  let startNodeIds: string[] = [];

  if (input.symbol) {
    // Direct symbol lookup
    const nodes = await findSymbol(storage, input.symbol);
    startNodeIds = nodes.slice(0, 3).map(n => n.id);
  } else {
    // Share search ranking so exact identifiers remain the first graph seeds.
    const ftsResults = await rankedQuery(storage, { query: input.query!, scope: "symbols" });
    const ftsIds = ftsResults.slice(0, 5).map(r => r.node.id);

    // Deduplicate FTS results in stable order.
    const seen = new Set<string>();
    for (const id of ftsIds) {
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

  const { nodes, totalSymbols, truncated } = await buildMapTree(storage, startNodeIds);

  return {
    startingPoints: nodes,
    totalSymbols,
    truncated,
  };
}
