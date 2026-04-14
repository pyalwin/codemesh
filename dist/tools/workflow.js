/**
 * codemesh_workflow — Create a workflow node linking files in sequence.
 */
export async function handleWorkflow(storage, input) {
    const now = new Date().toISOString();
    // Create a stable workflow ID from the name
    const workflowId = `workflow:${input.name.toLowerCase().replace(/\s+/g, "-")}`;
    // Create the workflow node
    const workflowNode = {
        id: workflowId,
        type: "workflow",
        source: "agent",
        name: input.name,
        description: input.description,
        fileSequence: input.files,
        lastWalkedAt: now,
        stale: false,
        createdAt: now,
        updatedAt: now,
    };
    await storage.upsertNode(workflowNode);
    // Create traverses edges to each file with position data
    for (let i = 0; i < input.files.length; i++) {
        const filePath = input.files[i];
        const fileId = `file:${filePath}`;
        // Only create edge if the file node exists
        const fileNode = await storage.getNode(fileId);
        if (!fileNode)
            continue;
        const traversesEdge = {
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
//# sourceMappingURL=workflow.js.map