/**
 * codemesh_context — Get full context for a file or symbol.
 *
 * Returns METADATA only — signatures, line numbers, edges, concepts, workflows.
 * Does NOT return source code. The agent reads specific files/functions it needs.
 * This keeps responses small and lets the agent choose what to read.
 *
 * When an LSP client is available, symbol resolution is enhanced with:
 * - Precise definition locations (definedAt)
 * - Reference counts (referencedBy)
 * - Disambiguation of multiple symbol matches
 */
import type { StorageBackend } from "../graph/storage.js";
import type { LspClient } from "./lsp-client.js";
export interface ContextInput {
    path?: string;
    paths?: string[];
    symbol?: string;
}
/** Symbol metadata — enough for the agent to decide whether to Read it */
export interface SymbolInfo {
    name: string;
    kind: string;
    signature: string;
    lineStart: number;
    lineEnd: number;
    lineCount: number;
    /** What this symbol calls (outgoing call edges) */
    calls: string[];
    /** What calls this symbol (incoming call edges) */
    calledBy: string[];
    /** Full call chain reachable from this symbol (depth 5) — shows the complete graph path */
    callChain: string[];
    /** Where this symbol is defined (via LSP), if available */
    definedAt?: {
        uri: string;
        line: number;
        character: number;
    };
    /** Number of references to this symbol (via LSP), if available */
    referencedBy?: number;
    /** PageRank centrality score — higher means more structurally important */
    pagerank?: number;
}
export interface ContextOutput {
    file: {
        path: string;
        absolutePath: string;
        name: string;
    } | null;
    /** Symbols with rich metadata — signature, line range, calls, calledBy */
    symbols: SymbolInfo[];
    /** Files this file imports */
    imports: string[];
    /** Files that import this file */
    importedBy: string[];
    /** Agent-written summaries about this file */
    concepts: Array<{
        summary: string;
        lastUpdatedBy: string;
    }>;
    /** Known workflows that traverse this file */
    workflows: Array<{
        name: string;
        description: string;
        files: string[];
    }>;
    /** Git hotspot data — change frequency and last changed date */
    hotspot?: {
        changeCount: number;
        lastChanged: string;
    };
    /** Files that frequently change together with this file */
    coChanges: string[];
}
export declare function handleContext(storage: StorageBackend, input: ContextInput, projectRoot?: string, lspClient?: LspClient | null): Promise<ContextOutput>;
