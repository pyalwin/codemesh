/**
 * codemesh_workflow — Create a workflow node linking files in sequence.
 */

import type { StorageBackend } from "../graph/storage.js";
import type { WorkflowNode, GraphEdge } from "../graph/types.js";

export interface WorkflowInput {
  name: string;
  description: string;
  files: string[];
}

export interface WorkflowOutput {
  workflowId: string;
  success: boolean;
}

export async function handleWorkflow(
  storage: StorageBackend,
  input: WorkflowInput,
): Promise<WorkflowOutput> {
  const now = new Date().toISOString();

  // Create a stable workflow ID from the name
  const workflowId = `workflow:${input.name.toLowerCase().replace(/\s+/g, "-")}`;
  const existing = await storage.getNode(workflowId);

  const fileHashes: Record<string, string> = {};
  for (const file of input.files) {
    const node = await storage.getNode(`file:${file}`);
    if (node?.type === "file") fileHashes[file] = node.hash;
  }

  // Create the workflow node
  const workflowNode: WorkflowNode = {
    id: workflowId,
    type: "workflow",
    source: "agent",
    name: input.name,
    description: input.description,
    fileSequence: input.files,
    lastWalkedAt: now,
    stale: false,
    fileHashes,
    status: existing?.type === "workflow" ? existing.status ?? "hypothesis" : "hypothesis",
    history: existing?.type === "workflow"
      ? [...(existing.history ?? []), {
          description: existing.description,
          fileSequence: existing.fileSequence,
          at: existing.updatedAt,
        }].slice(-20)
      : undefined,
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
  };

  // Replace prior traverses links when an existing workflow is rewritten.
  await storage.deleteEdgesByNode(workflowId);
  await storage.upsertNode(workflowNode);

  // Create traverses edges to each file with position data
  for (let i = 0; i < input.files.length; i++) {
    const filePath = input.files[i];
    const fileId = `file:${filePath}`;

    // Only create edge if the file node exists
    const fileNode = await storage.getNode(fileId);
    if (!fileNode) continue;

    const traversesEdge: GraphEdge = {
      id: `edge:traverses:${workflowId}:${fileId}`,
      type: "traverses",
      source: "agent",
      fromId: workflowId,
      toId: fileId,
      data: { position: i, total: input.files.length },
      createdAt: now,
    };

    await storage.upsertEdge(traversesEdge);
  }

  return { workflowId, success: true };
}
