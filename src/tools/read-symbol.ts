/**
 * codemesh read — Read source code for a specific symbol by name.
 *
 * Returns just the source body of a single symbol, avoiding full-file reads.
 * Uses the same multi-strategy symbol finder as trace.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { SymbolNode } from "../graph/types.js";
import { findSymbol } from "./trace.js";
import { readSourceLines } from "./source-reader.js";

export interface ReadSymbolInput {
  symbol: string;
}

export interface ReadSymbolOutput {
  symbol: string;
  filePath: string;
  kind: string;
  signature: string;
  lines: string;
  source: string | null;
}

export async function handleReadSymbol(
  storage: StorageBackend,
  input: ReadSymbolInput,
  projectRoot: string,
): Promise<ReadSymbolOutput | { error: string }> {
  const nodes = await findSymbol(storage, input.symbol);

  if (nodes.length === 0) {
    return { error: `Symbol '${input.symbol}' not found in the knowledge graph.` };
  }

  const node = nodes[0];
  if (node.type !== "symbol") {
    return { error: `'${input.symbol}' resolved to a ${node.type} node, not a symbol.` };
  }

  const sym = node as SymbolNode;
  const source = readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd);

  return {
    symbol: sym.name,
    filePath: sym.filePath,
    kind: sym.kind,
    signature: sym.signature,
    lines: `${sym.lineStart}-${sym.lineEnd}`,
    source,
  };
}
