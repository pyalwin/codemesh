/** Revision-aware, exact symbol source retrieval shared by CLI and MCP. */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
function hash(content) {
    return createHash("sha256").update(content).digest("hex");
}
async function resolveSymbol(storage, idOrName) {
    const exact = await storage.getNode(idOrName);
    if (exact) {
        if (exact.type !== "symbol")
            throw new Error(`Symbol not found: ${idOrName}`);
        return exact;
    }
    // A missing canonical ID is never reinterpreted as another symbol's name.
    if (!idOrName || /^(symbol|file|concept|workflow):/.test(idOrName)) {
        throw new Error(`Symbol not found: ${idOrName}`);
    }
    // queryNodes(name) is exact equality, not search/prefix matching. The index
    // stores qualified names; only bare method names also need a suffix lookup.
    const exactNames = await storage.queryNodes({ type: "symbol", name: idOrName });
    const methods = !/[.:/\\]/.test(idOrName)
        ? await storage.queryNodes({ type: "symbol", kind: "method" })
        : [];
    const candidates = [...new Map([...exactNames, ...methods.filter(node => node.type === "symbol" && node.name.endsWith(`.${idOrName}`))].filter((node) => node.type === "symbol")
            .map(node => [node.id, node])).values()];
    if (candidates.length === 1)
        return candidates[0];
    if (candidates.length === 0)
        throw new Error(`Symbol not found: ${idOrName}`);
    candidates.sort((a, b) => a.filePath.localeCompare(b.filePath)
        || a.lineStart - b.lineStart || a.id.localeCompare(b.id));
    const lines = [];
    let bytes = 0;
    for (const candidate of candidates.slice(0, 20)) {
        const line = JSON.stringify({ id: candidate.id, filePath: candidate.filePath,
            lineStart: candidate.lineStart, lineEnd: candidate.lineEnd });
        if (bytes + Buffer.byteLength(line) + 1 > 8 * 1024)
            break;
        lines.push(line);
        bytes += Buffer.byteLength(line) + 1;
    }
    throw new Error(`Ambiguous symbol name: ${candidates.length} matches. Request one exact ID.\n`
        + lines.join("\n")
        + (lines.length < candidates.length ? `\nShowing ${lines.length} of ${candidates.length} candidates; narrow the qualified name or use map for more.` : ""));
}
const AUTO_MAX_LINES = 120;
const AUTO_MAX_BYTES = 12 * 1024;
const AUTO_PREVIEW_HINT = "Bounded navigation preview. Omitted ranges are not source. Use map to select a specific method, then request its source, or request mode=full for the complete exact body.";
/** Render whole lines only: even one oversized line must never be truncated into fake code. */
function renderAutoPreview(sym, lines) {
    const renderSelected = (selected) => {
        const parts = [];
        const omittedRanges = [];
        let next = 0;
        for (const index of [...selected, lines.length]) {
            if (index > next) {
                const start = sym.lineStart + next;
                const end = sym.lineStart + index - 1;
                parts.push(`… [lines ${start}-${end} omitted] …`);
                omittedRanges.push({ start, end });
            }
            if (index < lines.length)
                parts.push(lines[index]);
            next = index + 1;
        }
        return { source: parts.join("\n"), omittedRanges, hint: AUTO_PREVIEW_HINT };
    };
    // Keep the same leading/trailing context as preview, but account for UTF-8 bytes
    // and omission markers before accepting each complete line.
    const candidates = new Set();
    for (let i = 0; i < Math.min(12, lines.length); i++)
        candidates.add(i);
    for (let i = Math.max(0, lines.length - 8); i < lines.length; i++)
        candidates.add(i);
    let selected = [];
    let rendered = renderSelected(selected);
    for (const index of candidates) {
        const proposed = [...selected, index].sort((a, b) => a - b);
        const next = renderSelected(proposed);
        if (Buffer.byteLength(next.source) <= AUTO_MAX_BYTES) {
            selected = proposed;
            rendered = next;
        }
    }
    return rendered;
}
function renderSource(sym, lines, mode) {
    if (mode === "fold") {
        return { source: [sym.signature, sym.summary].filter(Boolean).join("\n") };
    }
    if (mode === "auto") {
        const source = lines.join("\n");
        return lines.length <= AUTO_MAX_LINES && Buffer.byteLength(source) <= AUTO_MAX_BYTES
            ? { source }
            : renderAutoPreview(sym, lines);
    }
    if (mode === "full" || lines.length <= 24) {
        return { source: lines.join("\n") };
    }
    const first = lines.slice(0, 12);
    const last = lines.slice(-8);
    const start = sym.lineStart + first.length;
    const end = sym.lineEnd - last.length;
    return {
        source: `${first.join("\n")}\n… [lines ${start}-${end} omitted] …\n${last.join("\n")}`,
        omittedRanges: [{ start, end }],
    };
}
export async function handleSource(storage, input, projectRoot) {
    const sym = await resolveSymbol(storage, input.id);
    const mode = input.mode ?? "full";
    const absolutePath = join(projectRoot, sym.filePath);
    const fileNode = await storage.getNode(`file:${sym.filePath}`);
    const base = {
        symbol: sym.name,
        id: sym.id,
        filePath: sym.filePath,
        absolutePath,
        kind: sym.kind,
        signature: sym.signature,
        summary: sym.summary ?? null,
        lineStart: sym.lineStart,
        lineEnd: sym.lineEnd,
        mode,
        indexRevision: fileNode?.lastIndexedAt ?? null,
    };
    let bytes;
    try {
        bytes = readFileSync(absolutePath);
    }
    catch {
        return { ...base, status: "deleted", sourceHash: null, bodyHash: null, source: null,
            hint: "Source file is unavailable. Run codemesh index to refresh the graph." };
    }
    if (!fileNode || hash(bytes) !== fileNode.hash) {
        return { ...base, status: "stale", sourceHash: null, bodyHash: null, source: null,
            hint: "Indexed source spans are stale. Run codemesh index, then request this symbol again." };
    }
    const allLines = bytes.toString("utf-8").split("\n");
    if (sym.lineStart < 1 || sym.lineEnd < sym.lineStart || sym.lineEnd > allLines.length) {
        return { ...base, status: "stale", sourceHash: null, bodyHash: null, source: null,
            hint: "Indexed symbol span is invalid. Run codemesh index." };
    }
    const lines = allLines.slice(sym.lineStart - 1, sym.lineEnd);
    const bodyHash = hash(lines.join("\n"));
    if (sym.bodyHash && sym.bodyHash !== bodyHash) {
        return { ...base, status: "stale", sourceHash: null, bodyHash: null, source: null,
            hint: "Symbol body differs from the index. Run codemesh index." };
    }
    const rendered = renderSource(sym, lines, mode);
    const sourceHash = hash(rendered.source);
    if (!input.force && input.ifHash === sourceHash) {
        await storage.incrementMetric?.("source_bytes_avoided", Buffer.byteLength(rendered.source));
        await storage.incrementMetric?.("conditional_hits", 1);
        return { ...base, status: "unchanged", sourceHash, bodyHash, source: null,
            ...(mode === "auto" && rendered.omittedRanges ? { omittedRanges: rendered.omittedRanges } : {}),
            hint: ["The requested view is byte-identical. Repeat with force=true to rehydrate it.", rendered.hint].filter(Boolean).join(" ") };
    }
    if (input.sessionId && input.contextEpoch && storage.recordDelivery) {
        await storage.recordDelivery({ sessionId: input.sessionId, contextEpoch: input.contextEpoch,
            nodeId: sym.id, mode, sourceHash });
    }
    await storage.incrementMetric?.("source_bytes_delivered", Buffer.byteLength(rendered.source));
    return { ...base, status: "fresh", sourceHash, bodyHash, source: rendered.source,
        ...(rendered.omittedRanges ? { omittedRanges: rendered.omittedRanges } : {}),
        ...(rendered.hint ? { hint: rendered.hint } : {}) };
}
//# sourceMappingURL=source.js.map