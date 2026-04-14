/**
 * Tree-sitter based parser that extracts symbols, imports, and call
 * relationships from source files.
 */
import type { SymbolKind } from "../graph/types.js";
export interface ParsedSymbol {
    name: string;
    kind: SymbolKind;
    lineStart: number;
    lineEnd: number;
    signature: string;
}
export interface CallReference {
    callee: string;
    lineNumber: number;
}
export interface ParseResult {
    symbols: ParsedSymbol[];
    imports: string[];
    calls: CallReference[];
}
/**
 * Parse a source file and extract symbols, imports, and call references.
 *
 * @param absolutePath - Full filesystem path (used to read the file)
 * @param relativePath - Project-relative path (currently unused but reserved for future use)
 * @returns ParseResult with extracted data, or empty result for unsupported files
 */
export declare function parseFile(absolutePath: string, _relativePath: string): Promise<ParseResult>;
