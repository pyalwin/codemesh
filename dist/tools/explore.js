/**
 * codemesh_explore — The "mega-tool". Takes a task description, searches the graph,
 * traverses ALL connected nodes to completion, and returns full source code for
 * every symbol in the connected subgraph. One call, complete picture.
 */
import { join } from "node:path";
import { readSourceLines } from "./source-reader.js";
// ── Implementation ───────────────────────────────────────────────────
export async function handleExplore(storage, input, projectRoot) {
    const maxDepth = input.maxDepth ?? 10;
    const maxSymbols = input.maxSymbols ?? 200;
    const includeSource = input.includeSource !== false; // default true
    // Phase 1: Search the graph for entry points
    const searchResults = await storage.search(input.task, "all");
    const entryNodeIds = [];
    const entryNames = [];
    for (const result of searchResults.slice(0, 15)) {
        entryNodeIds.push(result.node.id);
        entryNames.push(result.node.name);
        // If entry is a file, also add its contained symbols as entry points
        if (result.node.type === "file") {
            const containsEdges = await storage.getEdges(result.node.id, "out", ["contains"]);
            for (const edge of containsEdges) {
                if (!entryNodeIds.includes(edge.toId)) {
                    entryNodeIds.push(edge.toId);
                }
            }
        }
    }
    // Phase 2: BFS traverse ALL edges from entry points until leaf or visited
    const visitedNodes = new Set();
    const symbolMap = new Map();
    const fileSet = new Set();
    let actualMaxDepth = 0;
    const queue = entryNodeIds.map((id) => ({ nodeId: id, depth: 0 }));
    while (queue.length > 0 && symbolMap.size < maxSymbols) {
        const { nodeId, depth } = queue.shift();
        if (visitedNodes.has(nodeId) || depth > maxDepth)
            continue;
        visitedNodes.add(nodeId);
        actualMaxDepth = Math.max(actualMaxDepth, depth);
        const node = await storage.getNode(nodeId);
        if (!node)
            continue;
        if (node.type === "symbol") {
            const sym = node;
            fileSet.add(sym.filePath);
            // Get outgoing calls
            const outEdges = await storage.getEdges(nodeId, "out", ["calls"]);
            const callNames = [];
            for (const edge of outEdges) {
                const target = await storage.getNode(edge.toId);
                if (target) {
                    callNames.push(target.name);
                    // Continue traversal through calls
                    if (!visitedNodes.has(edge.toId)) {
                        queue.push({ nodeId: edge.toId, depth: depth + 1 });
                    }
                }
            }
            // Get incoming callers
            const inEdges = await storage.getEdges(nodeId, "in", ["calls"]);
            const callerNames = [];
            for (const edge of inEdges) {
                const caller = await storage.getNode(edge.fromId);
                if (caller) {
                    callerNames.push(caller.name);
                    // Also traverse callers (upstream context)
                    if (!visitedNodes.has(edge.fromId)) {
                        queue.push({ nodeId: edge.fromId, depth: depth + 1 });
                    }
                }
            }
            symbolMap.set(nodeId, {
                name: sym.name,
                kind: sym.kind,
                filePath: sym.filePath,
                absolutePath: join(projectRoot, sym.filePath),
                lineStart: sym.lineStart,
                lineEnd: sym.lineEnd,
                signature: sym.signature,
                source: includeSource
                    ? readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd, 100)
                    : null,
                calls: callNames,
                calledBy: callerNames,
            });
        }
        else if (node.type === "file") {
            const file = node;
            fileSet.add(file.path);
            // Traverse into contained symbols
            const containsEdges = await storage.getEdges(nodeId, "out", ["contains"]);
            for (const edge of containsEdges) {
                if (!visitedNodes.has(edge.toId)) {
                    queue.push({ nodeId: edge.toId, depth: depth + 1 });
                }
            }
            // Traverse imports (connected files)
            const importEdges = await storage.getEdges(nodeId, "out", ["imports"]);
            for (const edge of importEdges) {
                if (!visitedNodes.has(edge.toId)) {
                    queue.push({ nodeId: edge.toId, depth: depth + 1 });
                }
            }
        }
    }
    // Phase 3: Assemble file information
    const fileDetails = [];
    for (const filePath of fileSet) {
        const fileId = `file:${filePath}`;
        const fileNode = await storage.getNode(fileId);
        if (!fileNode)
            continue;
        // Symbols in this file
        const containsEdges = await storage.getEdges(fileId, "out", ["contains"]);
        const symbolNames = [];
        for (const edge of containsEdges) {
            const sym = await storage.getNode(edge.toId);
            if (sym)
                symbolNames.push(sym.name);
        }
        // Imports
        const importEdges = await storage.getEdges(fileId, "out", ["imports"]);
        const imports = [];
        for (const edge of importEdges) {
            const target = await storage.getNode(edge.toId);
            if (target && target.type === "file")
                imports.push(target.path);
        }
        // Imported by
        const importedByEdges = await storage.getEdges(fileId, "in", ["imports"]);
        const importedBy = [];
        for (const edge of importedByEdges) {
            const source = await storage.getNode(edge.fromId);
            if (source && source.type === "file")
                importedBy.push(source.path);
        }
        // Concepts
        const conceptEdges = await storage.getEdges(fileId, "in", ["describes"]);
        const concepts = [];
        for (const edge of conceptEdges) {
            const concept = await storage.getNode(edge.fromId);
            if (concept && concept.type === "concept") {
                concepts.push(concept.summary);
            }
        }
        fileDetails.push({
            path: filePath,
            absolutePath: join(projectRoot, filePath),
            symbols: symbolNames,
            imports,
            importedBy,
            concepts,
        });
    }
    // Phase 4: Collect relevant workflows
    const workflows = [];
    for (const filePath of fileSet) {
        const fileId = `file:${filePath}`;
        const traverseEdges = await storage.getEdges(fileId, "in", ["traverses"]);
        for (const edge of traverseEdges) {
            const wfNode = await storage.getNode(edge.fromId);
            if (wfNode && wfNode.type === "workflow") {
                const wf = wfNode;
                // Avoid duplicates
                if (!workflows.some((w) => w.name === wf.name)) {
                    workflows.push({
                        name: wf.name,
                        description: wf.description,
                        files: wf.fileSequence,
                    });
                }
            }
        }
    }
    return {
        task: input.task,
        entryPoints: entryNames,
        symbols: Array.from(symbolMap.values()),
        files: fileDetails,
        workflows,
        stats: {
            symbolCount: symbolMap.size,
            fileCount: fileDetails.length,
            traversalDepth: actualMaxDepth,
            searchHits: searchResults.length,
        },
    };
}
//# sourceMappingURL=explore.js.map