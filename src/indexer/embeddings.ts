/**
 * LanceDB semantic search with local HuggingFace transformer embeddings.
 *
 * At index time (opt-in via --with-embeddings), generates embeddings for
 * symbol names + signatures using a local model. Stores in LanceDB.
 * At query time, does semantic vector search alongside FTS5.
 *
 * Zero API cost — all inference runs locally.
 */

import { readFileSync } from "node:fs";
import { join, isAbsolute } from "node:path";

const runtimeImport = new Function(
  "specifier",
  "return import(specifier)",
) as <T = any>(specifier: string) => Promise<T>;

// ── Model loading (lazy) ────────────────────────────────────────────

let embedder: any = null;

async function getEmbedder() {
  if (!embedder) {
    // Dynamic import to avoid loading the heavy transformers module
    // unless embeddings are explicitly requested
    const { pipeline } =
      await runtimeImport<typeof import("@huggingface/transformers")>(
        "@huggingface/transformers",
      );
    embedder = await pipeline("feature-extraction", "jinaai/jina-embeddings-v2-base-code", {
      dtype: "fp32",
    });
  }
  return embedder;
}

export async function generateEmbedding(text: string): Promise<number[]> {
  const embed = await getEmbedder();
  const result = await embed(text, { pooling: "mean", normalize: true });
  return Array.from(result.data as Float32Array);
}

// ── LanceDB storage ─────────────────────────────────────────────────

let db: any = null;

/**
 * Build the text to embed for a symbol node.
 * Richer text → better semantic alignment with natural-language queries.
 *
 * Format: [module: {filePath}]\n{name} {signature}\n{summary OR sourceLines}
 * Summary is preferred over source because it's denser signal.
 */
export function buildEmbeddingText(sym: {
  name: string;
  signature: string;
  filePath: string;
  summary?: string;
  sourceLines?: string;
}): string {
  const nameSig = sym.signature ? `${sym.name} ${sym.signature}` : sym.name;
  const parts: string[] = [`[module: ${sym.filePath}]`, nameSig];
  if (sym.summary) {
    parts.push(sym.summary);
  } else if (sym.sourceLines) {
    parts.push(sym.sourceLines);
  }
  return parts.join("\n");
}

/**
 * Read up to maxLines source lines for a symbol from disk.
 * Returns empty string if: the file is unreadable, filePath is absolute,
 * lineStart is out of range, or lineEnd < lineStart.
 * filePath must be relative to projectRoot.
 */
function readSourceLines(
  projectRoot: string,
  filePath: string,
  lineStart: number,
  lineEnd: number,
  maxLines = 30,
): string {
  // filePath must be relative to projectRoot — absolute paths produce wrong joins
  if (isAbsolute(filePath)) return "";
  try {
    const abs = join(projectRoot, filePath);
    const content = readFileSync(abs, "utf-8");
    const lines = content.split("\n");
    const end = Math.min(lineEnd, lineStart + maxLines - 1);
    // lineStart is 1-based (tree-sitter convention); convert to 0-based array index
    return lines.slice(lineStart - 1, end).join("\n");
  } catch {
    return "";
  }
}

async function getLanceDb(projectRoot: string) {
  if (!db) {
    const lancedb =
      await runtimeImport<typeof import("@lancedb/lancedb")>(
        "@lancedb/lancedb",
      );
    const dbPath = join(projectRoot, ".codemesh", "vectors");
    db = await lancedb.connect(dbPath);
  }
  return db;
}

/**
 * Reset the cached LanceDB connection (useful for tests or re-initialization).
 */
export function resetLanceDb(): void {
  db = null;
}

/**
 * Reset the cached embedder (useful for tests or model switching).
 */
export function resetEmbedder(): void {
  embedder = null;
}

/**
 * Stream-index symbol embeddings into LanceDB.
 *
 * Inference is serial per-call (one tensor set in flight at a time); `batchSize`
 * only controls the SQLite-write batch size and the disk-I/O fan-out for reading
 * source lines, not the inference fan-out. This caps peak RSS independent of
 * batchSize.
 */
