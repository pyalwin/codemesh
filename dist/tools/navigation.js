/** Deterministic, one-hop navigation over the indexed code graph. */
const RELATIONS = new Set([
    "contains", "calls", "called_by", "imports", "imported_by", "extends",
]);
function describe(node) {
    if (node.type === "file") {
        return { id: node.id, name: node.name, type: "file", path: node.path };
    }
    if (node.type === "symbol") {
        return {
            id: node.id,
            name: node.name,
            type: "symbol",
            path: node.filePath,
            signature: node.signature,
        };
    }
    return { id: node.id, name: node.name, type: node.type };
}
function compare(a, b) {
    // Avoid locale-dependent collation so repeated calls order identically.
    return a.name < b.name ? -1 : a.name > b.name ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}
function symbolChildren(symbols, parent) {
    const prefix = parent ? `${parent}.` : "";
    return symbols.filter((symbol) => {
        if (!symbol.name.startsWith(prefix))
            return false;
        return !symbol.name.slice(prefix.length).includes(".");
    });
}
function immediateFileChildren(paths, parent) {
    const prefix = parent ? `${parent}/` : "";
    const directories = new Set();
    const entries = [];
    for (const path of paths) {
        if (!path.startsWith(prefix))
            continue;
        const rest = path.slice(prefix.length);
        if (!rest || rest.startsWith("/"))
            continue;
        const slash = rest.indexOf("/");
        if (slash < 0) {
            entries.push({ id: `file:${path}`, name: rest, type: "file", path });
        }
        else {
            directories.add(prefix + rest.slice(0, slash));
        }
    }
    for (const path of directories) {
        entries.push({
            id: `dir:${path}`,
            name: path.slice(path.lastIndexOf("/") + 1),
            type: "dir",
            path,
            hasChildren: true,
        });
    }
    return entries;
}
function validateInput(input) {
    const id = input.node ?? "repo";
    const relation = input.relation ?? "contains";
    const limit = input.limit ?? 30;
    const maxBytes = input.maxBytes ?? 8192;
    if (typeof id !== "string" || !id)
        throw new Error("node must be a nonempty ID");
    if (!RELATIONS.has(relation))
        throw new Error(`Unsupported navigation relation: ${String(relation)}`);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
        throw new Error("limit must be an integer between 1 and 100");
    }
    if (input.cursor !== undefined && !/^(0|[1-9]\d*)$/.test(input.cursor)) {
        throw new Error("cursor must be a nonnegative integer offset");
    }
    const offset = input.cursor === undefined ? 0 : Number(input.cursor);
    if (!Number.isSafeInteger(offset))
        throw new Error("cursor is too large");
    if (!Number.isInteger(maxBytes) || maxBytes < 512 || maxBytes > 65536) {
        throw new Error("maxBytes must be an integer between 512 and 65536");
    }
    return { id, relation, limit, offset, maxBytes };
}
/**
 * Expand one hierarchy level or one graph edge type. Navigation returns IDs,
 * names and signatures only; exact code remains behind codemesh_source.
 */
export async function handleNavigation(storage, input) {
    const { id, relation, limit, offset, maxBytes } = validateInput(input);
    const virtual = id === "repo" || id.startsWith("dir:");
    if (virtual && relation !== "contains") {
        throw new Error(`${relation} is only available for indexed file or symbol IDs`);
    }
    let current;
    let entries;
    if (virtual) {
        if (id === "repo") {
            const paths = await storage.queryFilePaths();
            current = { id: "repo", name: "repo", type: "repo" };
            entries = immediateFileChildren(paths, "");
        }
        else {
            const path = id.slice(4);
            if (!path || path.startsWith("/") || path.endsWith("/") || path.includes("//") || path.split("/").some((part) => part === "." || part === "..")) {
                throw new Error(`Invalid directory ID: ${id}`);
            }
            const paths = await storage.queryFilePaths(path);
            if (paths.length === 0) {
                throw new Error(`Navigation node not found: ${id}`);
            }
            current = { id, name: path.slice(path.lastIndexOf("/") + 1), type: "dir", path };
            entries = immediateFileChildren(paths, path);
        }
    }
    else {
        const node = await storage.getNode(id);
        if (!node)
            throw new Error(`Navigation node not found: ${id}`);
        current = describe(node);
        if (relation === "contains") {
            if (node.type === "file") {
                const symbols = (await storage.queryNodesByFilePaths([node.path]));
                entries = symbolChildren(symbols, "").map((symbol) => ({
                    ...describe(symbol),
                    hasChildren: symbols.some((child) => child.name.startsWith(`${symbol.name}.`)),
                }));
            }
            else if (node.type === "symbol") {
                const symbols = (await storage.queryNodesByFilePaths([node.filePath]));
                entries = symbolChildren(symbols, node.name).map((symbol) => ({
                    ...describe(symbol),
                    hasChildren: symbols.some((child) => child.name.startsWith(`${symbol.name}.`)),
                }));
            }
            else {
                entries = [];
            }
        }
        else {
            const incoming = relation === "called_by" || relation === "imported_by";
            const edgeType = relation === "called_by" ? "calls" : relation === "imported_by" ? "imports" : relation;
            const edges = await storage.getEdges(id, incoming ? "in" : "out", [edgeType]);
            const targetIds = [...new Set(edges.map((edge) => incoming ? edge.fromId : edge.toId))];
            const targets = await Promise.all(targetIds.map((targetId) => storage.getNode(targetId)));
            entries = targets.filter((target) => target !== null).map(describe);
        }
    }
    entries.sort(compare);
    const page = entries.slice(offset, offset + limit);
    // Only inspect files on the requested page. A file with no indexed symbols
    // is a leaf, while directories always have children by construction.
    const pageFiles = page.filter((entry) => entry.type === "file").map((entry) => entry.path);
    if (pageFiles.length > 0) {
        const symbols = await storage.queryNodesByFilePaths(pageFiles);
        const nonempty = new Set(symbols.filter((symbol) => symbol.type === "symbol").map((symbol) => symbol.filePath));
        for (const entry of page) {
            if (entry.type === "file")
                entry.hasChildren = nonempty.has(entry.path);
        }
    }
    const output = { node: current, entries: [] };
    const bytes = () => Buffer.byteLength(JSON.stringify(output));
    for (const entry of page) {
        output.entries.push(entry);
        if (offset + output.entries.length < entries.length)
            output.nextCursor = String(offset + output.entries.length);
        else
            delete output.nextCursor;
        if (bytes() > maxBytes) {
            output.entries.pop();
            break;
        }
    }
    if (output.entries.length < page.length) {
        output.truncated = true;
        output.maxBytes = maxBytes;
        output.omitted = { entries: entries.length - offset - output.entries.length };
        // The cursor points at the first unreturned entry, never past a dropped
        // record. A too-large first entry requires a larger budget to make progress.
        do {
            output.nextCursor = String(offset + output.entries.length);
            output.omitted.entries = entries.length - offset - output.entries.length;
            if (!output.entries.length)
                output.hint = "Next complete entry exceeds maxBytes; increase the budget and reuse nextCursor.";
            if (bytes() <= maxBytes)
                break;
        } while (output.entries.pop());
    }
    else if (offset + output.entries.length < entries.length) {
        output.nextCursor = String(offset + output.entries.length);
    }
    else
        delete output.nextCursor;
    if (bytes() > maxBytes)
        throw new Error("Navigation node metadata exceeds maxBytes; increase the budget.");
    return output;
}
//# sourceMappingURL=navigation.js.map