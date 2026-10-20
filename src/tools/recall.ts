/** Retrieve attributed cross-session knowledge without treating it as code fact. */

import type { StorageBackend } from "../graph/storage.js";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ConceptNode, FileNode, SymbolNode, WorkflowNode } from "../graph/types.js";

export interface RecallInput {
  id?: string;
  path?: string;
  symbol?: string;
  query?: string;
  limit?: number;
}

export interface RecalledNote {
  id: string;
  kind: "concept" | "workflow";
  summary: string;
  status: "hypothesis" | "verified" | "stale";
  targets: string[];
  author?: string;
  updatedAt: string;
}

export async function handleRecall(storage: StorageBackend, input: RecallInput, projectRoot?: string): Promise<{ notes: RecalledNote[] }> {
  if (!input.id && !input.path && !input.query) throw new Error("id, path, or query is required");
  const limit = input.limit ?? 20;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error("limit must be 1..100");
  const candidates = new Map<string, ConceptNode | WorkflowNode>();
  if (input.id) {
    const node = await storage.getNode(input.id);
    if (node?.type === "concept" || node?.type === "workflow") candidates.set(node.id, node);
  }
  const liveFreshness = new Map<string, boolean>();
  const fileIsFresh = async (path: string): Promise<boolean> => {
    if (!projectRoot) return true;
    const cached = liveFreshness.get(path);
    if (cached !== undefined) return cached;
    const indexed = await storage.getNode(`file:${path}`);
    let fresh = false;
    if (indexed?.type === "file") {
      try {
        const liveHash = createHash("sha256").update(readFileSync(join(projectRoot, path))).digest("hex");
        fresh = liveHash === indexed.hash;
      } catch { /* deleted or unreadable */ }
    }
    liveFreshness.set(path, fresh);
    return fresh;
  };

  if (input.path) {
    const targetId = input.symbol ? `symbol:${input.path}:${input.symbol}` : `file:${input.path}`;
    for (const edge of await storage.getEdges(targetId, "in", ["describes", "traverses"])) {
      const node = await storage.getNode(edge.fromId);
      if (node?.type === "concept" || node?.type === "workflow") candidates.set(node.id, node);
    }
    // Workflow links point from workflow to file, while concepts point to it.
  }
  if (input.query) {
    for (const result of await storage.search(input.query, "all")) {
      const node = result.node;
      if (node.type === "concept" || node.type === "workflow") candidates.set(node.id, node);
      if (candidates.size >= limit * 3) break;
    }
  }

  const notes: RecalledNote[] = [];
  for (const node of candidates.values()) {
    if (notes.length >= limit) break;
    if (node.type === "concept") {
      const concept = node as ConceptNode;
      const edges = await storage.getEdges(node.id, "out", ["describes"]);
      const targets = [...new Set([concept.targetId, ...edges.map((edge) => edge.toId)].filter((x): x is string => Boolean(x)))];
      let stale = concept.stale;
      if (concept.targetId && concept.targetHash) {
        const current = await storage.getNode(concept.targetId);
        const currentHash = current?.type === "file" ? (current as FileNode).hash
          : current?.type === "symbol" ? (current as SymbolNode).bodyHash : null;
        stale = stale || currentHash !== concept.targetHash;
        if (current?.type === "file") stale = stale || !(await fileIsFresh(current.path));
        if (current?.type === "symbol") stale = stale || !(await fileIsFresh(current.filePath));
      }
      notes.push({
        id: concept.id, kind: "concept", summary: concept.summary,
        status: stale ? "stale" : concept.status ?? "hypothesis",
        targets, author: concept.lastUpdatedBy, updatedAt: concept.updatedAt,
      });
    } else {
      const workflow = node as WorkflowNode;
      let stale = workflow.stale;
      for (const file of workflow.fileSequence) {
        const current = await storage.getNode(`file:${file}`);
        if (!current || current.type !== "file" ||
            (workflow.fileHashes?.[file] && current.hash !== workflow.fileHashes[file]) ||
            !(await fileIsFresh(file))) {
          stale = true;
          break;
        }
      }
      notes.push({
        id: workflow.id, kind: "workflow", summary: workflow.description,
        status: stale ? "stale" : workflow.status ?? "hypothesis",
        targets: workflow.fileSequence.map((file) => `file:${file}`),
        updatedAt: workflow.updatedAt,
      });
    }
  }
  return { notes };
}