export async function indexEmbeddings(
  projectRoot: string,
  symbols: Array<{
    id: string;
    name: string;
    signature: string;
    filePath: string;
    lineStart?: number;
    lineEnd?: number;
    summary?: string;
  }>,
  options?: {
    batchSize?: number;
    onBatch?: (completed: number, total: number) => void;
  },
): Promise<{ count: number; durationMs: number }> {
  const start = Date.now();
  if (symbols.length === 0) {
    return { count: 0, durationMs: Date.now() - start };
  }

  const batchSize = options?.batchSize ?? 256;
  const ldb = await getLanceDb(projectRoot);

  let table: any;
  try {
    table = await ldb.openTable("symbols");
  } catch {
    table = null; // will create on first batch
  }

  let processed = 0;
  for (let i = 0; i < symbols.length; i += batchSize) {
    const batch = symbols.slice(i, i + batchSize);

    // Pass 1: overlap disk I/O for readSourceLines across the batch.
    const prepared = await Promise.all(
      batch.map(async (sym) => {
        const sourceLines =
          !sym.summary && sym.lineStart && sym.lineEnd
            ? readSourceLines(
                projectRoot,
                sym.filePath,
                sym.lineStart,
                sym.lineEnd,
              )
            : undefined;
        const text = buildEmbeddingText({
          name: sym.name,
          signature: sym.signature,
          filePath: sym.filePath,
          summary: sym.summary,
          sourceLines,
        });
        return { sym, text };
      }),
    );

    // Pass 2: run inference strictly one-at-a-time so the embedder holds
    // at most one set of tensor buffers concurrently. This caps peak RSS
    // regardless of batchSize.
    const records = [];
    for (const { sym, text } of prepared) {
      const vector = await generateEmbedding(text);
      records.push({
        id: sym.id,
        name: sym.name,
        signature: sym.signature,
        filePath: sym.filePath,
        text,
        vector,
      });
    }

    if (!table) {
      table = await ldb.createTable("symbols", records);
    } else {
      await table
        .mergeInsert("id")
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute(records);
    }

    processed += records.length;
    options?.onBatch?.(processed, symbols.length);
  }

  return { count: processed, durationMs: Date.now() - start };
}

export async function semanticSearch(
  projectRoot: string,
  query: string,
  limit: number = 10,
): Promise<
  Array<{ id: string; name: string; filePath: string; score: number }>
> {
  const ldb = await getLanceDb(projectRoot);
  const queryVector = await generateEmbedding(query);

  try {
    const table = await ldb.openTable("symbols");
    const results = await table.search(queryVector).limit(limit).toArray();
    return results.map((r: any) => ({
      id: r.id,
      name: r.name,
      filePath: r.filePath,
      score: r._distance ?? 0,
    }));
  } catch {
    // Table doesn't exist or search failed — graceful fallback
    return [];
  }
}

/**
 * Delete embedding rows whose id matches any value in `ids`.
 * Returns the number of rows deleted. If the table does not exist
 * (e.g., embeddings never enabled for this project), returns 0 silently.
 * Throws if the table exists but the delete itself fails (bad predicate / IO).
 *
 * Inputs are chunked to keep predicate strings bounded — LanceDB's SQL layer
 * degrades on very large IN lists.
 */
export async function deleteEmbeddings(
  projectRoot: string,
  ids: string[],
): Promise<number> {
  if (ids.length === 0) return 0;

  const ldb = await getLanceDb(projectRoot);

  let table;
  try {
    table = await ldb.openTable("symbols");
  } catch {
    return 0; // no table yet
  }

  const CHUNK = 5000;
  let removed = 0;
  const unique = Array.from(new Set(ids));

  for (let i = 0; i < unique.length; i += CHUNK) {
    const batch = unique.slice(i, i + CHUNK);
    // LanceDB SQL requires quoted string literals; escape stray single-quotes
    // by doubling them (standard SQL literal rule). IDs in codemesh come from
    // filesystem paths + identifier names, so no backslash escaping is needed.
    const literals = batch
      .map((id) => `'${id.replace(/'/g, "''")}'`)
      .join(", ");
    const result = await table.delete(`id IN (${literals})`);
    removed += result.numDeletedRows;
  }

  return removed;
}

/**
 * Delete all embedding rows whose filePath matches one of the given paths.
 * Used by the indexer when a file is changed or deleted.
 *
 * Chunks input to keep predicate strings bounded — LanceDB's SQL layer
 * degrades on very large IN lists.
 * Throws if the table exists but the delete itself fails (bad predicate / IO).
 */
export async function deleteEmbeddingsByFilePaths(
  projectRoot: string,
  filePaths: string[],
): Promise<number> {
  if (filePaths.length === 0) return 0;

  const ldb = await getLanceDb(projectRoot);

  let table;
  try {
    table = await ldb.openTable("symbols");
  } catch {
    return 0;
  }

  const CHUNK = 5000;
  let removed = 0;
  const unique = Array.from(new Set(filePaths));

  for (let i = 0; i < unique.length; i += CHUNK) {
    const batch = unique.slice(i, i + CHUNK);
    // Safe quote-escape: filePaths come from project-relative filesystem
    // scans, so single-quote is the only SQL metacharacter realistic here.
    const literals = batch
      .map((p) => `'${p.replace(/'/g, "''")}'`)
      .join(", ");
    const result = await table.delete(`filePath IN (${literals})`);
    removed += result.numDeletedRows;
  }

  return removed;
}
