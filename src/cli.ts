#!/usr/bin/env node
// Commands: codemesh index | status | rebuild | help

import { SqliteBackend } from "./graph/sqlite.js";
import { Indexer } from "./indexer/indexer.js";
import { join } from "path";
import { mkdirSync, rmSync, existsSync } from "fs";

const args = process.argv.slice(2);
const command = args[0];
const projectRoot = process.env.CODEMESH_PROJECT_ROOT ?? process.cwd();
const dbDir = join(projectRoot, ".codemesh");
const dbPath = join(dbDir, "codemesh.db");

function printUsage(): void {
  console.log(`
codemesh — Code knowledge graph CLI

Usage:
  codemesh index     Index the project (incremental)
  codemesh status    Show graph statistics
  codemesh rebuild   Delete DB and re-index from scratch
  codemesh help      Show this help message

Environment:
  CODEMESH_PROJECT_ROOT   Project root directory (default: cwd)
`);
}

async function runIndex(): Promise<void> {
  mkdirSync(dbDir, { recursive: true });
  const storage = new SqliteBackend(dbPath);
  await storage.initialize();

  const indexer = new Indexer(storage, projectRoot);
  const result = await indexer.index();

  console.log(`Indexed ${result.filesIndexed} files`);
  console.log(`  Symbols found:  ${result.symbolsFound}`);
  console.log(`  Edges created:  ${result.edgesCreated}`);
  console.log(`  Files deleted:  ${result.filesDeleted}`);
  console.log(`  Duration:       ${result.durationMs}ms`);

  await storage.close();
}

async function runStatus(): Promise<void> {
  if (!existsSync(dbPath)) {
    console.log("No codemesh database found. Run `codemesh index` first.");
    return;
  }

  const storage = new SqliteBackend(dbPath);
  await storage.initialize();

  const stats = await storage.getStats();

  const totalNodes = Object.values(stats.nodeCount).reduce(
    (a, b) => a + b,
    0,
  );
  const totalEdges = Object.values(stats.edgeCount).reduce(
    (a, b) => a + b,
    0,
  );

  console.log(`Codemesh status for ${projectRoot}`);
  console.log();
  console.log(`Nodes: ${totalNodes}`);
  for (const [type, count] of Object.entries(stats.nodeCount)) {
    console.log(`  ${type}: ${count}`);
  }
  console.log();
  console.log(`Edges: ${totalEdges}`);
  for (const [type, count] of Object.entries(stats.edgeCount)) {
    console.log(`  ${type}: ${count}`);
  }
  console.log();
  console.log(`Stale concepts: ${stats.staleCount}`);
  console.log(
    `Last indexed:   ${stats.lastIndexedAt ?? "never"}`,
  );

  await storage.close();
}

async function runRebuild(): Promise<void> {
  if (existsSync(dbDir)) {
    rmSync(dbDir, { recursive: true });
    console.log("Deleted existing database.");
  }

  await runIndex();
}

async function main(): Promise<void> {
  switch (command) {
    case "index":
      await runIndex();
      break;
    case "status":
      await runStatus();
      break;
    case "rebuild":
      await runRebuild();
      break;
    case "help":
    case "--help":
    case "-h":
    case undefined:
      printUsage();
      break;
    default:
      console.error(`Unknown command: ${command}`);
      printUsage();
      process.exit(1);
  }
}

main().catch((err) => {
  console.error("Error:", err.message);
  process.exit(1);
});
