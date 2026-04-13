/**
 * codemesh_source — Return source code for a single symbol by ID.
 */

import { join } from "node:path";
import type { StorageBackend } from "../graph/storage.js";
import type { SymbolNode } from "../graph/types.js";
import { readSourceLines } from "./source-reader.js";

export interface SourceInput {
  id: string;
}

export interface SourceOutput {
  symbol: string;
  id: string;
  filePath: string;
  absolutePath: string;
  kind: string;
  signature: string;
  summary: string | null;
  lineStart: number;
  lineEnd: number;
  source: string;
}

export async function handleSource(
  storage: StorageBackend,
  input: SourceInput,
  projectRoot: string,
): Promise<SourceOutput> {
  const node = await storage.getNode(input.id);
  if (!node || node.type !== "symbol") {
    throw new Error(`Symbol not found: ${input.id}`);
  }

  const sym = node as SymbolNode;
  const source = readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd);

  if (source === null) {
    throw new Error(`Could not read source for ${sym.name} at ${sym.filePath}:${sym.lineStart}-${sym.lineEnd}`);
  }

  return {
    symbol: sym.name,
    id: sym.id,
    filePath: sym.filePath,
    absolutePath: join(projectRoot, sym.filePath),
    kind: sym.kind,
    signature: sym.signature,
    summary: sym.summary ?? null,
    lineStart: sym.lineStart,
    lineEnd: sym.lineEnd,
    source,
  };
}
