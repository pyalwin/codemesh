/**
 * codemesh_answer — One-call context assembly.
 *
 * Takes a natural language question, searches the graph, follows call chains,
 * and assembles everything into one structured response. The agent gets a
 * complete context package in ONE call.
 */
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { buildMapTree } from "./map-tree.js";
// How much centrality (PageRank) should boost retrieval rank.
// pagerankScore in this codebase ranges 0–0.02; a value of 50 means a max-pagerank
// symbol gets ~2x boost, while a baseline symbol (pr ≈ 0.001) gets ~5% boost.
const PAGERANK_BOOST = 50;
const STOPWORDS = new Set([
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "do", "does", "did", "doing", "done",
    "have", "has", "had", "having",
    "how", "what", "where", "when", "why", "which", "who", "whom", "whose",
    "this", "that", "these", "those",
    "and", "or", "not", "nor", "for", "but",
    "of", "to", "in", "on", "at", "by", "with", "from", "as", "about",
    "i", "we", "you", "they", "it",
    "can", "could", "should", "would", "will", "shall", "may", "might", "must",
    "if", "then", "than", "else",
]);
function filterQueryTokens(question) {
    const filtered = question
        .split(/\s+/)
        .filter((t) => t.length > 1 && !STOPWORDS.has(t.toLowerCase()))
        .join(" ");
    return filtered || question;
}
/**
 * Extract a snippet of source code for a symbol.
 *
 * - If the symbol fits within maxLines, returns the full symbol.
 * - If the symbol is longer than maxLines (truncated case): when queryTokens
 *   is provided, slides a maxLines-window across the symbol and picks the
 *   window with the highest query-token density. Otherwise falls back to the
 *   first maxLines.
 *
 * Returns the actual window's start/end line numbers (absolute in the file)
 * so callers can reference the snippet precisely.
 */
