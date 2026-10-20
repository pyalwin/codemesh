/** Restore saved references as bounded, exact, live-verified source. */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { handleSource } from "./source.js";
const MODES = new Set(["full", "auto", "preview", "fold"]);
const hash = (value) => createHash("sha256").update(value).digest("hex");
const bytes = (value) => Buffer.byteLength(JSON.stringify(value));
function validate(input) {
    if (!Array.isArray(input.references) || input.references.length < 1 || input.references.length > 8) {
        throw new Error("references must contain 1..8 saved source references");
    }
    const maxBytes = input.maxBytes ?? 24 * 1024;
    const maxSymbols = input.maxSymbols ?? 4;
    if (!Number.isInteger(maxBytes) || maxBytes < 1024 || maxBytes > 128 * 1024) {
        throw new Error("maxBytes must be an integer from 1024 to 131072");
    }
    if (!Number.isInteger(maxSymbols) || maxSymbols < 1 || maxSymbols > 8) {
        throw new Error("maxSymbols must be an integer from 1 to 8");
    }
    for (const ref of input.references) {
        if (!ref || typeof ref.id !== "string" || !ref.id || Buffer.byteLength(ref.id) > 4096) {
            throw new Error("Each reference requires a nonempty ID of at most 4096 bytes");
        }
        if (ref.mode !== undefined && !MODES.has(ref.mode))
            throw new Error("Invalid saved source mode");
        for (const key of ["hash", "fileHash", "bodyHash"]) {
            if (ref[key] !== undefined && (typeof ref[key] !== "string" || !/^[a-f0-9]{64}$/i.test(ref[key]))) {
                throw new Error(`${key} must be a SHA-256 hex string`);
            }
        }
    }
    return { maxBytes, maxSymbols };
}
/** Internal reads must not claim bytes or conditional delivery to the caller. */
function silentReader(storage) {
    return new Proxy(storage, {
        get(target, key) {
            if (key === "incrementMetric" || key === "recordDelivery")
                return undefined;
            const value = Reflect.get(target, key, target);
            return typeof value === "function" ? value.bind(target) : value;
        },
    });
}
function sourceFailure(source, item) {
    item.id = source.id;
    if (source.status === "deleted") {
        item.status = "missing";
        item.reason = "source_unavailable";
        return true;
    }
    if (source.status !== "fresh" || typeof source.source !== "string") {
        item.status = "stale";
        item.reason = "index_not_current";
        return true;
    }
    return false;
}
function boundDiagnostics(output) {
    for (const field of ["detail", "requestedId", "id"]) {
        for (let index = output.references.length - 1; index >= 0 && bytes(output) > output.limits.maxBytes; index--) {
            delete output.references[index][field];
        }
    }
}
/**
 * changed: a fresh live body/view/file differs from an explicit saved hash.
 * stale: the index cannot verify current source, including a concurrent edit.
 * Neither condition returns source bytes for that reference. A hash of a saved
 * preview only checks that preview; callers needing whole-body continuity must
 * also save bodyHash or fileHash. Missing expected hashes simply request current
 * live source and do not certify that a note's earlier interpretation is valid.
 */
