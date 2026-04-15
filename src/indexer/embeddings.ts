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
import { join } from "node:path";

// ── Model loading (lazy) ────────────────────────────────────────────

let embedder: any = null;

async function getEmbedder() {
  if (!embedder) {
    // Dynamic import to avoid loading the heavy transformers module
    // unless embeddings are explicitly requested
    const { pipeline } = await import("@huggingface/transformers");
    embedder = await pipeline("feature-extraction", "Xenova/all-MiniLM-L6-v2", {
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
 * Returns empty string if the file is unreadable.
 */
function readSourceLines(
  projectRoot: string,
  filePath: string,
  lineStart: number,
  lineEnd: number,
  maxLines = 30,
): string {
  try {
    const abs = join(projectRoot, filePath);
    const content = readFileSync(abs, "utf-8");
    const lines = content.split("\n");
    const end = Math.min(lineEnd, lineStart + maxLines - 1);
    return lines.slice(lineStart - 1, end).join("\n");
  } catch {
    return "";
  }
}

async function getLanceDb(projectRoot: string) {
  if (!db) {
    const lancedb = await import("@lancedb/lancedb");
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
): Promise<{ count: number; durationMs: number }> {
  const start = Date.now();
  const ldb = await getLanceDb(projectRoot);

  // Generate embeddings for each symbol
  const records: Array<{
    id: string;
    name: string;
    signature: string;
    filePath: string;
    text: string;
    vector: number[];
  }> = [];

  for (const sym of symbols) {
    const sourceLines =
      !sym.summary && sym.lineStart && sym.lineEnd
        ? readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd)
        : undefined;
    const text = buildEmbeddingText({
      name: sym.name,
      signature: sym.signature,
      filePath: sym.filePath,
      summary: sym.summary,
      sourceLines,
    });
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

  if (records.length > 0) {
    // Create or overwrite table
    try {
      await ldb.dropTable("symbols");
    } catch {
      // Table may not exist yet — that's fine
    }
    await ldb.createTable("symbols", records);
  }

  return { count: records.length, durationMs: Date.now() - start };
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
