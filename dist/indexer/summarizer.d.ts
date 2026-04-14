/**
 * LLM-powered symbol summarization at index time.
 *
 * Groups symbols by file, sends one Claude Haiku call per file,
 * returns a Map of symbol ID → one-sentence summary.
 */
interface SummarizableSymbol {
    id: string;
    name: string;
    kind: string;
    signature: string;
    filePath: string;
    lineStart: number;
    lineEnd: number;
}
export declare function summarizeSymbols(projectRoot: string, symbols: SummarizableSymbol[]): Promise<Map<string, string>>;
export {};
