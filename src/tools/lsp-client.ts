/**
 * Lightweight LSP client — spawns language servers on demand for precise symbol resolution.
 *
 * Supports: textDocument/definition, textDocument/references
 * Protocol: JSON-RPC over stdio with Content-Length framing
 *
 * The agent never sees LSP — this is an internal enhancement for context.ts.
 * If no language server is available, everything falls back gracefully.
 */

import { spawn, execSync, type ChildProcess } from "node:child_process";
import { readFileSync } from "node:fs";
import { extname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

// ── Public Interface ──────────────────────────────────────────────

export interface LspLocation {
  uri: string;
  line: number;
  character: number;
}

export interface LspClient {
  /** Try to get the definition location for a symbol at a given position */
  getDefinition(filePath: string, line: number, character: number): Promise<LspLocation | null>;

  /** Get all references to a symbol at a given position */
  getReferences(filePath: string, line: number, character: number): Promise<LspLocation[]>;

  /** Shutdown the server */
  shutdown(): Promise<void>;
}

// ── Language Server Detection ─────────────────────────────────────

function isOnPath(cmd: string): boolean {
  try {
    execSync(`which ${cmd}`, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

function detectLanguageServer(filePath: string): string | null {
  const ext = extname(filePath);

  if ([".ts", ".tsx", ".js", ".jsx"].includes(ext)) {
    if (isOnPath("typescript-language-server")) return "typescript-language-server --stdio";
  }
  if (ext === ".py") {
    if (isOnPath("pyright-langserver")) return "pyright-langserver --stdio";
    if (isOnPath("pylsp")) return "pylsp";
  }
  if (ext === ".swift") {
    if (isOnPath("sourcekit-lsp")) return "sourcekit-lsp";
  }
  return null;
}

// ── JSON-RPC Transport ────────────────────────────────────────────

interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: unknown;
}

interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
}

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

class LspTransport {
  private process: ChildProcess;
  private nextId = 1;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  private buffer = "";

  constructor(command: string) {
    const parts = command.split(/\s+/);
    this.process = spawn(parts[0], parts.slice(1), {
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.process.stdout?.on("data", (chunk: Buffer) => {
      this.buffer += chunk.toString();
      this.drainBuffer();
    });

    // Silently ignore stderr — language servers are chatty
    this.process.stderr?.on("data", () => {});

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

  private drainBuffer(): void {
    while (true) {
      const headerEnd = this.buffer.indexOf("\r\n\r\n");
      if (headerEnd === -1) return;

      const header = this.buffer.slice(0, headerEnd);
      const match = header.match(/Content-Length:\s*(\d+)/i);
      if (!match) {
        // Malformed — skip past the header
        this.buffer = this.buffer.slice(headerEnd + 4);
        continue;
      }

      const contentLength = parseInt(match[1], 10);
      const bodyStart = headerEnd + 4;

      if (this.buffer.length < bodyStart + contentLength) return; // incomplete

      const body = this.buffer.slice(bodyStart, bodyStart + contentLength);
      this.buffer = this.buffer.slice(bodyStart + contentLength);

      try {
        const msg = JSON.parse(body) as JsonRpcResponse;
        if (msg.id !== undefined && this.pending.has(msg.id)) {
          const entry = this.pending.get(msg.id)!;
          this.pending.delete(msg.id);
          if (msg.error) {
            entry.reject(new Error(msg.error.message));
          } else {
            entry.resolve(msg.result);
          }
        }
      } catch {
        // Ignore parse errors
      }
    }
  }

  async sendRequest(method: string, params?: unknown): Promise<unknown> {
    const id = this.nextId++;
    const msg: JsonRpcRequest = { jsonrpc: "2.0", id, method, params };
    return new Promise<unknown>((resolve, reject) => {
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

  sendNotification(method: string, params?: unknown): void {
    const msg: JsonRpcNotification = { jsonrpc: "2.0", method, params };
    this.send(msg);
  }

  private send(msg: JsonRpcRequest | JsonRpcNotification): void {
    const body = JSON.stringify(msg);
    const header = `Content-Length: ${Buffer.byteLength(body)}\r\n\r\n`;
    this.process.stdin?.write(header + body);
  }

  kill(): void {
    try {
      this.process.stdin?.end();
      this.process.kill();
    } catch {
      // Already dead
    }
  }
}

// ── LSP Client Implementation ─────────────────────────────────────

class LspClientImpl implements LspClient {
  private transport: LspTransport;
  private projectRoot: string;
  private openedFiles = new Set<string>();

  constructor(transport: LspTransport, projectRoot: string) {
    this.transport = transport;
    this.projectRoot = projectRoot;
  }

  static async create(command: string, projectRoot: string): Promise<LspClientImpl> {
    const transport = new LspTransport(command);
    const client = new LspClientImpl(transport, projectRoot);
    await client.initialize();
    return client;
  }

  private async initialize(): Promise<void> {
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

  private ensureFileOpen(filePath: string): void {
    const absPath = resolve(this.projectRoot, filePath);
    const uri = pathToFileURL(absPath).toString();

    if (this.openedFiles.has(uri)) return;
    this.openedFiles.add(uri);

    let text: string;
    try {
      text = readFileSync(absPath, "utf-8");
    } catch {
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

  async getDefinition(filePath: string, line: number, character: number): Promise<LspLocation | null> {
    try {
      this.ensureFileOpen(filePath);

      const absPath = resolve(this.projectRoot, filePath);
      const uri = pathToFileURL(absPath).toString();

      const result = await this.transport.sendRequest("textDocument/definition", {
        textDocument: { uri },
        position: { line, character },
      });

      return parseLspLocation(result);
    } catch {
      return null;
    }
  }

  async getReferences(filePath: string, line: number, character: number): Promise<LspLocation[]> {
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
    } catch {
      return [];
    }
  }

  async shutdown(): Promise<void> {
    try {
      await this.transport.sendRequest("shutdown", null);
      this.transport.sendNotification("exit", null);
    } catch {
      // Ignore shutdown errors
    }
    // Small delay to let exit notification flush, then force kill
    setTimeout(() => this.transport.kill(), 500);
  }
}

// ── Helpers ───────────────────────────────────────────────────────

function getLangId(ext: string): string {
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

function parseLspLocation(result: unknown): LspLocation | null {
  if (!result) return null;

  // Can be a single Location, an array of Locations, or a LocationLink array
  const loc = Array.isArray(result) ? result[0] : result;
  if (!loc) return null;

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

function parseLspLocations(result: unknown): LspLocation[] {
  if (!Array.isArray(result)) return [];

  const locations: LspLocation[] = [];
  for (const item of result) {
    const loc = parseLspLocation(item);
    if (loc) locations.push(loc);
  }
  return locations;
}

// ── Factory — Session-Cached Client ──────────────────────────────

/** Cache: projectRoot -> serverCommand -> LspClient */
const clientCache = new Map<string, Map<string, LspClient>>();

/**
 * Get or create an LSP client for the given file and project root.
 * Returns null silently if no language server is available.
 */
export async function getLspClient(filePath: string, projectRoot: string): Promise<LspClient | null> {
  const command = detectLanguageServer(filePath);
  if (!command) return null;

  let projectClients = clientCache.get(projectRoot);
  if (!projectClients) {
    projectClients = new Map();
    clientCache.set(projectRoot, projectClients);
  }

  const existing = projectClients.get(command);
  if (existing) return existing;

  try {
    const client = await LspClientImpl.create(command, projectRoot);
    projectClients.set(command, client);
    return client;
  } catch {
    // Server failed to start — return null, don't cache the failure
    return null;
  }
}

/**
 * Shutdown all cached LSP clients. Call on server exit.
 */
export async function shutdownAllLspClients(): Promise<void> {
  const shutdowns: Promise<void>[] = [];
  for (const [, projectClients] of clientCache) {
    for (const [, client] of projectClients) {
      shutdowns.push(client.shutdown());
    }
  }
  await Promise.allSettled(shutdowns);
  clientCache.clear();
}
