/** Exact, bounded source bundles with conservative one-hop call binding. */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { handleSource } from "./source.js";
import { resolveInspectCalls, resolveInspectIncoming } from "./inspect-resolver.js";
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const size = (output) => Buffer.byteLength(JSON.stringify(output));
/** Metadata changes (including a longer status) still count toward the cap. */
function boundMetadata(output) {
    if (size(output) > output.limits.maxBytes)
        delete output.hint;
    if (size(output) > output.limits.maxBytes) {
        output.omitted.sources += output.sources.length;
        output.omitted.calls += output.calls.length;
        output.sources = [];
        output.calls = [];
    }
    if (size(output) > output.limits.maxBytes)
        throw new Error("Inspect metadata exceeds maxBytes; increase maxBytes.");
    return output;
}
function validate(input) {
    if (typeof input.id !== "string" || !input.id || Buffer.byteLength(input.id) > 4096) {
        throw new Error("id must be a nonempty symbol ID or name of at most 4096 bytes");
    }
    const maxBytes = input.maxBytes ?? 24 * 1024;
    const maxSymbols = input.maxSymbols ?? 4;
    const depth = input.depth ?? 1;
    const direction = input.direction ?? "outgoing";
    const maxCandidates = input.maxCandidates ?? 32;
    if (!Number.isInteger(maxBytes) || maxBytes < 1024 || maxBytes > 128 * 1024) {
        throw new Error("maxBytes must be an integer from 1024 to 131072");
    }
    if (!Number.isInteger(maxSymbols) || maxSymbols < 1 || maxSymbols > 8) {
        throw new Error("maxSymbols must be an integer from 1 to 8");
    }
    if (depth !== 0 && depth !== 1)
        throw new Error("depth must be 0 or 1");
    if (!["outgoing", "incoming", "both"].includes(direction))
        throw new Error("direction must be outgoing, incoming, or both");
    if (!Number.isInteger(maxCandidates) || maxCandidates < 1 || maxCandidates > 128) {
        throw new Error("maxCandidates must be an integer from 1 to 128");
    }
    return { maxBytes, maxSymbols, depth, direction, maxCandidates };
}
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
/** Synchronous final verification: all awaited lookups have finished. */
function unchangedFile(projectRoot, path, expected) {
    try {
        return hash(readFileSync(join(projectRoot, path))) === expected;
    }
    catch {
        return false;
    }
}
export async function handleInspect(storage, input, projectRoot) {
    const limits = validate(input);
    const reader = silentReader(storage);
    const root = await handleSource(reader, { id: input.id, mode: "full", force: true }, projectRoot);
    const output = {
        status: root.status === "deleted" ? "deleted" : root.status === "stale" ? "stale" : "fresh",
        rootId: root.id, sources: [], calls: [], omitted: { sources: 0, calls: 0 }, limits,
        hint: "Whole exact source blocks only. Request source for omitted IDs; unresolved calls require further navigation.",
    };
    // A pathological canonical ID may itself exceed a very small envelope.
    boundMetadata(output);
    output.sources.push(root);
    if (size(output) > limits.maxBytes) {
        output.sources = [];
        output.status = root.status === "fresh" ? "budget_exceeded" : output.status;
        output.omitted.sources = 1;
        return boundMetadata(output);
    }
    if (root.status !== "fresh")
        return output;
    const rootFile = await reader.getNode(`file:${root.filePath}`);
    const snapshots = new Map();
    const conflictingSnapshots = new Set();
    const rememberSnapshot = (path, expected) => {
        if (snapshots.has(path) && snapshots.get(path) !== expected)
            conflictingSnapshots.add(path);
        else
            snapshots.set(path, expected);
    };
    if (rootFile?.type === "file")
        snapshots.set(root.filePath, rootFile.hash);
    const result = limits.depth === 1 && limits.direction !== "incoming"
        ? await resolveInspectCalls(reader, root, projectRoot) : { calls: [], snapshots: [] };
    for (const snapshot of result.snapshots)
        rememberSnapshot(snapshot.path, snapshot.hash);
    const calls = result.calls.map(call => ({ ...call,
        sourceId: root.id, direction: "outgoing", relation: "calls",
        ...(call.targetId ? { neighborId: call.targetId } : {}),
        status: call.resolution === "ambiguous" ? "ambiguous" : call.targetId ? "omitted" : "unresolved",
    }));
    const candidates = new Map([[root.id, root]]);
    const failures = new Map();
    if (limits.depth === 1 && limits.direction !== "outgoing") {
        const incoming = await resolveInspectIncoming(reader, root, projectRoot, limits.maxCandidates);
        output.incoming = incoming.coverage;
        for (const snapshot of incoming.snapshots)
            rememberSnapshot(snapshot.path, snapshot.hash);
        for (const source of incoming.sources)
            candidates.set(source.id, source);
        for (const { failureStatus, ...call } of incoming.calls)
            calls.push({ ...call, direction: "incoming", relation: "calls",
                ...(call.targetId ? { neighborId: call.sourceId } : {}),
                status: failureStatus ?? (call.targetId ? "omitted" : "unresolved"),
            });
    }
    for (const call of calls) {
        if (call.direction !== "outgoing" || !call.targetId || candidates.has(call.targetId) || failures.has(call.targetId))
            continue;
        const source = await handleSource(reader, { id: call.targetId, mode: "full", force: true }, projectRoot);
        if (source.status !== "fresh") {
            failures.set(call.targetId, { status: source.status === "deleted" ? "deleted" : "stale", reason: "callee_source_not_current" });
            continue;
        }
        const file = await reader.getNode(`file:${source.filePath}`);
        if (file?.type !== "file") {
            failures.set(call.targetId, { status: "stale", reason: "callee_index_unavailable" });
            continue;
        }
        rememberSnapshot(source.filePath, file.hash);
        candidates.set(source.id, source);
    }
    // Import/package files are part of the proof, even when no source from them
    // is returned. A concurrent edit invalidates that proof as well as the body.
    const changed = [...snapshots].filter(([path, expected]) => conflictingSnapshots.has(path)
        || !unchangedFile(projectRoot, path, expected));
    for (const source of candidates.values()) {
        try {
            const actual = readFileSync(join(projectRoot, source.filePath), "utf-8").split("\n")
                .slice(source.lineStart - 1, source.lineEnd).join("\n");
            if (actual === source.source)
                continue;
        }
        catch { /* Missing files are stale snapshots too. */ }
        changed.push([source.filePath, "body_changed"]);
    }
    const invalidateIncomingProofs = () => {
        if (output.incoming) {
            output.incoming.verified = 0;
            output.incoming.unverified = output.incoming.checked;
        }
    };
    if (!snapshots.has(root.filePath) || changed.some(([path]) => path === root.filePath)) {
        invalidateIncomingProofs();
        output.status = "stale";
        output.sources = [{ ...root, status: "stale", source: null, sourceHash: null, bodyHash: null,
                hint: "Source changed during inspection. Reindex and inspect again." }];
        return boundMetadata(output);
    }
    if (changed.length) {
        invalidateIncomingProofs();
        for (const [id] of candidates)
            if (id !== root.id) {
                failures.set(id, { status: "stale", reason: "binding_dependency_changed_during_inspection" });
                candidates.delete(id);
            }
    }
    // Bodies take priority over diagnostic call details. Every accepted body is
    // whole and exact; a byte limit never changes a full body into a preview.
    const delivered = new Map([[root.id, 0]]);
    const excluded = new Map();
    for (const [id, source] of candidates) {
        if (id === root.id)
            continue;
        if (output.sources.length >= limits.maxSymbols) {
            excluded.set(id, "max_symbols");
            continue;
        }
        output.sources.push(source);
        if (size(output) > limits.maxBytes) {
            output.sources.pop();
            excluded.set(id, "max_bytes");
        }
        else
            delivered.set(id, output.sources.length - 1);
    }
    output.omitted.sources = excluded.size;
    for (const call of calls) {
        if (call.neighborId) {
            const index = delivered.get(call.neighborId);
            const failure = failures.get(call.neighborId);
            if (changed.length) {
                call.status = "stale";
                call.reason = "binding_dependency_changed_during_inspection";
            }
            else if (index !== undefined) {
                call.status = "included";
                call.sourceIndex = index;
            }
            else if (failure)
                Object.assign(call, failure);
            else {
                call.status = "omitted";
                call.reason = excluded.get(call.neighborId) ?? "binding_dependency_changed";
            }
        }
        output.calls.push(call);
        if (size(output) > limits.maxBytes) {
            output.calls.pop();
            output.omitted.calls++;
        }
    }
    // The counts may gain digits while pruning. Preserve the byte contract for
    // the final complete JSON envelope, not just each candidate's trial size.
    while (size(output) > limits.maxBytes && output.calls.length) {
        output.calls.pop();
        output.omitted.calls++;
    }
    while (size(output) > limits.maxBytes && output.sources.length > 1) {
        const removed = output.sources.pop();
        output.omitted.sources++;
        for (const call of output.calls)
            if (call.neighborId === removed.id) {
                call.status = "omitted";
                call.reason = "max_bytes";
                delete call.sourceIndex;
            }
    }
    if (size(output) > limits.maxBytes) {
        output.sources = [];
        output.calls = [];
        output.omitted.sources = candidates.size;
        output.omitted.calls = calls.length;
        output.status = "budget_exceeded";
        boundMetadata(output);
    }
    for (const source of output.sources) {
        await storage.incrementMetric?.("source_bytes_delivered", Buffer.byteLength(source.source));
    }
    // Metrics are asynchronous storage calls too. If one races an edit, retract
    // the affected delivered-byte count and verify the remaining root again.
    // Each pass removes at least one fresh source, so this loop is bounded by
    // maxSymbols; no invalid source or proof becomes eligible again here.
    while (output.sources.some(source => source.status === "fresh")) {
        const rootChanged = !unchangedFile(projectRoot, root.filePath, snapshots.get(root.filePath))
            || (() => {
                try {
                    return readFileSync(join(projectRoot, root.filePath), "utf-8").split("\n")
                        .slice(root.lineStart - 1, root.lineEnd).join("\n") !== root.source;
                }
                catch {
                    return true;
                }
            })();
        const dependencyChanged = [...snapshots].some(([path, expected]) => path !== root.filePath
            && !unchangedFile(projectRoot, path, expected));
        const removed = rootChanged ? output.sources
            : dependencyChanged ? output.sources.filter(source => source.id !== root.id) : [];
        if (!removed.length) {
            // Even an omitted caller has a claimed proof. A symbol budget may leave
            // only the root body, but cannot exempt those claims from the final check.
            if (dependencyChanged) {
                invalidateIncomingProofs();
                for (const call of output.calls)
                    if (call.targetId) {
                        call.status = "stale";
                        call.reason = "binding_dependency_changed_during_inspection";
                        delete call.sourceIndex;
                    }
                while (size(output) > limits.maxBytes && output.calls.length) {
                    output.calls.pop();
                    output.omitted.calls++;
                }
                boundMetadata(output);
            }
            break;
        }
        invalidateIncomingProofs();
        if (rootChanged) {
            output.status = "stale";
            output.sources = [{ ...root, status: "stale", source: null, sourceHash: null, bodyHash: null,
                    hint: "Source changed during inspection. Reindex and inspect again." }];
            output.omitted.calls += output.calls.length;
            output.calls = [];
        }
        else {
            output.sources = [root];
            for (const call of output.calls)
                if (call.targetId) {
                    call.status = "stale";
                    call.reason = "binding_dependency_changed_during_inspection";
                    delete call.sourceIndex;
                }
        }
        await storage.incrementMetric?.("source_bytes_delivered", -removed.reduce((sum, source) => sum + Buffer.byteLength(source.source), 0));
        while (size(output) > limits.maxBytes && output.calls.length) {
            output.calls.pop();
            output.omitted.calls++;
        }
        boundMetadata(output);
    }
    return output;
}
//# sourceMappingURL=inspect.js.map