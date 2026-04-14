/**
 * codemesh_trace — Trace a call chain from a symbol and return source code.
 */
import { readSourceLines } from "./source-reader.js";
/**
 * Multi-strategy symbol finder.
 * Handles: exact name, Class.method splitting, fuzzy FTS search.
 * Returns matching nodes, best match first.
 */
export async function findSymbol(storage, query) {
    // Strategy 1: exact name match
    const exact = await storage.queryNodes({ type: "symbol", name: query });
    if (exact.length > 0)
        return exact;
    // Strategy 2: if "Class.method" format, search for the method name
    // and filter by symbols in files containing the class
    if (query.includes(".")) {
        const parts = query.split(".");
        const className = parts[0];
        const methodName = parts[parts.length - 1];
        // Find files that contain the class
        const classNodes = await storage.queryNodes({ type: "symbol", name: className });
        const classFiles = new Set(classNodes
            .filter((n) => n.type === "symbol")
            .map((n) => n.filePath));
        if (classFiles.size > 0) {
            // Find method symbols in those files
            const methodNodes = await storage.queryNodes({ type: "symbol", name: methodName });
            const filtered = methodNodes.filter((n) => n.type === "symbol" && classFiles.has(n.filePath));
            if (filtered.length > 0)
                return filtered;
        }
        // Also try the full "Class.method" as stored name (some languages do this)
        const dotName = await storage.queryNodes({ type: "symbol", name: `${className}.${methodName}` });
        if (dotName.length > 0)
            return dotName;
    }
    // Strategy 3: FTS search
    const searchResults = await storage.search(query, "symbols");
    if (searchResults.length > 0) {
        return searchResults.slice(0, 3).map((r) => r.node);
    }
    return [];
}
export async function handleTrace(storage, input, projectRoot) {
    const maxDepth = input.depth ?? 3;
    const steps = [];
    const visited = new Set();
    // Find the starting symbol with multi-strategy matching
    let startNodes = await findSymbol(storage, input.symbol);
    if (startNodes.length === 0) {
        return { startSymbol: input.symbol, steps: [], depth: maxDepth };
    }
    // BFS through call chain
    const queue = [
        { nodeId: startNodes[0].id, currentDepth: 0 },
    ];
    while (queue.length > 0) {
        const { nodeId, currentDepth } = queue.shift();
        if (visited.has(nodeId) || currentDepth > maxDepth)
            continue;
        visited.add(nodeId);
        const node = await storage.getNode(nodeId);
        if (!node || node.type !== "symbol")
            continue;
        const sym = node;
        // Get outgoing calls edges
        const callEdges = await storage.getEdges(nodeId, "out", ["calls"]);
        const calleeNames = [];
        for (const edge of callEdges) {
            const callee = await storage.getNode(edge.toId);
            if (callee) {
                calleeNames.push(callee.name);
                if (currentDepth + 1 <= maxDepth) {
                    queue.push({ nodeId: edge.toId, currentDepth: currentDepth + 1 });
                }
            }
        }
        const step = {
            symbol: sym.name,
            filePath: sym.filePath,
            kind: sym.kind,
            signature: sym.signature,
            source: input.compact ? null : readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd),
            calls: calleeNames,
            pagerankScore: sym.pagerankScore,
        };
        if (input.compact && sym.lineStart != null && sym.lineEnd != null) {
            step.lines = `${sym.lineStart}-${sym.lineEnd}`;
        }
        steps.push(step);
    }
    // Sort steps by PageRank — most important symbols first
    steps.sort((a, b) => (b.pagerankScore ?? 0) - (a.pagerankScore ?? 0));
    return { startSymbol: input.symbol, steps, depth: maxDepth };
}
//# sourceMappingURL=trace.js.map