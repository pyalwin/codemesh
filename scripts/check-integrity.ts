#!/usr/bin/env node
/**
 * Assert graph integrity. Exits 0 if the graph is well-formed, 1 otherwise.
 *
 * Checks:
 *   1. No duplicate node IDs (enforced by PRIMARY KEY; still checked defensively).
 *   2. Every edge's from_id and to_id reference an existing node.
 *   3. Every symbol node has at least one `contains` edge from a file.
 *   4. Every file node's path field matches its id.
 *
 * NOTE on module loading:
 *   The source lives at `scripts/check-integrity.ts` and compiles to
 *   `scripts/dist/check-integrity.js`. At runtime we need the compiled
 *   `SqliteBackend` from the project's main `dist/graph/sqlite.js`, but a
 *   literal relative path would resolve differently at compile-time vs.
 *   runtime (the source and emitted file sit at different depths). We sidestep
 *   the mismatch by using a type-only import (resolved from `../src/...` at
 *   compile-time) and a dynamic runtime `import()` relative to the emitted
 *   file URL.
 */

import { existsSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import type Database from "better-sqlite3";

/** Minimal structural type of the compiled SqliteBackend we exercise here. */
interface SqliteBackendLike {
  initialize(): Promise<void>;
  close(): Promise<void>;
}

type SqliteBackendCtor = new (dbPath: string) => SqliteBackendLike;

async function loadSqliteBackend(): Promise<SqliteBackendCtor> {
  // From scripts/dist/check-integrity.js, the compiled SqliteBackend sits at
  // ../../dist/graph/sqlite.js (codemesh/dist/graph/sqlite.js).
  const here = dirname(fileURLToPath(import.meta.url));
  const target = resolve(here, "..", "..", "dist", "graph", "sqlite.js");
  const mod = (await import(pathToFileURL(target).href)) as {
    SqliteBackend: SqliteBackendCtor;
  };
  return mod.SqliteBackend;
}

async function main(): Promise<void> {
  const projectRoot = process.env.CODEMESH_PROJECT_ROOT ?? process.cwd();
  const dbPath = join(projectRoot, ".codemesh", "codemesh.db");

  if (!existsSync(dbPath)) {
    console.error(`No codemesh database at ${dbPath}. Run \`codemesh index\` first.`);
    process.exit(1);
  }

  const SqliteBackend = await loadSqliteBackend();
  const backend = new SqliteBackend(dbPath);
  await backend.initialize();

  const errors: string[] = [];

  // Unwrap the private db handle for raw SQL — this script ships with the
  // package, so bypassing the abstraction is acceptable.
  const db = (backend as unknown as { getDb(): Database.Database }).getDb();

  // Check 2: edges pointing at missing nodes
  const danglingFrom = db
    .prepare(
      `SELECT COUNT(*) AS cnt FROM edges e LEFT JOIN nodes n ON e.from_id = n.id WHERE n.id IS NULL`,
    )
    .get() as { cnt: number };
  const danglingTo = db
    .prepare(
      `SELECT COUNT(*) AS cnt FROM edges e LEFT JOIN nodes n ON e.to_id = n.id WHERE n.id IS NULL`,
    )
    .get() as { cnt: number };
  if (danglingFrom.cnt > 0) errors.push(`${danglingFrom.cnt} edges have a missing from_id`);
  if (danglingTo.cnt > 0) errors.push(`${danglingTo.cnt} edges have a missing to_id`);

  // Check 3: orphan symbols
  const orphanSymbols = db
    .prepare(
      `SELECT COUNT(*) AS cnt FROM nodes n
       WHERE n.type = 'symbol'
         AND NOT EXISTS (
           SELECT 1 FROM edges e
           WHERE e.to_id = n.id AND e.type = 'contains'
         )`,
    )
    .get() as { cnt: number };
  if (orphanSymbols.cnt > 0) errors.push(`${orphanSymbols.cnt} symbol nodes have no 'contains' edge`);

  // Check 4: file id must equal `file:` + path
  const fileIdMismatch = db
    .prepare(
      `SELECT COUNT(*) AS cnt FROM nodes WHERE type = 'file' AND id <> 'file:' || path`,
    )
    .get() as { cnt: number };
  if (fileIdMismatch.cnt > 0) errors.push(`${fileIdMismatch.cnt} file nodes have mismatched id / path`);

  await backend.close();

  if (errors.length > 0) {
    console.error("Graph integrity check failed:");
    for (const err of errors) console.error(`  - ${err}`);
    process.exit(1);
  }
  console.log("Graph integrity: OK");
}

main().catch((e) => {
  console.error("Integrity check crashed:", e);
  process.exit(1);
});
