/**
 * PageRank scoring via graphology.
 *
 * Builds an in-memory directed graph from SQLite nodes + edges,
 * computes PageRank centrality, and returns scores keyed by node ID.
 * Higher scores indicate more "important" nodes — ones that many
 * other nodes point to (via imports, calls, contains, etc.).
 */
import GraphModule from "graphology";
import pagerankModule from "graphology-metrics/centrality/pagerank.js";
// Handle CJS/ESM interop — these modules may expose .default at runtime
const Graph = GraphModule.default ?? GraphModule;
const pagerank = pagerankModule.default ?? pagerankModule;
const MAX_NODES_FOR_PAGERANK = 20000;
export async function computePageRank(storage) {
    // 1. Query all file and symbol nodes from storage
    const fileNodes = await storage.queryNodes({ type: "file" });
    const symbolNodes = await storage.queryNodes({ type: "symbol" });
    const allNodes = [...fileNodes, ...symbolNodes];
    // Guard: skip PageRank for very large repos to avoid stack overflow
    if (allNodes.length > MAX_NODES_FOR_PAGERANK) {
        console.log(`  PageRank skipped: ${allNodes.length} nodes exceeds ${MAX_NODES_FOR_PAGERANK} limit`);
        return new Map();
    }
    const graph = new Graph();
    // 2. Add nodes
    for (const node of allNodes) {
        graph.addNode(node.id);
    }
    // 3. Add edges (calls, imports, contains, co_changes, etc.)
    for (const node of allNodes) {
        const edges = await storage.getEdges(node.id, "out");
        for (const edge of edges) {
            if (graph.hasNode(edge.toId) && !graph.hasEdge(edge.fromId, edge.toId)) {
                graph.addEdge(edge.fromId, edge.toId);
            }
        }
    }
    // 4. Compute PageRank
    const ranks = pagerank(graph, { getEdgeWeight: null });
    // 5. Return as Map
    const result = new Map();
    graph.forEachNode((nodeId) => {
        result.set(nodeId, ranks[nodeId] || 0);
    });
    return result;
}
//# sourceMappingURL=pagerank.js.map