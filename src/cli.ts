#!/usr/bin/env node
// Commands: codemesh index | status | rebuild | explore | help

import { SqliteBackend } from "./graph/sqlite.js";
import { Indexer } from "./indexer/indexer.js";
import { handleQuery } from "./tools/query.js";
import { handleContext } from "./tools/context.js";
import { handleTrace } from "./tools/trace.js";
import { handleImpact } from "./tools/impact.js";
import { handleAnswer } from "./tools/answer.js";
import { handleReadSymbol } from "./tools/read-symbol.js";
import { semanticSearch } from "./indexer/embeddings.js";
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
  codemesh index [--with-embeddings] [--with-summaries]  Index the project (incremental)
  codemesh status                                Show graph statistics
  codemesh rebuild                               Delete DB and re-index from scratch
  codemesh explore search <query>                Search the knowledge graph (FTS)
  codemesh explore context <path...> [--symbol S] Get file/symbol context (multi-path supported)
  codemesh explore trace <symbol> [--depth N]    Trace a call chain from a symbol
  codemesh explore impact <path> [--symbol S]    Find reverse dependencies
  codemesh explore answer <question>             One-call context assembly for a question
  codemesh explore read <symbol>                 Read source code for a specific symbol
  codemesh explore semantic <query>              Semantic vector search (requires --with-embeddings)
  codemesh help                                  Show this help message

Environment:
  CODEMESH_PROJECT_ROOT   Project root directory (default: cwd)
`);
}

async function runIndex(): Promise<void> {
  const withEmbeddings = args.includes("--with-embeddings");
  const withSummaries = args.includes("--with-summaries");

  mkdirSync(dbDir, { recursive: true });
  const storage = new SqliteBackend(dbPath);
  await storage.initialize();

  const indexer = new Indexer(storage, projectRoot);
  const result = await indexer.index({ withEmbeddings, withSummaries });

  console.log(`Indexed ${result.filesIndexed} files`);
  console.log(`  Symbols found:  ${result.symbolsFound}`);
  console.log(`  Edges created:  ${result.edgesCreated}`);
  console.log(`  Files deleted:  ${result.filesDeleted}`);
  console.log(`  Duration:       ${result.durationMs}ms`);

  if (result.pagerankScore) {
    console.log(`  PageRank:       ${result.pagerankScore.computed} nodes scored`);
    if (result.pagerankScore.topNodes.length > 0) {
      console.log(`  Top nodes by PageRank:`);
      for (const { id, score } of result.pagerankScore.topNodes.slice(0, 5)) {
        console.log(`    ${id} — ${score.toFixed(6)}`);
      }
    }
  }

  if (result.summaries) {
    console.log(`  Summaries:      ${result.summaries.generated} generated, ${result.summaries.skipped} skipped`);
  }

  if (result.embeddings) {
    console.log(`  Embeddings:     ${result.embeddings.count} symbols embedded in ${result.embeddings.durationMs}ms`);
  }

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

function parseFlag(flagArgs: string[], flag: string): string | undefined {
  const idx = flagArgs.indexOf(flag);
  if (idx !== -1 && idx + 1 < flagArgs.length) {
    return flagArgs[idx + 1];
  }
  return undefined;
}

async function runExplore(): Promise<void> {
  const subcommand = args[1];
  if (!subcommand) {
    console.error("Usage: codemesh explore <search|context|trace|impact|answer> ...");
    process.exit(1);
  }

  if (!existsSync(dbPath)) {
    console.error("No codemesh database found. Run `codemesh index` first.");
    process.exit(1);
  }

  const storage = new SqliteBackend(dbPath);
  await storage.initialize();

  try {
    let result: unknown;

    switch (subcommand) {
      case "search": {
        const query = args.slice(2).filter((a) => !a.startsWith("--")).join(" ");
        if (!query) {
          console.error("Usage: codemesh explore search <query>");
          process.exit(1);
        }
        result = await handleQuery(storage, { query }, projectRoot);
        break;
      }
      case "context": {
        const symbol = parseFlag(args, "--symbol");
        // Collect positional args after "context", skipping --flags and their values
        const contextPaths: string[] = [];
        const contextArgs = args.slice(2);
        for (let i = 0; i < contextArgs.length; i++) {
          if (contextArgs[i].startsWith("--")) {
            i++; // skip the flag's value too
            continue;
          }
          contextPaths.push(contextArgs[i]);
        }

        if (contextPaths.length === 0) {
          console.error("Usage: codemesh explore context <path> [path2 ...] [--symbol name]");
          process.exit(1);
        }

        if (contextPaths.length === 1) {
          result = await handleContext(storage, { path: contextPaths[0], symbol }, projectRoot);
        } else {
          result = await handleContext(storage, { paths: contextPaths, symbol }, projectRoot);
        }
        break;
      }
      case "trace": {
        const symbol = args[2];
        if (!symbol) {
          console.error("Usage: codemesh explore trace <symbol> [--depth N] [--compact]");
          process.exit(1);
        }
        const depthStr = parseFlag(args, "--depth");
        const depth = depthStr ? parseInt(depthStr, 10) : 5;
        const compact = args.includes("--compact");
        result = await handleTrace(storage, { symbol, depth, compact }, projectRoot);
        break;
      }
      case "impact": {
        const path = args[2];
        if (!path) {
          console.error("Usage: codemesh explore impact <path> [--symbol name]");
          process.exit(1);
        }
        const symbol = parseFlag(args, "--symbol");
        result = await handleImpact(storage, { path, symbol });
        break;
      }
      case "answer": {
        const question = args.slice(2).filter((a) => !a.startsWith("--")).join(" ");
        if (!question) {
          console.error("Usage: codemesh explore answer <question>");
          process.exit(1);
        }
        result = await handleAnswer(storage, { question }, projectRoot);
        break;
      }
      case "read": {
        const readSymbol = args[2];
        if (!readSymbol) {
          console.error("Usage: codemesh explore read <symbol>");
          process.exit(1);
        }
        result = await handleReadSymbol(storage, { symbol: readSymbol }, projectRoot);
        break;
      }
      case "semantic": {
        const query = args.slice(2).filter((a) => !a.startsWith("--")).join(" ");
        if (!query) {
          console.error("Usage: codemesh explore semantic <query>");
          process.exit(1);
        }
        const limitStr = parseFlag(args, "--limit");
        const limit = limitStr ? parseInt(limitStr, 10) : 10;
        try {
          const matches = await semanticSearch(projectRoot, query, limit);
          result = { query, results: matches };
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          console.error(`Semantic search failed: ${msg}`);
          console.error("Did you run `codemesh index --with-embeddings` first?");
          process.exit(1);
        }
        break;
      }
      default:
        console.error(`Unknown explore subcommand: ${subcommand}`);
        console.error("Available: search, context, trace, impact, answer, semantic");
        process.exit(1);
    }

    console.log(JSON.stringify({ projectRoot, ...result as Record<string, unknown> }, null, 2));
  } finally {
    await storage.close();
  }
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
    case "explore":
      await runExplore();
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