export async function handleHydrate(storage, input, projectRoot) {
    const limits = validate(input);
    const reader = silentReader(storage);
    const output = {
        status: "complete", sources: [], references: [], omitted: { sources: input.references.length }, limits,
    };
    const candidates = new Map();
    for (const [index, ref] of input.references.entries()) {
        const item = { index, requestedId: ref.id, status: "omitted", reason: "max_symbols" };
        output.references.push(item);
        try {
            const view = await handleSource(reader, { id: ref.id, mode: ref.mode ?? "full", force: true }, projectRoot);
            if (sourceFailure(view, item))
                continue;
            if (ref.hash && view.sourceHash !== ref.hash.toLowerCase()) {
                item.status = "changed";
                item.reason = "saved_view_hash_mismatch";
                continue;
            }
            const full = view.mode === "full" ? view
                : await handleSource(reader, { id: view.id, mode: "full", force: true }, projectRoot);
            if (sourceFailure(full, item))
                continue;
            if (view.bodyHash !== full.bodyHash) {
                item.status = "stale";
                item.reason = "changed_during_hydration";
                continue;
            }
            // Recheck the live snapshot after all awaited reads, including the exact
            // returned span. Never certify source from an earlier concurrent version.
            const indexedFile = await storage.getNode(`file:${full.filePath}`);
            const live = readFileSync(join(projectRoot, full.filePath));
            const fileHash = hash(live);
            const span = live.toString("utf-8").split("\n").slice(full.lineStart - 1, full.lineEnd).join("\n");
            if (indexedFile?.type !== "file" || indexedFile.hash !== fileHash
                || span !== full.source || hash(span) !== full.bodyHash) {
                item.status = "stale";
                item.reason = "changed_during_hydration";
                continue;
            }
            if (ref.fileHash && fileHash !== ref.fileHash.toLowerCase()) {
                item.status = "changed";
                item.reason = "saved_file_hash_mismatch";
                continue;
            }
            if (ref.bodyHash && full.bodyHash !== ref.bodyHash.toLowerCase()) {
                item.status = "changed";
                item.reason = "saved_body_hash_mismatch";
                continue;
            }
            candidates.set(index, { source: full, fileHash });
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            const unavailable = ["ENOENT", "ENOTDIR"].includes(error?.code ?? "");
            item.status = message.startsWith("Ambiguous symbol name:") ? "ambiguous"
                : message.startsWith("Symbol not found:") || unavailable ? "missing" : "error";
            item.reason = item.status === "ambiguous" ? "ambiguous_reference"
                : unavailable ? "source_unavailable" : item.status === "missing" ? "symbol_not_found" : "source_read_failed";
            item.detail = message.split("\n", 1)[0].slice(0, 200);
        }
    }
    // A later awaited lookup may have raced an edit to an earlier reference.
    // Verify all candidate snapshots again before selecting the final bundle.
    for (const [index, candidate] of candidates) {
        try {
            if (hash(readFileSync(join(projectRoot, candidate.source.filePath))) === candidate.fileHash)
                continue;
            output.references[index].status = "stale";
            output.references[index].reason = "changed_during_hydration";
        }
        catch {
            output.references[index].status = "missing";
            output.references[index].reason = "source_unavailable";
        }
        candidates.delete(index);
    }
    // Keep indexed outcomes for every reference. Large optional diagnostics must
    // not displace every source block or defeat a small caller-specified budget.
    boundDiagnostics(output);
    const delivered = new Map();
    for (const [index, { source }] of candidates) {
        const item = output.references[index];
        const duplicate = delivered.get(source.id);
        if (duplicate === undefined && output.sources.length >= limits.maxSymbols) {
            item.reason = "max_symbols";
            continue;
        }
        item.reason = "max_bytes";
        const before = { ...item };
        item.status = "fresh";
        item.sourceIndex = duplicate ?? output.sources.length;
        delete item.reason;
        if (duplicate === undefined)
            output.sources.push(source);
        if (bytes(output) > limits.maxBytes) {
            if (duplicate === undefined)
                output.sources.pop();
            Object.assign(item, before);
            delete item.sourceIndex;
        }
        else if (duplicate === undefined) {
            delivered.set(source.id, item.sourceIndex);
        }
    }
    output.omitted.sources = output.references.filter(item => item.status === "omitted").length;
    output.status = output.references.every(item => item.status === "fresh") ? "complete" : "partial";
    for (const source of output.sources) {
        await storage.incrementMetric?.("source_bytes_delivered", Buffer.byteLength(source.source));
    }
    // Metrics await storage too. Recheck after those awaits and compensate for
    // any bytes removed. Each correction can race another edit, so continue
    // until the remaining delivered snapshots pass a final synchronous check.
    // A pass removes at least one source; there are at most maxSymbols passes.
    const snapshots = new Map([...candidates.values()].map(candidate => [candidate.source.id, candidate.fileHash]));
    while (output.sources.length) {
        const failures = new Map();
        for (const [index, source] of output.sources.entries()) {
            try {
                if (hash(readFileSync(join(projectRoot, source.filePath))) !== snapshots.get(source.id)) {
                    failures.set(index, "stale");
                }
            }
            catch {
                failures.set(index, "missing");
            }
        }
        if (!failures.size)
            break;
        const removedBytes = output.sources.reduce((sum, source, index) => sum + (failures.has(index) ? Buffer.byteLength(source.source) : 0), 0);
        const retained = output.sources.filter((_, index) => !failures.has(index));
        const sourceIndexes = new Map(retained.map((source, index) => [source.id, index]));
        for (const item of output.references) {
            if (item.status !== "fresh" || item.sourceIndex === undefined)
                continue;
            const failure = failures.get(item.sourceIndex);
            if (failure) {
                item.status = failure;
                item.reason = failure === "missing" ? "source_unavailable" : "changed_during_hydration";
                delete item.sourceIndex;
            }
            else
                item.sourceIndex = sourceIndexes.get(output.sources[item.sourceIndex].id);
        }
        output.sources = retained;
        output.status = "partial";
        boundDiagnostics(output);
        await storage.incrementMetric?.("source_bytes_delivered", -removedBytes);
    }
    return output;
}
//# sourceMappingURL=hydrate.js.map