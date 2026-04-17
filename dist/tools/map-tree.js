/**
 * Shared tree builder for codemesh_map and codemesh_answer.
 *
 * BFS through calls edges with a visited set, no depth limit.
 * Returns a recursive tree with summary/kind/filePath/id/pagerank on each node.
 */
const MAX_CALLERS_PER_NODE = 8;
export async function buildMapTree(storage, startNodeIds) {
    const visited = new Set();
    const roots = [];
    for (const startId of startNodeIds) {
        if (visited.has(startId))
            continue;
        const node = await buildNode(storage, startId, visited);
        if (node)
            roots.push(node);
    }
    return { nodes: roots, totalSymbols: visited.size };
}
async function buildNode(storage, nodeId, visited, relationship) {
    if (visited.has(nodeId))
        return null;
    visited.add(nodeId);
    const node = await storage.getNode(nodeId);
    if (!node || node.type !== "symbol")
        return null;
    const sym = node;
    // Get outgoing calls edges and build children recursively
    const callEdges = await storage.getEdges(nodeId, "out", ["calls"]);
    const children = [];
    for (const edge of callEdges) {
        const child = await buildNode(storage, edge.toId, visited, edge.type);
        if (child)
            children.push(child);
    }
    // Collect immediate callers (flat, non-recursive) — the inverse of `children`
    const incomingCallEdges = await storage.getEdges(nodeId, "in", ["calls"]);
    const calledBy = [];
    for (const edge of incomingCallEdges.slice(0, MAX_CALLERS_PER_NODE)) {
        const caller = await storage.getNode(edge.fromId);
        if (caller && caller.type === "symbol") {
            const sCaller = caller;
            calledBy.push({
                symbol: sCaller.name,
                id: sCaller.id,
                filePath: sCaller.filePath,
            });
        }
    }
    return {
        symbol: sym.name,
        id: sym.id,
        filePath: sym.filePath,
        kind: sym.kind,
        signature: sym.signature,
        lineStart: sym.lineStart,
        lineEnd: sym.lineEnd,
        summary: sym.summary ?? null,
        ...(relationship ? { relationship } : {}),
        pagerank: sym.pagerankScore ?? null,
        children,
        ...(calledBy.length > 0 ? { calledBy } : {}),
    };
}
//# sourceMappingURL=map-tree.js.map