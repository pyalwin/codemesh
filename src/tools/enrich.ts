/**
 * codemesh_enrich — Create a concept node describing a file or symbol.
 */

import type { StorageBackend } from "../graph/storage.js";
import { createHash } from "node:crypto";
import type { ConceptNode, GraphEdge, FileNode, SymbolNode } from "../graph/types.js";

export interface EnrichInput {
  path: string;
  symbol?: string;
  summary: string;
  related_files?: string[];
  sessionId: string;
  status?: "hypothesis" | "verified";
}

export interface EnrichOutput {
  nodeId: string;
  success: boolean;
}

export async function handleEnrich(
  storage: StorageBackend,
  input: EnrichInput,
): Promise<EnrichOutput> {
  const now = new Date().toISOString();

  // Determine the target node ID
  const targetId = input.symbol
    ? `symbol:${input.path}:${input.symbol}`
    : `file:${input.path}`;

  // Verify the target exists
  const targetNode = await storage.getNode(targetId);
  if (!targetNode) {
    return { nodeId: "", success: false };
  }

  // Exact repeated notes consolidate. Differing notes remain side by side so
  // contradictions are visible instead of being silently overwritten.
  const existingEdges = await storage.getEdges(targetId, "in", ["describes"]);
  let existing: ConceptNode | null = null;
  for (const edge of existingEdges) {
    const candidate = await storage.getNode(edge.fromId);
    if (candidate?.type === "concept" && candidate.summary.trim() === input.summary.trim()) {
      existing = candidate;
      break;
    }
  }
  const summaryHash = createHash("sha256").update(input.summary.trim()).digest("hex").slice(0, 16);
  const conceptId = existing?.id ?? `concept:${targetId}:${summaryHash}`;
  const targetHash = targetNode.type === "file"
    ? (targetNode as FileNode).hash
    : (targetNode as SymbolNode).bodyHash;

  // Create the concept node
  const conceptNode: ConceptNode = {
    id: conceptId,
    type: "concept",
    source: "agent",
    name: input.summary.slice(0, 80),
    summary: input.summary,
    lastUpdatedBy: input.sessionId,
    stale: false,
    targetId,
    targetHash,
    status: input.status ?? existing?.status ?? "hypothesis",
    history: existing && existing.lastUpdatedBy !== input.sessionId
      ? [...(existing.history ?? []), {
          sessionId: existing.lastUpdatedBy,
          status: existing.status ?? "hypothesis",
          at: existing.updatedAt,
        }].slice(-20)
      : existing?.history,
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
  };

  await storage.upsertNode(conceptNode);

  // Create describes edge: concept -> target
  const describesEdge: GraphEdge = {
    id: `edge:describes:${conceptId}:${targetId}`,
    type: "describes",
    source: "agent",
    fromId: conceptId,
    toId: targetId,
    createdAt: now,
  };

  await storage.upsertEdge(describesEdge);

  // If related_files specified, create related_to edges to their existing concepts
  if (input.related_files && input.related_files.length > 0) {
    for (const relatedPath of input.related_files) {
      const relatedFileId = `file:${relatedPath}`;
      const relatedFile = await storage.getNode(relatedFileId);
      if (!relatedFile) continue;

      // Find existing concepts that describe the related file or its symbols
      const relatedIncoming = await storage.getEdges(relatedFileId, "in", ["describes"]);
      for (const edge of relatedIncoming) {
        const relatedConcept = await storage.getNode(edge.fromId);
        if (relatedConcept && relatedConcept.type === "concept") {
          const relatedEdge: GraphEdge = {
            id: `edge:related_to:${conceptId}:${relatedConcept.id}`,
            type: "related_to",
            source: "agent",
            fromId: conceptId,
            toId: relatedConcept.id,
            createdAt: now,
          };
          await storage.upsertEdge(relatedEdge);
        }
      }
    }
  }

  return { nodeId: conceptId, success: true };
}
