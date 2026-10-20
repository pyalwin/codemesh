/**
 * Shared tree builder for codemesh_map and codemesh_answer.
 *
 * Bounded traversal through calls edges with a visited set.
 * Returns a recursive tree with summary/kind/filePath/id/pagerank on each node.
 */
const MAX_CALLERS_PER_NODE = 8;
const MAX_DEPTH = 3;
const MAX_NODES = 25;
export async function buildMapTree(storage, startNodeIds) {
    const state = { visited: new Set(), truncated: false };
    const roots = [];
    for (const startId of startNodeIds) {
        if (state.visited.has(startId))
            continue;
        const node = await buildNode(storage, startId, state, 0);
        if (node)
            roots.push(node);
    }
    return { nodes: roots, totalSymbols: state.visited.size, truncated: state.truncated };
}
async function buildNode(storage, nodeId, state, depth, relationship) {
    if (state.visited.has(nodeId))
        return null;
    if (state.visited.size >= MAX_NODES) {
        state.truncated = true;
        return null;
    }
    state.visited.add(nodeId);
    const node = await storage.getNode(nodeId);
    if (!node || node.type !== "symbol")
        return null;
    const sym = node;
    // Get outgoing calls edges and build children recursively
    const callEdges = (await storage.getEdges(nodeId, "out", ["calls"]))
        .sort((a, b) => a.toId.localeCompare(b.toId));
    const children = [];
    if (depth >= MAX_DEPTH) {
        if (callEdges.length > 0)
            state.truncated = true;
    }
    else {
        for (const edge of callEdges) {
            const child = await buildNode(storage, edge.toId, state, depth + 1, edge.type);
            if (child)
                children.push(child);
        }
    }
    // Collect immediate callers (flat, non-recursive) — the inverse of `children`
    const incomingCallEdges = await storage.getEdges(nodeId, "in", ["calls"]);
    const calledBy = [];
    for (const edge of incomingCallEdges.sort((a, b) => a.fromId.localeCompare(b.fromId)).slice(0, MAX_CALLERS_PER_NODE)) {
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