function readSnippet(projectRoot, filePath, lineStart, lineEnd, queryTokens = [], maxLines = 30, expectedFileHash) {
    try {
        const abs = join(projectRoot, filePath);
        const bytes = readFileSync(abs);
        if (!expectedFileHash || createHash("sha256").update(bytes).digest("hex") !== expectedFileHash) {
            return { text: "", truncated: true, windowStart: lineStart, windowEnd: lineEnd, stale: true };
        }
        const content = bytes.toString("utf-8");
        const lines = content.split("\n");
        const desiredLines = lineEnd - lineStart + 1;
        if (desiredLines <= maxLines) {
            const actualEnd = Math.min(lineEnd, lines.length);
            return {
                text: lines.slice(lineStart - 1, actualEnd).join("\n"),
                truncated: false,
                windowStart: lineStart,
                windowEnd: actualEnd,
            };
        }
        // Truncated — pick the best window.
        const symbolLines = lines.slice(lineStart - 1, lineEnd);
        if (queryTokens.length === 0) {
            return {
                text: symbolLines.slice(0, maxLines).join("\n"),
                truncated: true,
                windowStart: lineStart,
                windowEnd: lineStart + maxLines - 1,
            };
        }
        const lowerTokens = queryTokens.map((t) => t.toLowerCase());
        const perLineScore = symbolLines.map((line) => {
            const lower = line.toLowerCase();
            let s = 0;
            for (const t of lowerTokens) {
                if (lower.includes(t))
                    s++;
            }
            return s;
        });
        // Sliding window to find max token density
        let bestStart = 0;
        let windowScore = 0;
        for (let i = 0; i < maxLines && i < perLineScore.length; i++) {
            windowScore += perLineScore[i];
        }
        let bestScore = windowScore;
        for (let start = 1; start + maxLines <= perLineScore.length; start++) {
            windowScore -= perLineScore[start - 1];
            windowScore += perLineScore[start + maxLines - 1];
            if (windowScore > bestScore) {
                bestScore = windowScore;
                bestStart = start;
            }
        }
        // If nothing matched, fall back to the prologue
        if (bestScore === 0) {
            return {
                text: symbolLines.slice(0, maxLines).join("\n"),
                truncated: true,
                windowStart: lineStart,
                windowEnd: lineStart + maxLines - 1,
            };
        }
        const winText = symbolLines.slice(bestStart, bestStart + maxLines).join("\n");
        return {
            text: winText,
            truncated: true,
            windowStart: lineStart + bestStart,
            windowEnd: lineStart + bestStart + maxLines - 1,
        };
    }
    catch {
        return { text: "", truncated: true, windowStart: lineStart, windowEnd: lineEnd, stale: true };
    }
}
// Symbols with logic (methods/functions/classes) are more informative for
// "how does X work" queries than consts/types/interfaces. Small multiplicative
// boost to surface them higher in topSymbols and suggestedReads rankings.
// Tuned: compared 1.2 vs 1.5 on a 15-question A/B. 1.5 wins on comprehension
// (Q7, Q9) by surfacing methods strongly; 1.2 costs 6 tools + 20s NEW-side.
const KIND_BOOST = {
    method: 1.5,
    function: 1.5,
    class: 1.2,
    const: 1.0,
    interface: 1.0,
    type: 1.0,
    enum: 1.0,
};
export async function handleAnswer(storage, input, projectRoot) {
    // Step 1: Search the SQLite graph with the question (FTS5 + trigram fallback)
    // Filter natural-language stopwords so OR-split BM25 focuses on content tokens.
    const ftsQuery = filterQueryTokens(input.question);
    const searchResults = await storage.search(ftsQuery, "all");
    // Step 1b: IDF-weighted rescoring. Expand candidate pool to top 30, then
    // reweight by which query tokens each result actually matched (and how rare
    // those tokens are across the corpus). This promotes results that hit many
    // rare tokens (e.g., `codemesh_answer`) over results that hit only common
    // ones (`response`, `tool`). BM25 already applies per-term IDF internally,
    // but re-weighting at the candidate set gives us control over the blend.
    const candidates = searchResults.slice(0, 30);
    const queryTokens = ftsQuery
        .split(/\s+/)
        .map((t) => t.toLowerCase())
        .filter((t) => t.length > 1);
    // Per-token document frequency — one FTS5 query per token. For typical
    // 3–5 token queries this adds ~5 fast SELECTs (<50ms total on this corpus).
    const tokenMatchIds = [];
    for (const token of queryTokens) {
        const perToken = await storage.search(token, "all");
        tokenMatchIds.push(new Set(perToken.map((r) => r.node.id)));
    }
    // Approximate corpus size. Only the relative IDFs matter for ranking, so
    // an estimate is fine; the absolute scale cancels out in the normalized score.
    const CORPUS_SIZE = 1000;
    const tokenIdf = queryTokens.map((_, i) => {
        const df = Math.max(1, tokenMatchIds[i].size);
        return Math.log(CORPUS_SIZE / df);
    });
    const sumAllIdf = tokenIdf.reduce((a, b) => a + b, 0);
    // Re-score each candidate by fraction-of-total-idf-matched, multiplied by
    // the original BM25 rank. Results matching only common tokens get heavily
    // downweighted; results matching rare + multiple tokens get boosted.
    if (sumAllIdf > 0) {
        for (const result of candidates) {
            let idfHit = 0;
            for (let i = 0; i < queryTokens.length; i++) {
                if (tokenMatchIds[i].has(result.node.id))
                    idfHit += tokenIdf[i];
            }
            // Don't zero out results with no token hits (they might have matched via
            // trigram or stemming quirks); give them a small floor.
            const fraction = idfHit / sumAllIdf;
            result.rank = result.rank * Math.max(0.1, fraction);
        }
        candidates.sort((a, b) => b.rank - a.rank);
    }
    const topResults = candidates.slice(0, 10);
    // Collect unique files and symbols from results
    const fileMap = new Map();
    const symbolResults = [];
    for (const result of topResults) {
        const node = result.node;
        if (node.type === "file") {
            const fileNode = node;
            if (!fileMap.has(fileNode.path)) {
                fileMap.set(fileNode.path, {
                    node: fileNode,
                    why: `Matched via ${result.matchedField} search (rank ${result.rank.toFixed(2)})`,
                    symbolCount: 0,
                    topSymbols: [],
                });
            }
        }
        else if (node.type === "symbol") {
            const sym = node;
            // Raw BM25 rank already incorporates IDF — common tokens like "response"
            // naturally score lower than rare identifiers. Don't normalize to 1.0.
            symbolResults.push({
                sym,
                rank: result.rank,
                matchedField: result.matchedField,
            });
            // Also track the file this symbol belongs to
            if (!fileMap.has(sym.filePath)) {
                const fileId = `file:${sym.filePath}`;
                const fileNode = await storage.getNode(fileId);
                if (fileNode && fileNode.type === "file") {
                    fileMap.set(sym.filePath, {
                        node: fileNode,
                        why: `Contains matching symbol '${sym.name}'`,
                        symbolCount: 0,
                        topSymbols: [],
                    });
                }
            }
        }
        else if (node.type === "concept") {
            const edges = await storage.getEdges(node.id, "out", ["describes"]);
            for (const edge of edges) {
                const target = await storage.getNode(edge.toId);
                if (target?.type === "symbol") {
                    if (!symbolResults.some((entry) => entry.sym.id === target.id)) {
                        symbolResults.push({ sym: target, rank: result.rank, matchedField: "saved note" });
                    }
                    const fileNode = await storage.getNode(`file:${target.filePath}`);
                    if (fileNode?.type === "file" && !fileMap.has(target.filePath)) {
                        fileMap.set(target.filePath, {
                            node: fileNode, why: `Matched saved note for '${target.name}'`,
                            symbolCount: 0, topSymbols: [],
                        });
                    }
                }
                else if (target?.type === "file" && !fileMap.has(target.path)) {
                    fileMap.set(target.path, {
                        node: target, why: "Matched saved note", symbolCount: 0, topSymbols: [],
                    });
                }
            }
        }
    }
    // Step 2: For each file, count symbols and pick top ones by PageRank
    // We only store counts + top symbols — NOT full inventories
    const matchedSymbolNames = new Set(symbolResults.map(r => r.sym.name));
    for (const [filePath, entry] of fileMap) {
        const containsEdges = await storage.getEdges(entry.node.id, "out", ["contains"]);
        entry.symbolCount = containsEdges.length;
        // Load all symbols, rank by: matched > high pagerank > rest
        const allSyms = [];
        for (const edge of containsEdges) {
            const node = await storage.getNode(edge.toId);
            if (!node || node.type !== "symbol")
                continue;
            const sym = node;
            // Matched symbols get a big boost, then sort by pagerank.
            // Multiply by kind boost so methods/functions outrank peripheral consts
            // for same-PageRank ties (important for "how does X work" queries).
            const matchBoost = matchedSymbolNames.has(sym.name) ? 1000 : 0;
            const kindBoost = KIND_BOOST[sym.kind] ?? 1.0;
            allSyms.push({
                name: sym.name,
                kind: sym.kind,
                signature: sym.signature,
                lineStart: sym.lineStart,
                lineEnd: sym.lineEnd,
                summary: sym.summary,
                score: (matchBoost + (sym.pagerankScore ?? 0)) * kindBoost,
            });
        }
        allSyms.sort((a, b) => b.score - a.score);
        entry.topSymbols = allSyms
            .slice(0, 5)
            .map(({ name, kind, signature, lineStart, lineEnd, summary }) => ({
            name,
            kind,
            signature,
            lineStart,
            lineEnd,
            summary,
        }));
        // Follow imports to discover more relevant files (lightweight — no symbol enumeration)
        const importEdges = await storage.getEdges(entry.node.id, "out", ["imports"]);
        for (const edge of importEdges) {
            const target = await storage.getNode(edge.toId);
            if (target && target.type === "file" && !fileMap.has(target.path)) {
                const importedFile = target;
                if (fileMap.size < 15) {
                    const importedContains = await storage.getEdges(importedFile.id, "out", ["contains"]);
                    fileMap.set(importedFile.path, {
                        node: importedFile,
                        why: `Imported by '${filePath}'`,
                        symbolCount: importedContains.length,
                        topSymbols: [],
                    });
                }
            }
        }
    }
    // Step 3: Build symbol map — summary-enriched call graph tree
    const startNodeIds = symbolResults.map(r => r.sym.id);
    const { nodes: symbolMap } = await buildMapTree(storage, startNodeIds);
    // Step 4: Collect concepts for matched files AND matched symbols
    // (enrich can target either — earlier we only checked file-level describes edges)
    const concepts = [];
    const seenConcepts = new Set();
    for (const [filePath, entry] of fileMap) {
        const incomingEdges = await storage.getEdges(entry.node.id, "in", ["describes"]);
        for (const edge of incomingEdges) {
            if (seenConcepts.has(edge.fromId))
                continue;
            seenConcepts.add(edge.fromId);
            const conceptNode = await storage.getNode(edge.fromId);
            if (conceptNode && conceptNode.type === "concept") {
                concepts.push({
                    summary: conceptNode.summary ?? "",
                    file: filePath,
                    stale: conceptNode.stale,
                });
            }
        }
    }
    for (const { sym } of symbolResults) {
        const incomingEdges = await storage.getEdges(sym.id, "in", ["describes"]);
        for (const edge of incomingEdges) {
            if (seenConcepts.has(edge.fromId))
                continue;
            seenConcepts.add(edge.fromId);
            const conceptNode = await storage.getNode(edge.fromId);
            if (conceptNode && conceptNode.type === "concept") {
                concepts.push({
                    summary: conceptNode.summary ?? "",
                    file: sym.filePath,
                    symbol: sym.name,
                    stale: conceptNode.stale,
                });
            }
        }
    }
    // Step 5: Collect workflows that traverse matched files
    const workflows = [];
    const seenWorkflows = new Set();
    for (const [, entry] of fileMap) {
        const incomingEdges = await storage.getEdges(entry.node.id, "in", ["traverses"]);
        for (const edge of incomingEdges) {
            if (seenWorkflows.has(edge.fromId))
                continue;
            seenWorkflows.add(edge.fromId);
            const wfNode = await storage.getNode(edge.fromId);
            if (wfNode && wfNode.type === "workflow") {
                workflows.push({
                    name: wfNode.name,
                    description: wfNode.description ?? "",
                    files: wfNode.fileSequence ?? [],
                });
            }
        }
    }
    // Step 6: Build relevantFiles output (with hotspot + co-change data + pagerank)
    const relevantFiles = [];
    for (const [filePath, entry] of fileMap) {
        const hotspotData = entry.node.hotspot;
        const pagerankScore = entry.node.pagerankScore;
        // Collect co-change pairs with confidence + count (the edge carries both)
        const coChanges = [];
        const coChangeEdgesOut = await storage.getEdges(entry.node.id, "out", ["co_changes"]);
        for (const edge of coChangeEdgesOut) {
            const target = await storage.getNode(edge.toId);
            if (target && "path" in target) {
                coChanges.push({
                    path: target.path,
                    confidence: edge.data?.confidence,
                    count: edge.data?.coChangeCount,
                });
            }
        }
        const coChangeEdgesIn = await storage.getEdges(entry.node.id, "in", ["co_changes"]);
        for (const edge of coChangeEdgesIn) {
            const source = await storage.getNode(edge.fromId);
            if (source && "path" in source) {
                coChanges.push({
                    path: source.path,
                    confidence: edge.data?.confidence,
                    count: edge.data?.coChangeCount,
                });
            }
        }
        relevantFiles.push({
            path: filePath,
            absolutePath: join(projectRoot, filePath),
            why: entry.why,
            symbolCount: entry.symbolCount,
            topSymbols: entry.topSymbols,
            hotspot: hotspotData,
            coChanges,
            pagerankScore,
        });
    }
    // Rank relevantFiles by (best matching symbol's rank) × PageRank boost.
    // Central files with weak query matches lose to peripheral files with strong
    // matches (unless their centrality is high enough to compensate).
    const fileMaxRank = new Map();
    for (const r of symbolResults) {
        const current = fileMaxRank.get(r.sym.filePath) ?? 0;
        if (r.rank > current)
            fileMaxRank.set(r.sym.filePath, r.rank);
    }
    relevantFiles.sort((a, b) => {
        const ar = (fileMaxRank.get(a.path) ?? 0) * (1 + PAGERANK_BOOST * (a.pagerankScore ?? 0));
        const br = (fileMaxRank.get(b.path) ?? 0) * (1 + PAGERANK_BOOST * (b.pagerankScore ?? 0));
        if (br !== ar)
            return br - ar;
        // Tiebreak: pure PageRank (central files first when relevance is identical)
        return (b.pagerankScore ?? 0) - (a.pagerankScore ?? 0);
    });
    // Step 7: Build suggestedReads — top 5 symbols by blended score:
    //   blended = retrievalRank × (1 + PAGERANK_BOOST × symbolPageRank)
    // This multiplicatively combines relevance with graph centrality, so central
    // symbols (like computePageRank) outrank tangential semantic matches.
    const suggestedReads = [];
    const rankedSymbols = symbolResults
        .map((r) => ({
        ...r,
        blendedScore: r.rank *
            (1 + PAGERANK_BOOST * (r.sym.pagerankScore ?? 0)) *
            (KIND_BOOST[r.sym.kind] ?? 1.0),
    }))
        .sort((a, b) => b.blendedScore - a.blendedScore)
        .slice(0, 5);
    for (const { sym, matchedField } of rankedSymbols) {
        const { text: snippet, truncated, windowStart, windowEnd, stale } = readSnippet(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd, queryTokens, 30, fileMap.get(sym.filePath)?.node.hash);
        suggestedReads.push({
            file: sym.filePath,
            absolutePath: join(projectRoot, sym.filePath),
            lines: `${windowStart}-${windowEnd}`,
            symbolRange: truncated ? `${sym.lineStart}-${sym.lineEnd}` : undefined,
            reason: stale
                ? `${sym.name} (${sym.kind}) — index stale; run codemesh index before reading`
                : `${sym.name} (${sym.kind}) — matched via ${matchedField}`,
            signature: sym.signature,
            snippet,
            truncated,
            summary: sym.summary,
            stale,
        });
    }
    // If we have fewer than 5 suggested reads, fill from remaining symbol results
    if (suggestedReads.length < 5) {
        const suggestedIds = new Set(rankedSymbols.map(r => r.sym.id));
        for (const { sym, matchedField } of symbolResults) {
            if (suggestedReads.length >= 5)
                break;
            if (suggestedIds.has(sym.id))
                continue;
            suggestedIds.add(sym.id);
            {
                const { text: snippet, truncated, windowStart, windowEnd, stale } = readSnippet(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd, queryTokens, 30, fileMap.get(sym.filePath)?.node.hash);
                suggestedReads.push({
                    file: sym.filePath,
                    absolutePath: join(projectRoot, sym.filePath),
                    lines: `${windowStart}-${windowEnd}`,
                    symbolRange: truncated ? `${sym.lineStart}-${sym.lineEnd}` : undefined,
                    reason: stale
                        ? `${sym.name} (${sym.kind}) — index stale; run codemesh index before reading`
                        : `${sym.name} (${sym.kind}) — matched via ${matchedField}`,
                    signature: sym.signature,
                    snippet,
                    truncated,
                    summary: sym.summary,
                    stale,
                });
            }
        }
    }
    return {
        question: input.question,
        relevantFiles,
        symbolMap,
        concepts,
        workflows,
        suggestedReads,
    };
}
//# sourceMappingURL=answer.js.map