/**
 * Lightweight LSP client — spawns language servers on demand for precise symbol resolution.
 *
 * Supports: textDocument/definition, textDocument/references
 * Protocol: JSON-RPC over stdio with Content-Length framing
 *
 * The agent never sees LSP — this is an internal enhancement for context.ts.
 * If no language server is available, everything falls back gracefully.
 */
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
/**
 * Get or create an LSP client for the given file and project root.
 * Returns null silently if no language server is available.
 */
export declare function getLspClient(filePath: string, projectRoot: string): Promise<LspClient | null>;
/**
 * Shutdown all cached LSP clients. Call on server exit.
 */
export declare function shutdownAllLspClients(): Promise<void>;
