/** Conservative adapter for observations produced by native agent tools. */
import { createHash } from "node:crypto";
import { readFileSync, realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
// Leading whitespace would make a path heading ambiguous with an indented
// match line, so that uncommon filename shape stays on the passthrough path.
const RG_LINE = /^([^\s:\r\n][^:\r\n]*):([1-9]\d*):(.*)$/;
/** Fold repeated rg paths while preserving every matched line and its order. */
export function foldNativeSearch(output) {
    if (!output || output.includes("\r") || !output.endsWith("\n")) {
        return { output, transformed: false, reason: "unsupported_format" };
    }
    const lines = output.slice(0, -1).split("\n");
    if (lines.length < 2)
        return { output, transformed: false, reason: "too_small" };
    const parsed = lines.map((line) => RG_LINE.exec(line));
    if (parsed.some((match) => !match)) {
        return { output, transformed: false, reason: "unsupported_format" };
    }
    const folded = [];
    let priorPath;
    for (const match of parsed) {
        const path = match[1];
        if (path !== priorPath) {
            folded.push(`${path}:`);
            priorPath = path;
        }
        folded.push(`  ${match[2]}:${match[3]}`);
    }
    const candidate = folded.join("\n") + "\n";
    return candidate.length < output.length
        ? { output: candidate, transformed: true, reason: "lossless_rg_fold" }
        : { output, transformed: false, reason: "no_savings" };
}
export async function outlineNativeRead(storage, projectRoot, path, output, intent) {
    const fallback = (reason) => ({ output, transformed: false, reason });
    if (intent !== "navigate")
        return fallback("exact_read_requested");
    if (!path || isAbsolute(path))
        return fallback("unsupported_path");
    const root = realpathSync(projectRoot);
    let file;
    try {
        file = realpathSync(resolve(root, path));
    }
    catch {
        return fallback("file_unavailable");
    }
    const rel = relative(root, file);
    if (!rel || rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel)) {
        return fallback("outside_project");
    }
    // Only transform a complete, byte-exact native read. Numbered, partial, or
    // client-truncated reads pass through; they may be the exact span for an edit.
    let live;
    try {
        live = readFileSync(file);
    }
    catch {
        return fallback("file_unavailable");
    }
    if (!live.equals(Buffer.from(output)))
        return fallback("partial_or_modified_read");
    const indexed = await storage.getNode(`file:${rel}`);
    if (indexed?.type !== "file" || indexed.hash !== createHash("sha256").update(live).digest("hex")) {
        return fallback("stale_index");
    }
    const symbols = (await storage.queryNodesByFilePaths([rel]))
        .filter((node) => node.type === "symbol" && !node.name.includes("."))
        .sort((a, b) => a.lineStart - b.lineStart || a.id.localeCompare(b.id));
    if (symbols.length === 0 || symbols.length > 40)
        return fallback("outline_unavailable");
    const outline = [
        `${rel} [Codemesh navigation outline; bodies omitted; read exact source before editing]`,
        ...symbols.map((symbol) => `${symbol.lineStart}-${symbol.lineEnd} ${symbol.id} ${symbol.signature}`),
        "",
    ].join("\n");
    return outline.length < output.length
        ? { output: outline, transformed: true, reason: "verified_read_outline" }
        : fallback("no_savings");
}
//# sourceMappingURL=native-adapter.js.map