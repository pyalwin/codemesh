import Parser from "tree-sitter";
import type { ParseResult } from "./parser.js";
export declare function parseWithQuery(tree: Parser.Tree, language: unknown, languageName: string, queryFile: string, source: string): ParseResult;
export declare function resetQueryCache(): void;
