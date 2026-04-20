/**
 * Tree-sitter Query-based parser. Used for languages that have a dedicated
 * `.scm` query file under `src/indexer/queries/` rather than a hand-written
 * walker.
 *
 * The query file must follow a capture-name convention:
 *   - Symbols: `@symbol.<kind>.name` (the identifier) and
 *              `@symbol.<kind>.node` (the full declaration node for scope).
 *     `<kind>` maps to a `SymbolKind` via `KIND_BY_CAPTURE` below.
 *   - Imports: `@import.source` (the module identifier).
 *   - Calls:   `@call.callee` (the callee identifier).
 */
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import Parser from "tree-sitter";
import type { ParsedSymbol, CallReference, ParseResult } from "./parser.js";
import type { SymbolKind } from "../graph/types.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Cache compiled queries keyed by language name. */
const queryCache = new Map<string, Parser.Query>();

function loadQuery(
  language: unknown,
  languageName: string,
  queryFile: string,
): Parser.Query {
  const cached = queryCache.get(languageName);
  if (cached) return cached;

  const queryPath = join(__dirname, "queries", queryFile);
  const src = readFileSync(queryPath, "utf-8");
  // tree-sitter's Query constructor is exposed as Parser.Query at runtime
  // but the type exports sometimes lag behind, hence the cast.
  const query = new (Parser as unknown as {
    Query: new (lang: unknown, src: string) => Parser.Query;
  }).Query(language, src);
  queryCache.set(languageName, query);
  return query;
}

/**
 * Maps a capture name prefix like "symbol.class" to its kind.
 * Query files must follow this convention in `@symbol.<kind>.name` / `.node`.
 */
const KIND_BY_CAPTURE: Record<string, SymbolKind> = {
  "symbol.class": "class",
  "symbol.function": "function",
  "symbol.method": "method",
  "symbol.interface": "interface",
  "symbol.struct": "class", // treat struct as class for graph purposes
  "symbol.enum": "enum",
  "symbol.type": "type",
  "symbol.const": "const",
};

interface RawMatch {
  captures: { name: string; node: Parser.SyntaxNode }[];
}

export function parseWithQuery(
  tree: Parser.Tree,
  language: unknown,
  languageName: string,
  queryFile: string,
  source: string,
): ParseResult {
  const query = loadQuery(language, languageName, queryFile);
  const matches = query.matches(tree.rootNode) as unknown as RawMatch[];

  const symbols: ParsedSymbol[] = [];
  const imports: string[] = [];
  const calls: CallReference[] = [];

  // Collect container ranges (classes/structs/interfaces) for scope lookup.
  const containers: { name: string; start: number; end: number }[] = [];
  for (const m of matches) {
    for (const cap of m.captures) {
      const prefix = cap.name.split(".").slice(0, 2).join(".");
      if (
        prefix === "symbol.class" ||
        prefix === "symbol.struct" ||
        prefix === "symbol.interface"
      ) {
        if (cap.name.endsWith(".node")) {
          const nameCap = m.captures.find((c) => c.name === `${prefix}.name`);
          if (nameCap) {
            containers.push({
              name: nameCap.node.text,
              start: cap.node.startPosition.row + 1,
              end: cap.node.endPosition.row + 1,
            });
          }
        }
      }
    }
  }

  function scopeOf(line: number): string[] {
    // Innermost-first: sort containers by ascending size, pick those whose
    // range contains `line`. The innermost (smallest) is the tightest scope.
    const enclosing = containers
      .filter((c) => c.start <= line && line <= c.end)
      .sort((a, b) => a.end - a.start - (b.end - b.start));
    return enclosing.map((c) => c.name).reverse();
  }

  // Emit symbols, imports, calls.
  for (const m of matches) {
    const byName = new Map<string, Parser.SyntaxNode>();
    for (const cap of m.captures) {
      byName.set(cap.name, cap.node);
    }

    // Symbols
    for (const [captureName, node] of byName) {
      if (!captureName.endsWith(".name")) continue;
      const kindPrefix = captureName.slice(0, -".name".length);
      const kind = KIND_BY_CAPTURE[kindPrefix];
      if (!kind) continue;

      const nodeCapture = byName.get(`${kindPrefix}.node`) ?? node;
      const lineStart = nodeCapture.startPosition.row + 1;
      const lineEnd = nodeCapture.endPosition.row + 1;

      // For functions/methods inside a container, the container shows up in scopeOf.
      // For container symbols (class/struct/interface), scope excludes self.
      const scopePath = scopeOf(lineStart).filter((n) => n !== node.text);

      symbols.push({
        name: node.text,
        kind,
        lineStart,
        lineEnd,
        signature: source
          .slice(
            nodeCapture.startIndex,
            Math.min(nodeCapture.endIndex, nodeCapture.startIndex + 200),
          )
          .split("\n")[0]
          .trim(),
        scopePath,
      });
    }

    // Imports
    const importSource = byName.get("import.source");
    if (importSource) {
      imports.push(importSource.text.replace(/^["']|["']$/g, ""));
    }

    // Calls
    const calleeNode = byName.get("call.callee");
    if (calleeNode) {
      calls.push({
        callee: calleeNode.text,
        lineNumber: calleeNode.startPosition.row + 1,
        scopePath: scopeOf(calleeNode.startPosition.row + 1),
      });
    }
  }

  // Post-pass: a symbol.function whose scope intersects a container's name
  // becomes a method.
  const containerNames = new Set(containers.map((c) => c.name));
  for (const sym of symbols) {
    if (
      sym.kind === "function" &&
      sym.scopePath.some((s) => containerNames.has(s))
    ) {
      sym.kind = "method";
    }
  }

  return { symbols, imports, calls };
}

export function resetQueryCache(): void {
  queryCache.clear();
}
