/**
 * codemesh_answer — One-call context assembly.
 *
 * Takes a natural language question, searches the graph, follows call chains,
 * and assembles everything into one structured response. The agent gets a
 * complete context package in ONE call.
 */
import type { StorageBackend } from "../graph/storage.js";
import { type MapNode } from "./map-tree.js";
export interface AnswerInput {
    question: string;
}
export interface AnswerOutput {
    question: string;
    relevantFiles: Array<{
        path: string;
        absolutePath: string;
        why: string;
        symbolCount: number;
        topSymbols: Array<{
            name: string;
            kind: string;
            signature?: string;
            lineStart?: number;
            lineEnd?: number;
            summary?: string;
        }>;
        hotspot?: {
            changeCount: number;
            lastChanged: string;
        };
        coChanges: Array<{
            path: string;
            confidence?: number;
            count?: number;
        }>;
        pagerankScore?: number;
    }>;
    symbolMap: MapNode[];
    concepts: Array<{
        summary: string;
        file: string;
        symbol?: string;
        stale?: boolean;
    }>;
    workflows: Array<{
        name: string;
        description: string;
        files: string[];
    }>;
    suggestedReads: Array<{
        file: string;
        absolutePath: string;
        /** Actual line range shown in the `snippet` field. When a symbol is
         *  truncated, this is the window selected by query-token density — not
         *  the full symbol range. Use `symbolRange` for the full symbol bounds. */
        lines: string;
        /** Full symbol range (lineStart-lineEnd). Only set when `lines` is a
         *  window inside a larger symbol (i.e., truncated=true). */
        symbolRange?: string;
        reason: string;
        signature?: string;
        snippet?: string;
        /** True if the symbol exceeds the ~30-line snippet cap. If false, the
         *  snippet contains the full symbol body — no Read needed. */
        truncated?: boolean;
        summary?: string;
    }>;
}
export declare function handleAnswer(storage: StorageBackend, input: AnswerInput, projectRoot: string): Promise<AnswerOutput>;
