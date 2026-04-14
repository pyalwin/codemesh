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
            summary?: string;
        }>;
        hotspot?: {
            changeCount: number;
            lastChanged: string;
        };
        coChanges: string[];
        pagerankScore?: number;
    }>;
    symbolMap: MapNode[];
    concepts: Array<{
        summary: string;
        file: string;
    }>;
    workflows: Array<{
        name: string;
        description: string;
        files: string[];
    }>;
    suggestedReads: Array<{
        file: string;
        absolutePath: string;
        lines: string;
        reason: string;
        summary?: string;
    }>;
}
export declare function handleAnswer(storage: StorageBackend, input: AnswerInput, projectRoot: string): Promise<AnswerOutput>;
