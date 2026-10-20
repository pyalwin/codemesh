#!/usr/bin/env node
// Commands: codemesh index | status | rebuild | explore | help | version
import { SqliteBackend } from "./graph/sqlite.js";
import { Indexer } from "./indexer/indexer.js";
import { handleQuery } from "./tools/query.js";
import { handleContext } from "./tools/context.js";
import { handleTrace } from "./tools/trace.js";
import { handleImpact } from "./tools/impact.js";
import { handleAnswer } from "./tools/answer.js";
import { handleMap } from "./tools/map.js";
import { handleReadSymbol } from "./tools/read-symbol.js";
import { handleSource } from "./tools/source.js";
import { handleRecall } from "./tools/recall.js";
import { handleInspect } from "./tools/inspect.js";
import { handleHydrate } from "./tools/hydrate.js";
import { handleEnrich } from "./tools/enrich.js";
import { handleWorkflow } from "./tools/workflow.js";
import { compactExploreResult } from "./cli/compact.js";
import { foldNativeSearch, outlineNativeRead } from "./cli/native-adapter.js";
import { dirname, join } from "path";
import { fileURLToPath } from "url";
import { mkdirSync, readFileSync, existsSync } from "fs";
const args = process.argv.slice(2);
const command = args[0];
const projectRoot = process.env.CODEMESH_PROJECT_ROOT || process.cwd();
const dbDir = join(projectRoot, ".codemesh");
const dbPath = join(dbDir, "codemesh.db");
function readPackageVersion() {
    try {
        const here = dirname(fileURLToPath(import.meta.url));
        const pkg = JSON.parse(readFileSync(join(here, "..", "package.json"), "utf8"));
        if (typeof pkg.version === "string")
            return pkg.version;
    }
    catch {
        // fall through
    }
    return "unknown";
}
function printVersion() {
    console.log(`codemesh ${readPackageVersion()}`);
}
function printUsage() {
    console.log(`
codemesh — Code knowledge graph CLI

Usage:
  codemesh index [--with-summaries] [--json] Index the project (incremental, SQLite-only)
  codemesh status [--json]                      Show graph statistics
  codemesh rebuild [--json]                     Force structural re-index; keep agent knowledge
  codemesh explore map [--node ID] [--relation R] [--limit N] [--cursor C] [--max-bytes N] [--compact]
                                                Expand one indexed level or relation (start here)
  codemesh explore source <symbol-id> [--mode auto|fold|preview|full] [--if-hash HASH] [--force]
                          [--session ID --epoch ID] [--compact]
                                                Read a revision-checked source view
  codemesh explore inspect <symbol-id> [--depth 0|1] [--direction outgoing|incoming|both]
                          [--max-candidates N] [--max-bytes N] [--max-symbols N] [--compact]
                                                Inspect exact code and verified neighbors within a budget
  codemesh explore hydrate --references 'JSON_ARRAY' [--max-bytes N] [--max-symbols N] [--compact]
                                                Verify and reopen saved symbol references
  codemesh explore search <query> [--path FILE_OR_DIR] [--scope symbols|files|workflows|all]
                          [--limit 1..20] [--max-bytes N] [--exact-tie-break pagerank|path-depth] [--compact]
                                                Search the knowledge graph (FTS)
  codemesh explore recall [--id ID] [--path P] [--symbol S] [--query Q] [--compact]
                                                Recall saved notes and workflows
  codemesh explore context <path...> [--symbol S] Get file/symbol context (multi-path supported)
  codemesh explore trace <symbol> [--depth N]    Trace a call chain from a symbol
  codemesh explore impact <path> [--symbol S]    Find reverse dependencies
  codemesh explore answer <question>             One-call context assembly for a question
  codemesh explore read <symbol>                 Read source code for a specific symbol
  codemesh remember --path P --summary TEXT --session ID [--symbol S] [--status hypothesis|verified]
                                                Save an attributed code note
  codemesh workflow --name N --description D --file P [--file P ...]
                                                Save an ordered workflow
  codemesh adapt search [--json]                 Fold native rg -n output from stdin when reversible
  codemesh adapt read --path P --intent navigate|edit [--json]
                                                Outline a verified full-file read for navigation
  codemesh version | --version | -v              Print the installed version
  codemesh help                                  Show this help message

Environment:
  CODEMESH_PROJECT_ROOT   Project root directory (default: cwd)
`);
}
async function runIndex(force = false) {
    const withEmbeddings = false;
    const withSummaries = args.includes("--with-summaries");
    mkdirSync(dbDir, { recursive: true });
    const storage = new SqliteBackend(dbPath);
    await storage.initialize();
    const indexer = new Indexer(storage, projectRoot);
    const lastLogByPhase = {};
    const result = await indexer.index({
        withEmbeddings,
        withSummaries,
        force,
        onProgress: (p) => {
            const now = Date.now();
            const last = lastLogByPhase[p.phase] ?? 0;
            // Throttle to once per 2s per phase, but always flush the final tick.
            // Stderr keeps stdout pipe-clean for `explore` JSON consumers.
            if (p.completed === p.total || now - last > 2000) {
                lastLogByPhase[p.phase] = now;
                process.stderr.write(`  [${p.phase}] ${p.completed}/${p.total} (${p.elapsedMs}ms)\n`);
            }
        },
    });
    if (args.includes("--json")) {
        console.log(JSON.stringify({ projectRoot, ...result }, null, 2));
        await storage.close();
        return;
    }
    console.log(`Indexed ${result.filesIndexed} files`);
    console.log(`  Symbols found:  ${result.symbolsFound}`);
    console.log(`  Edges created:  ${result.edgesCreated}`);
    console.log(`  Files deleted:  ${result.filesDeleted}`);
    console.log(`  Duration:       ${result.durationMs}ms`);
    if (result.pagerankScore) {
        console.log(`  PageRank:       ${result.pagerankScore.computed} nodes scored`);
        if (result.pagerankScore.topNodes.length > 0) {
            console.log(`  Top nodes by PageRank:`);
            for (const { id, score } of result.pagerankScore.topNodes.slice(0, 5)) {
                console.log(`    ${id} — ${score.toFixed(6)}`);
            }
        }
    }
    if (result.summaries) {
        console.log(`  Summaries:      ${result.summaries.generated} generated, ${result.summaries.skipped} skipped`);
    }
    await storage.close();
}
async function runStatus() {
    if (!existsSync(dbPath)) {
        console.log("No codemesh database found. Run `codemesh index` first.");
        return;
    }
    const storage = new SqliteBackend(dbPath);
    await storage.initialize();
    const stats = await storage.getStats();
    if (args.includes("--json")) {
        console.log(JSON.stringify({ projectRoot, ...stats }, null, 2));
        await storage.close();
        return;
    }
    const totalNodes = Object.values(stats.nodeCount).reduce((a, b) => a + b, 0);
    const totalEdges = Object.values(stats.edgeCount).reduce((a, b) => a + b, 0);
    console.log(`Codemesh status for ${projectRoot}`);
    console.log();
    console.log(`Nodes: ${totalNodes}`);
    for (const [type, count] of Object.entries(stats.nodeCount)) {
        console.log(`  ${type}: ${count}`);
    }
    console.log();
    console.log(`Edges: ${totalEdges}`);
    for (const [type, count] of Object.entries(stats.edgeCount)) {
        console.log(`  ${type}: ${count}`);
    }
    console.log();
    console.log(`Stale concepts: ${stats.staleCount}`);
    console.log(`Last indexed:   ${stats.lastIndexedAt ?? "never"}`);
    if (stats.metrics) {
        console.log(`Source bytes delivered: ${stats.metrics.source_bytes_delivered ?? 0}`);
        console.log(`Source bytes avoided:   ${stats.metrics.source_bytes_avoided ?? 0}`);
        console.log(`Conditional read hits:  ${stats.metrics.conditional_hits ?? 0}`);
        console.log(`Retrieval cache hits:   ${stats.metrics.retrieval_cache_hits ?? 0}`);
    }
    await storage.close();
}
async function runRebuild() {
    await runIndex(true);
}
function parseFlag(flagArgs, flag) {
    const idx = flagArgs.indexOf(flag);
    if (idx !== -1 && idx + 1 < flagArgs.length) {
        return flagArgs[idx + 1];
    }
    return undefined;
}
function parseStrictOptions(tokens, values, switches = []) {
    const parsed = new Map();
    for (let i = 0; i < tokens.length; i++) {
        const flag = tokens[i];
        if (parsed.has(flag))
            throw new Error(`Duplicate option: ${flag}`);
        if (switches.includes(flag)) {
            parsed.set(flag, "true");
        }
        else if (values.includes(flag)) {
            const value = tokens[++i];
            if (!value || value.startsWith("--"))
                throw new Error(`Missing value for ${flag}`);
            parsed.set(flag, value);
        }
        else {
            throw new Error(`Unknown option: ${flag}`);
        }
    }
    return parsed;
}
function parseBoundedInt(options, name, minimum, maximum) {
    const raw = options.get(name);
    if (raw === undefined)
        return undefined;
    if (!/^\d+$/.test(raw))
        throw new Error(`${name} must be an integer from ${minimum} to ${maximum}`);
    const value = Number(raw);
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
        throw new Error(`${name} must be an integer from ${minimum} to ${maximum}`);
    }
    return value;
}
function parseHydrateReferences(options) {
    const inline = options.get("--references");
    const file = options.get("--references-file");
    if ((inline === undefined) === (file === undefined)) {
        throw new Error("Provide exactly one of --references JSON_ARRAY or --references-file PATH");
    }
    let parsed;
    try {
        parsed = JSON.parse(inline ?? readFileSync(file, "utf8"));
    }
    catch (error) {
        throw new Error(`Invalid references JSON: ${error instanceof Error ? error.message : String(error)}`);
    }
    if (!Array.isArray(parsed) || parsed.length < 1 || parsed.length > 8 || parsed.some((ref) => !ref || typeof ref !== "object" || Array.isArray(ref) ||
        typeof ref.id !== "string" || !ref.id ||
        ["hash", "fileHash", "bodyHash"].some((key) => ref[key] !== undefined && typeof ref[key] !== "string") ||
        (ref.mode !== undefined && !["auto", "fold", "preview", "full"].includes(ref.mode)))) {
        throw new Error("--references must be a JSON array of 1..8 objects with id and optional hash, mode, fileHash, bodyHash");
    }
    return parsed;
}
function parseMapArgs(flagArgs) {
    const result = {};
    const allowed = new Set(["--node", "--relation", "--limit", "--cursor", "--max-bytes"]);
    for (let i = 0; i < flagArgs.length; i += 2) {
        const flag = flagArgs[i];
        const value = flagArgs[i + 1];
        if (!allowed.has(flag) || !value || value.startsWith("--")) {
            throw new Error("Usage: codemesh explore map [--node ID] [--relation contains|calls|called_by|imports|imported_by|extends] [--limit 1..100] [--cursor C] [--max-bytes N]");
        }
        if (flag === "--node")
            result.node = value;
        if (flag === "--cursor")
            result.cursor = value;
        if (flag === "--max-bytes")
            result.maxBytes = Number(value);
        if (flag === "--relation") {
            if (!["contains", "calls", "called_by", "imports", "imported_by", "extends"].includes(value)) {
                throw new Error(`Invalid relation: ${value}`);
            }
            result.relation = value;
        }
        if (flag === "--limit") {
            const limit = Number(value);
            if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
                throw new Error("--limit must be an integer from 1 to 100");
            }
            result.limit = limit;
        }
    }
    return result;
}
async function runExplore() {
    const subcommand = args[1];
    const compact = args.includes("--compact");
    if (compact && !["map", "search", "source", "recall", "inspect", "hydrate"].includes(subcommand ?? "")) {
        throw new Error("--compact is available for map, search, source, recall, inspect, and hydrate");
    }
    if (!subcommand) {
        console.error("Usage: codemesh explore <map|source|inspect|hydrate|search|context|trace|impact|answer> ...");
        process.exit(1);
    }
    if (!existsSync(dbPath)) {
        console.error("No codemesh database found. Run `codemesh index` first.");
        process.exit(1);
    }
    const storage = new SqliteBackend(dbPath);
    await storage.initialize();
    try {
        let result;
        switch (subcommand) {
            case "map": {
                result = await handleMap(storage, parseMapArgs(args.slice(2).filter((arg) => arg !== "--compact")), projectRoot);
                break;
            }
            case "source": {
                const id = args[2];
                if (!id) {
                    throw new Error("Usage: codemesh explore source <symbol-id> [--mode auto|fold|preview|full] [--if-hash HASH] [--force]");
                }
                const mode = parseFlag(args, "--mode");
                if (mode && !["auto", "fold", "preview", "full"].includes(mode)) {
                    throw new Error("--mode must be auto, fold, preview, or full");
                }
                result = await handleSource(storage, {
                    id,
                    mode: mode,
                    ifHash: parseFlag(args, "--if-hash"),
                    force: args.includes("--force"),
                    sessionId: parseFlag(args, "--session"),
                    contextEpoch: parseFlag(args, "--epoch"),
                }, projectRoot);
                break;
            }
            case "inspect": {
                const id = args[2];
                if (!id || id.startsWith("--"))
                    throw new Error("Usage: codemesh explore inspect <symbol-id> [--depth 0|1] [--direction outgoing|incoming|both] [--max-candidates N] [--max-bytes N] [--max-symbols N] [--compact]");
                const options = parseStrictOptions(args.slice(3), ["--depth", "--direction", "--max-candidates", "--max-bytes", "--max-symbols"], ["--compact"]);
                const direction = options.get("--direction");
                if (direction && !["outgoing", "incoming", "both"].includes(direction))
                    throw new Error("--direction must be outgoing, incoming, or both");
                result = await handleInspect(storage, {
                    id,
                    depth: parseBoundedInt(options, "--depth", 0, 1),
                    direction: direction,
                    maxCandidates: parseBoundedInt(options, "--max-candidates", 1, 128),
                    maxBytes: parseBoundedInt(options, "--max-bytes", 1024, 131072),
                    maxSymbols: parseBoundedInt(options, "--max-symbols", 1, 8),
                }, projectRoot);
                break;
            }
            case "hydrate": {
                const options = parseStrictOptions(args.slice(2), ["--references", "--references-file", "--max-bytes", "--max-symbols"], ["--compact"]);
                result = await handleHydrate(storage, {
                    references: parseHydrateReferences(options),
                    maxBytes: parseBoundedInt(options, "--max-bytes", 1024, 131072),
                    maxSymbols: parseBoundedInt(options, "--max-symbols", 1, 8),
                }, projectRoot);
                break;
            }
            case "search": {
                const searchArgs = args.slice(2).filter((arg) => arg !== "--compact");
                const queryWords = [];
                let limit;
                let path;
                let maxBytes;
                let scope;
                let exactTieBreak;
                const seenOptions = new Set();
                for (let i = 0; i < searchArgs.length; i++) {
                    if (["--limit", "--path", "--scope", "--max-bytes", "--exact-tie-break"].includes(searchArgs[i])) {
                        const flag = searchArgs[i];
                        const value = searchArgs[++i];
                        if (seenOptions.has(flag) || !value || value.startsWith("--"))
                            throw new Error(`Missing or repeated search option: ${flag}`);
                        seenOptions.add(flag);
                        if (flag === "--limit")
                            limit = Number(value);
                        if (flag === "--max-bytes")
                            maxBytes = Number(value);
                        if (flag === "--path")
                            path = value;
                        if (flag === "--exact-tie-break") {
                            if (!["pagerank", "path-depth"].includes(value))
                                throw new Error("--exact-tie-break must be pagerank or path-depth");
                            exactTieBreak = value;
                        }
                        if (flag === "--scope") {
                            if (!["files", "symbols", "workflows", "all"].includes(value))
                                throw new Error("--scope must be files, symbols, workflows, or all");
                            scope = value;
                        }
                    }
                    else if (searchArgs[i].startsWith("--")) {
                        throw new Error(`Unknown search option: ${searchArgs[i]}`);
                    }
                    else {
                        queryWords.push(searchArgs[i]);
                    }
                }
                const query = queryWords.join(" ");
                if (!query) {
                    console.error("Usage: codemesh explore search <query> [--path FILE_OR_DIR] [--scope symbols|files|workflows|all] [--limit N] [--max-bytes N] [--exact-tie-break pagerank|path-depth]");
                    process.exit(1);
                }
                result = await handleQuery(storage, { query, path, scope, maxBytes, exactTieBreak, limit: limit ?? (compact ? 8 : undefined) }, projectRoot);
                break;
            }
            case "recall": {
                result = await handleRecall(storage, {
                    id: parseFlag(args, "--id"),
                    path: parseFlag(args, "--path"),
                    symbol: parseFlag(args, "--symbol"),
                    query: parseFlag(args, "--query"),
                    limit: parseFlag(args, "--limit") ? Number(parseFlag(args, "--limit")) : undefined,
                }, projectRoot);
                break;
            }
            case "context": {
                const symbol = parseFlag(args, "--symbol");
                // Collect positional args after "context", skipping --flags and their values
                const contextPaths = [];
                const contextArgs = args.slice(2);
                for (let i = 0; i < contextArgs.length; i++) {
                    if (contextArgs[i].startsWith("--")) {
                        i++; // skip the flag's value too
                        continue;
                    }
                    contextPaths.push(contextArgs[i]);
                }
                if (contextPaths.length === 0) {
                    console.error("Usage: codemesh explore context <path> [path2 ...] [--symbol name]");
                    process.exit(1);
                }
                if (contextPaths.length === 1) {
                    result = await handleContext(storage, { path: contextPaths[0], symbol }, projectRoot);
                }
                else {
                    result = await handleContext(storage, { paths: contextPaths, symbol }, projectRoot);
                }
                break;
            }
            case "trace": {
                const symbol = args[2];
                if (!symbol) {
                    console.error("Usage: codemesh explore trace <symbol> [--depth N] [--compact]");
                    process.exit(1);
                }
                const depthStr = parseFlag(args, "--depth");
                const depth = depthStr ? parseInt(depthStr, 10) : 5;
                const compact = args.includes("--compact");
                result = await handleTrace(storage, { symbol, depth, compact }, projectRoot);
                break;
            }
            case "impact": {
                const path = args[2];
                if (!path) {
                    console.error("Usage: codemesh explore impact <path> [--symbol name]");
                    process.exit(1);
                }
                const symbol = parseFlag(args, "--symbol");
                result = await handleImpact(storage, { path, symbol });
                break;
            }
            case "answer": {
                const question = args.slice(2).filter((a) => !a.startsWith("--")).join(" ");
                if (!question) {
                    console.error("Usage: codemesh explore answer <question>");
                    process.exit(1);
                }
                result = await handleAnswer(storage, { question }, projectRoot);
                break;
            }
            case "read": {
                const readSymbol = args[2];
                if (!readSymbol) {
                    console.error("Usage: codemesh explore read <symbol>");
                    process.exit(1);
                }
                result = await handleReadSymbol(storage, { symbol: readSymbol }, projectRoot);
                break;
            }
            default:
                console.error(`Unknown explore subcommand: ${subcommand}`);
                console.error("Available: map, source, inspect, hydrate, search, recall, context, trace, impact, answer, read");
                process.exit(1);
        }
        // Bounded handlers budget their serialized JSON. Keep these envelopes
        // compact and avoid adding projectRoot or indentation after budget checks.
        const bounded = ["inspect", "hydrate", "search"].includes(subcommand)
            || (subcommand === "map" && result !== null && typeof result === "object" && "entries" in result);
        console.log(JSON.stringify(compact ? compactExploreResult(subcommand, result)
            : bounded ? result : { projectRoot, ...result }, null, compact || bounded ? 0 : 2));
    }
    finally {
        await storage.close();
    }
}
async function runWrite(action) {
    if (!existsSync(dbPath))
        throw new Error("No codemesh database found. Run `codemesh index` first.");
    const storage = new SqliteBackend(dbPath);
    await storage.initialize();
    try {
        let result;
        if (action === "remember") {
            const path = parseFlag(args, "--path");
            const summary = parseFlag(args, "--summary");
            const sessionId = parseFlag(args, "--session");
            const status = parseFlag(args, "--status");
            if (!path || !summary || !sessionId || (status && !["hypothesis", "verified"].includes(status))) {
                throw new Error("Usage: codemesh remember --path P --summary TEXT --session ID [--symbol S] [--status hypothesis|verified]");
            }
            result = await handleEnrich(storage, {
                path, summary, sessionId, symbol: parseFlag(args, "--symbol"),
                status: status,
            });
        }
        else {
            const name = parseFlag(args, "--name");
            const description = parseFlag(args, "--description");
            const files = [];
            for (let i = 1; i < args.length - 1; i++) {
                if (args[i] === "--file")
                    files.push(args[i + 1]);
            }
            if (!name || !description || files.length === 0) {
                throw new Error("Usage: codemesh workflow --name N --description D --file P [--file P ...]");
            }
            result = await handleWorkflow(storage, { name, description, files });
        }
        console.log(JSON.stringify({ projectRoot, ...result }, null, 2));
    }
    finally {
        await storage.close();
    }
}
async function runAdapt() {
    const kind = args[1];
    const output = readFileSync(0, "utf8");
    let result;
    if (kind === "search") {
        result = foldNativeSearch(output);
    }
    else if (kind === "read") {
        const path = parseFlag(args, "--path");
        const intent = parseFlag(args, "--intent") ?? "edit";
        if (!path || !["navigate", "edit"].includes(intent)) {
            throw new Error("Usage: codemesh adapt read --path P --intent navigate|edit");
        }
        if (!existsSync(dbPath))
            throw new Error("No codemesh database found. Run `codemesh index` first.");
        const storage = new SqliteBackend(dbPath);
        await storage.initialize();
        try {
            result = await outlineNativeRead(storage, projectRoot, path, output, intent);
        }
        finally {
            await storage.close();
        }
    }
    else {
        throw new Error("Usage: codemesh adapt <search|read> [options] < native-output");
    }
    if (args.includes("--json"))
        console.log(JSON.stringify(result));
    else
        process.stdout.write(result.output);
}
async function main() {
    switch (command) {
        case "index":
            await runIndex();
            break;
        case "status":
            await runStatus();
            break;
        case "rebuild":
            await runRebuild();
            break;
        case "explore":
            await runExplore();
            break;
        case "remember":
        case "workflow":
            await runWrite(command);
            break;
        case "adapt":
            await runAdapt();
            break;
        case "help":
        case "--help":
        case "-h":
        case undefined:
            printUsage();
            break;
        case "version":
        case "--version":
        case "-v":
            printVersion();
            break;
        default:
            console.error(`Unknown command: ${command}`);
            printUsage();
            process.exit(1);
    }
}
main().catch((err) => {
    console.error("Error:", err.message);
    process.exit(1);
});
//# sourceMappingURL=cli.js.map