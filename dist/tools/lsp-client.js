/**
 * Lightweight LSP client — spawns language servers on demand for precise symbol resolution.
 *
 * Supports: textDocument/definition, textDocument/references
 * Protocol: JSON-RPC over stdio with Content-Length framing
 *
 * The agent never sees LSP — this is an internal enhancement for context.ts.
 * If no language server is available, everything falls back gracefully.
 */
import { spawn, execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { extname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
// ── Idle Reap Config ──────────────────────────────────────────────
function getIdleThresholdMs() {
    return Number(process.env.CODEMESH_LSP_IDLE_MS ?? 900_000);
}
function getHeartbeatMs() {
    return Number(process.env.CODEMESH_LSP_HEARTBEAT_MS ?? 60_000);
}
// ── Language Server Detection ─────────────────────────────────────
function isOnPath(cmd) {
    try {
        execFileSync("which", [cmd], { stdio: "ignore" });
        return true;
    }
    catch {
        return false;
    }
}
export function detectLanguageServer(filePath) {
    const ext = extname(filePath);
    if ([".ts", ".tsx", ".js", ".jsx"].includes(ext)) {
        if (isOnPath("typescript-language-server")) {
            return { binary: "typescript-language-server", args: ["--stdio"] };
        }
    }
    if (ext === ".py") {
        if (isOnPath("pyright-langserver")) {
            return { binary: "pyright-langserver", args: ["--stdio"] };
        }
        if (isOnPath("pylsp")) {
            return { binary: "pylsp", args: [] };
        }
    }
    if (ext === ".swift") {
        if (isOnPath("sourcekit-lsp")) {
            return { binary: "sourcekit-lsp", args: [] };
        }
    }
    return null;
}
class LspTransport {
    process;
    nextId = 1;
    pending = new Map();
    buffer = "";
    constructor(command) {
        this.process = spawn(command.binary, command.args, {
            stdio: ["pipe", "pipe", "pipe"],
        });
        this.process.stdout?.on("data", (chunk) => {
            this.buffer += chunk.toString();
            this.drainBuffer();
        });
        // Silently ignore stderr — language servers are chatty
        this.process.stderr?.on("data", () => { });
        this.process.on("error", () => {
            // Server failed to start — clear all pending
            for (const [, entry] of this.pending) {
                entry.reject(new Error("LSP process failed"));
            }
            this.pending.clear();
        });
        this.process.on("exit", () => {
            for (const [, entry] of this.pending) {
                entry.reject(new Error("LSP process exited"));
            }
            this.pending.clear();
        });
    }
    drainBuffer() {
        while (true) {
            const headerEnd = this.buffer.indexOf("\r\n\r\n");
            if (headerEnd === -1)
                return;
            const header = this.buffer.slice(0, headerEnd);
            const match = header.match(/Content-Length:\s*(\d+)/i);
            if (!match) {
                // Malformed — skip past the header
                this.buffer = this.buffer.slice(headerEnd + 4);
                continue;
            }
            const contentLength = parseInt(match[1], 10);
            const bodyStart = headerEnd + 4;
            if (this.buffer.length < bodyStart + contentLength)
                return; // incomplete
            const body = this.buffer.slice(bodyStart, bodyStart + contentLength);
            this.buffer = this.buffer.slice(bodyStart + contentLength);
            try {
                const msg = JSON.parse(body);
                if (msg.id !== undefined && this.pending.has(msg.id)) {
                    const entry = this.pending.get(msg.id);
                    this.pending.delete(msg.id);
                    if (msg.error) {
                        entry.reject(new Error(msg.error.message));
                    }
                    else {
                        entry.resolve(msg.result);
                    }
                }
            }
            catch {
                // Ignore parse errors
            }
        }
    }
    async sendRequest(method, params) {
        const id = this.nextId++;
        const msg = { jsonrpc: "2.0", id, method, params };
        return new Promise((resolve, reject) => {
            this.pending.set(id, { resolve, reject });
            this.send(msg);
            // Timeout after 10 seconds
            setTimeout(() => {
                if (this.pending.has(id)) {
                    this.pending.delete(id);
                    reject(new Error(`LSP request '${method}' timed out`));
                }
            }, 10_000);
        });
    }
    sendNotification(method, params) {
        const msg = { jsonrpc: "2.0", method, params };
        this.send(msg);
    }
    send(msg) {
        const body = JSON.stringify(msg);
        const header = `Content-Length: ${Buffer.byteLength(body)}\r\n\r\n`;
        this.process.stdin?.write(header + body);
    }
    kill() {
        try {
            this.process.stdin?.end();
            this.process.kill();
        }
        catch {
            // Already dead
        }
    }
}
// ── LSP Client Implementation ─────────────────────────────────────
class LspClientImpl {
    transport;
    projectRoot;
    openedFiles = new Set();
    lastRequestAt = Date.now();
    reaped = false;
    constructor(transport, projectRoot) {
        this.transport = transport;
        this.projectRoot = projectRoot;
    }
    static async create(command, projectRoot) {
        const transport = new LspTransport(command);
        const client = new LspClientImpl(transport, projectRoot);
        await client.initialize();
        return client;
    }
    async initialize() {
        const rootUri = pathToFileURL(this.projectRoot).toString();
        await this.transport.sendRequest("initialize", {
            processId: process.pid,
            rootUri,
            capabilities: {
                textDocument: {
                    definition: { dynamicRegistration: false },
                    references: { dynamicRegistration: false },
                },
            },
            workspaceFolders: [{ uri: rootUri, name: "root" }],
        });
        this.transport.sendNotification("initialized", {});
    }
    ensureFileOpen(filePath) {
        const absPath = resolve(this.projectRoot, filePath);
        const uri = pathToFileURL(absPath).toString();
        if (this.openedFiles.has(uri))
            return;
        this.openedFiles.add(uri);
        let text;
        try {
            text = readFileSync(absPath, "utf-8");
        }
        catch {
            return; // File doesn't exist — skip
        }
        const ext = extname(filePath);
        const langId = getLangId(ext);
        this.transport.sendNotification("textDocument/didOpen", {
            textDocument: {
                uri,
                languageId: langId,
                version: 1,
                text,
            },
        });
    }
    async getDefinition(filePath, line, character) {
        this.lastRequestAt = Date.now();
        try {
            this.ensureFileOpen(filePath);
            const absPath = resolve(this.projectRoot, filePath);
            const uri = pathToFileURL(absPath).toString();
            const result = await this.transport.sendRequest("textDocument/definition", {
                textDocument: { uri },
                position: { line, character },
            });
            return parseLspLocation(result);
        }
        catch {
            return null;
        }
    }
    async getReferences(filePath, line, character) {
        this.lastRequestAt = Date.now();
        try {
            this.ensureFileOpen(filePath);
            const absPath = resolve(this.projectRoot, filePath);
            const uri = pathToFileURL(absPath).toString();
            const result = await this.transport.sendRequest("textDocument/references", {
                textDocument: { uri },
                position: { line, character },
                context: { includeDeclaration: false },
            });
            return parseLspLocations(result);
        }
        catch {
            return [];
        }
    }
    getIdleMs(now = Date.now()) {
        return now - this.lastRequestAt;
    }
    isReaped() {
        return this.reaped;
    }
    async shutdown() {
        if (this.reaped)
            return;
        this.reaped = true;
        try {
            await this.transport.sendRequest("shutdown", null);
            this.transport.sendNotification("exit", null);
        }
        catch {
            // Ignore shutdown errors
        }
        // Small delay to let exit notification flush, then force kill
        setTimeout(() => this.transport.kill(), 500);
    }
}
// ── Helpers ───────────────────────────────────────────────────────
function getLangId(ext) {
    switch (ext) {
        case ".ts": return "typescript";
        case ".tsx": return "typescriptreact";
        case ".js": return "javascript";
        case ".jsx": return "javascriptreact";
        case ".py": return "python";
        case ".swift": return "swift";
        default: return "plaintext";
    }
}
function parseLspLocation(result) {
    if (!result)
        return null;
    // Can be a single Location, an array of Locations, or a LocationLink array
    const loc = Array.isArray(result) ? result[0] : result;
    if (!loc)
        return null;
    // Location: { uri, range: { start: { line, character } } }
    if (loc.uri && loc.range?.start) {
        return {
            uri: loc.uri,
            line: loc.range.start.line,
            character: loc.range.start.character,
        };
    }
    // LocationLink: { targetUri, targetRange: { start: { line, character } } }
    if (loc.targetUri && loc.targetRange?.start) {
        return {
            uri: loc.targetUri,
            line: loc.targetRange.start.line,
            character: loc.targetRange.start.character,
        };
    }
    return null;
}
function parseLspLocations(result) {
    if (!Array.isArray(result))
        return [];
    const locations = [];
    for (const item of result) {
        const loc = parseLspLocation(item);
        if (loc)
            locations.push(loc);
    }
    return locations;
}
// ── Factory — Session-Cached Client ──────────────────────────────
/** Cache: projectRoot -> serverBinary -> LspClient */
const clientCache = new Map();
let heartbeatTimer = null;
function startHeartbeatIfNeeded() {
    if (heartbeatTimer)
        return;
    heartbeatTimer = setInterval(reapIdle, getHeartbeatMs());
    // Don't keep the process alive just for this timer (important for CLI use).
    heartbeatTimer.unref?.();
}
function stopHeartbeat() {
    if (heartbeatTimer) {
        clearInterval(heartbeatTimer);
        heartbeatTimer = null;
    }
}
async function reapIdle() {
    const now = Date.now();
    const idleThreshold = getIdleThresholdMs();
    const reapPromises = [];
    for (const [root, byCmd] of clientCache) {
        for (const [cmdKey, client] of byCmd) {
            if (!(client instanceof LspClientImpl))
                continue;
            if (client.getIdleMs(now) >= idleThreshold) {
                byCmd.delete(cmdKey);
                reapPromises.push(client.shutdown());
            }
        }
        if (byCmd.size === 0) {
            clientCache.delete(root);
        }
    }
    if (clientCache.size === 0) {
        stopHeartbeat();
    }
    await Promise.allSettled(reapPromises);
}
/**
 * Get or create an LSP client for the given file and project root.
 * Returns null silently if no language server is available.
 */
export async function getLspClient(filePath, projectRoot) {
    const command = detectLanguageServer(filePath);
    if (!command)
        return null;
    let projectClients = clientCache.get(projectRoot);
    if (!projectClients) {
        projectClients = new Map();
        clientCache.set(projectRoot, projectClients);
    }
    const cacheKey = command.binary;
    const existing = projectClients.get(cacheKey);
    if (existing)
        return existing;
    try {
        const client = await LspClientImpl.create(command, projectRoot);
        projectClients.set(cacheKey, client);
        startHeartbeatIfNeeded();
        return client;
    }
    catch {
        // Server failed to start — return null, don't cache the failure
        return null;
    }
}
/**
 * Shutdown all cached LSP clients. Call on server exit.
 */
export async function shutdownAllLspClients() {
    stopHeartbeat();
    const shutdowns = [];
    for (const [, projectClients] of clientCache) {
        for (const [, client] of projectClients) {
            shutdowns.push(client.shutdown());
        }
    }
    await Promise.allSettled(shutdowns);
    clientCache.clear();
}
/** Test hook: force a reap pass synchronously. */
export async function __test_reapIdle() {
    await reapIdle();
}
/** Test hook: stop the heartbeat timer (used in afterEach). */
export function __test_stopHeartbeat() {
    stopHeartbeat();
}
//# sourceMappingURL=lsp-client.js.map