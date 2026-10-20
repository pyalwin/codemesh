/** Small, stable CLI observations. The shared tool handlers keep their full API. */

import type { QueryOutput } from "../tools/query.js";
import type { SourceOutput } from "../tools/source.js";
import type { NavigationOutput } from "../tools/navigation.js";
import type { RecalledNote } from "../tools/recall.js";

export function compactExploreResult(command: string, result: unknown): unknown {
  if ((command === "inspect" || command === "hydrate") && result && typeof result === "object" && "sources" in result) {
    const bounded = result as { sources: SourceOutput[] } & Record<string, unknown>;
    return {
      ...bounded,
      // Preserve exact code, source/body hashes, revision, per-reference outcomes,
      // and graph evidence. Drop only redundant display fields from source blocks.
      sources: bounded.sources.map(({ absolutePath: _absolutePath, summary: _summary, ...source }) => source),
    };
  }
  if (command === "search") {
    const search = result as QueryOutput;
    return {
      ...search,
      results: search.results.map(({ id, name, kind, filePath, matchedField, summary, signature, lineStart, lineEnd }) => ({
        id, name,
        ...(kind ? { kind } : {}),
        ...(filePath && !id.includes(filePath) ? { path: filePath } : {}),
        ...(signature ? { signature } : {}),
        ...(lineStart !== undefined && lineEnd !== undefined ? { lines: [lineStart, lineEnd] } : {}),
        ...(summary ? { summary: summary.length > 160 ? `${summary.slice(0, 159)}…` : summary } : {}),
        why: matchedField,
      })),
      total: search.total,
    };
  }
  if (command === "source") {
    const source = result as SourceOutput;
    return {
      status: source.status,
      id: source.id,
      path: source.filePath,
      lines: [source.lineStart, source.lineEnd],
      mode: source.mode,
      hash: source.sourceHash,
      source: source.source,
      ...(source.omittedRanges ? { omittedRanges: source.omittedRanges } : {}),
      ...(source.hint ? { hint: source.hint } : {}),
    };
  }
  if (command === "map" && result && typeof result === "object" && "entries" in result) {
    const map = result as NavigationOutput;
    const entry = ({ id, name, type, path, signature, hasChildren }: NavigationOutput["entries"][number]) => ({
      id, name, type,
      ...(path && !id.includes(path) ? { path } : {}),
      ...(signature ? { signature } : {}),
      ...(hasChildren ? { hasChildren: true } : {}),
    });
    return {
      ...map,
      node: entry(map.node), entries: map.entries.map(entry),
      ...(map.nextCursor ? { nextCursor: map.nextCursor } : {}),
    };
  }
  if (command === "recall") {
    const notes = (result as { notes: RecalledNote[] }).notes;
    return { notes: notes.map(({ id, summary, status, targets, author }) => ({
      id, summary, status, targets, ...(author ? { author } : {}),
    })) };
  }
  return result;
}
