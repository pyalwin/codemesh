/**
 * codemesh_query — Search the SQLite knowledge graph using FTS5 + trigram.
 *
 * Returns metadata only — signatures, line numbers, file paths.
 * Exact identifiers precede lexical matches, followed by trigram fallbacks.
 * Query relevance ranks each stage; PageRank breaks relevance ties by default.
 * Exact matches can opt into a weak path-depth tie-break before PageRank.
 */
const MAX_RESULTS = 20;
const DEFAULT_MAX_BYTES = 8 * 1024;
function slimNode(result) {
    const node = result.node;
    const base = {
        id: node.id,
        name: node.name,
        type: node.type,
        matchedField: result.matchedField,
    };
    if (node.type === "symbol") {
        const sym = node;
        base.filePath = sym.filePath;
        base.kind = sym.kind;
        base.signature = sym.signature;
        base.lineStart = sym.lineStart;
        base.lineEnd = sym.lineEnd;
        base.summary = sym.summary;
        base.pagerankScore = sym.pagerankScore;
    }
    else if (node.type === "file") {
        const file = node;
        base.filePath = file.path;
        base.pagerankScore = file.pagerankScore;
    }
    else if (node.type === "concept") {
        const concept = node;
        base.summary = concept.summary;
        base.filePath = concept.targetId?.startsWith("file:")
            ? concept.targetId.slice(5)
            : concept.targetId?.startsWith("symbol:")
                ? concept.targetId.slice(7).split(":")[0]
                : undefined;
    }
    else if (node.type === "workflow") {
        base.summary = node.description;
    }
    return base;
}
function normalizedPath(path) {
    if (path === undefined)
        return undefined;
    if (typeof path !== "string")
        throw new Error("path must be a project-relative file or directory");
    const value = path.replace(/^(\.\/)+/, "").replace(/\/$/, "");
    if (!value || value.startsWith("/") || value.includes("\\") || value.includes("\0")
        || /^[A-Za-z]:/.test(value) || value.split("/").some(part => !part || part === "." || part === "..")) {
        throw new Error("path must be a project-relative file or directory without parent traversal");
    }
    return value;
}
function nodePaths(node) {
    if (node.type === "symbol")
        return [node.filePath];
    if (node.type === "file")
        return [node.path];
    if (node.type === "workflow")
        return node.fileSequence;
    if (node.targetId?.startsWith("file:"))
        return [node.targetId.slice(5)];
    if (node.targetId?.startsWith("symbol:"))
        return [node.targetId.slice(7).split(":")[0]];
    return [];
}
function terms(text) {
    return [...new Set(text.replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [])];
}
function coverage(node, queryTerms) {
    const detail = node.type === "symbol" ? `${node.signature} ${node.summary ?? ""}`
        : node.type === "concept" ? node.summary : node.type === "workflow" ? node.description : "";
    const words = new Set(terms(`${node.name} ${nodePaths(node).join(" ")} ${detail}`));
    return queryTerms.filter(term => words.has(term)).length;
}
function sourcePathDepth(node) {
    const path = node.type === "symbol" ? node.filePath : node.type === "file" ? node.path : undefined;
    return path === undefined ? Number.POSITIVE_INFINITY : path.split("/").length - 1;
}
/** Shared deterministic relevance order for search pages and graph starting points. */
export async function rankedQuery(storage, input) {
    const scope = input.scope ?? "all";
    if (!["all", "files", "symbols", "workflows"].includes(scope))
        throw new Error("Invalid search scope");
    if (typeof input.query !== "string")
        throw new Error("query must be a string");
    const exactTieBreak = input.exactTieBreak ?? "pagerank";
    if (exactTieBreak !== "pagerank" && exactTieBreak !== "path-depth") {
        throw new Error("exactTieBreak must be pagerank or path-depth");
    }
    const path = normalizedPath(input.path);
    const query = input.query.trim();
    if (!query)
        return [];
    const matches = (node) => (scope === "all" || node.type === (scope === "files" ? "file" : scope === "symbols" ? "symbol" : "workflow"))
        && (path === undefined || nodePaths(node).some(value => value === path || value.startsWith(`${path}/`)));
    const exact = [];
    const exactIds = new Set();
    const addExact = (node, matchedField = "exact") => {
        if (node && matches(node) && !exactIds.has(node.id)) {
            exactIds.add(node.id);
            exact.push({ node, rank: Number.POSITIVE_INFINITY, matchedField });
        }
    };
    if (query.startsWith("file:") || query.startsWith("symbol:"))
        addExact(await storage.getNode(query));
    if (query.includes("/") || query.includes("."))
        addExact(await storage.getNode(`file:${query}`));
    if (scope === "all" || scope === "symbols") {
        for (const node of await storage.queryNodes({ type: "symbol", name: query, pathPrefix: path }))
            addExact(node);
        // Qualified declarations keep their full IDs and names; a leaf lookup never
        // turns test_check or checkHelper into an exact match for check.
        if (!/[.\s/:]/.test(query)) {
            for (const node of await storage.queryNodes({ type: "symbol", leafName: query, pathPrefix: path })) {
                addExact(node, node.name === query ? "exact" : "exact_leaf");
            }
        }
    }
    // FTS combines words with OR; repeating a word must not multiply its weight.
    // Keep the original query above for exact identifiers and symbol names.
    const lexicalQuery = [...new Set(query.toLowerCase().split(/\s+/))].join(" ");
    const allResults = [
        ...exact,
        ...(await storage.search(lexicalQuery, scope, { path })).filter((result) => !exactIds.has(result.node.id)),
    ];
    const results = allResults.filter(result => matches(result.node));
    const queryTerms = terms(query);
    const coverageById = new Map(results.map(result => [result.node.id, coverage(result.node, queryTerms)]));
    const tier = (result) => result.matchedField === "exact" ? 0
        : result.matchedField === "exact_leaf" ? 1 : result.matchedField === "trigram" ? 3 : 2;
    // Storage normalizes BM25 so higher is better. Trigram overlap ratios use
    // a different scale, so treat them as fallback candidates rather than
    // comparing them numerically with lexical relevance.
    results.sort((a, b) => {
        if (tier(a) !== tier(b))
            return tier(a) - tier(b);
        if (tier(a) >= 2 && coverageById.get(a.node.id) !== coverageById.get(b.node.id)) {
            return coverageById.get(b.node.id) - coverageById.get(a.node.id);
        }
        if (a.rank !== b.rank)
            return b.rank - a.rank;
        // A shallower declaration is only a weak structural hint for equally exact
        // matches. It does not identify a canonical implementation or lifecycle.
        // Explicit IDs/scopes and stronger match tiers retain their existing order.
        if (exactTieBreak === "path-depth" && tier(a) < 2) {
            const aDepth = sourcePathDepth(a.node);
            const bDepth = sourcePathDepth(b.node);
            if (aDepth !== bDepth)
                return aDepth - bDepth;
        }
        const aPr = a.node.pagerankScore ?? 0;
        const bPr = b.node.pagerankScore ?? 0;
        if (bPr !== aPr)
            return bPr - aPr;
        return a.node.id < b.node.id ? -1 : a.node.id > b.node.id ? 1 : 0;
    });
    return results;
}
export async function handleQuery(storage, input, _projectRoot) {
    const limit = input.limit ?? MAX_RESULTS;
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_RESULTS) {
        throw new Error(`limit must be an integer from 1 to ${MAX_RESULTS}`);
    }
    const maxBytes = input.maxBytes ?? DEFAULT_MAX_BYTES;
    if (!Number.isInteger(maxBytes) || maxBytes < 512 || maxBytes > 64 * 1024) {
        throw new Error("maxBytes must be an integer from 512 to 65536");
    }
    const ranked = await rankedQuery(storage, input);
    const output = { results: [], total: ranked.length };
    // Reserve the entire omission envelope before admitting any record. Removing
    // the envelope for complete output only makes it smaller; IDs are never cut.
    if (ranked.length)
        Object.assign(output, { truncated: true, omitted: ranked.length, limits: { maxBytes, limit } });
    for (const result of ranked.slice(0, limit)) {
        output.results.push(slimNode(result));
        output.omitted = ranked.length - output.results.length;
        if (Buffer.byteLength(JSON.stringify(output)) > maxBytes) {
            output.results.pop();
            output.omitted = ranked.length - output.results.length;
            break;
        }
    }
    if (output.results.length === ranked.length) {
        delete output.truncated;
        delete output.omitted;
        delete output.limits;
    }
    return output;
}
//# sourceMappingURL=query.js.map