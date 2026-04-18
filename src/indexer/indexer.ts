/**
 * Indexing pipeline that walks a project directory, parses each file
 * with tree-sitter, and stores the results in the graph database.
 */

import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, relative, extname, dirname, resolve } from "node:path";
import ignore, { type Ignore } from "ignore";
import type { StorageBackend } from "../graph/storage.js";
import type {
  FileNode,
  SymbolNode,
  GraphEdge,
  EdgeType,
} from "../graph/types.js";
import { parseFile } from "./parser.js";
import { getSupportedExtensions } from "./languages.js";
import { analyzeGitHistory } from "./git-intel.js";
import { computePageRank } from "./pagerank.js";
import {
  indexEmbeddings,
  deleteEmbeddingsByFilePaths,
} from "./embeddings.js";
import { summarizeSymbols } from "./summarizer.js";

// ─── Public types ───────────────────────────────────────────────────

export interface IndexResult {
  filesIndexed: number;
  symbolsFound: number;
  edgesCreated: number;
  filesDeleted: number;
  durationMs: number;
  pagerankScore?: { computed: number; topNodes: Array<{ id: string; score: number }> };
  embeddings?: { count: number; durationMs: number };
  summaries?: { generated: number; skipped: number };
}

export interface IndexOptions {
  /** Run semantic embedding generation. Defaults to true; set to false to skip. */
  withEmbeddings?: boolean;
  withSummaries?: boolean;
}

// ─── Constants ──────────────────────────────────────────────────────

/** Directories that are always ignored regardless of .gitignore */
const ALWAYS_IGNORED = [
  "node_modules",
  ".git",
  ".codemesh",
  "dist",
  "build",
  "__pycache__",
];

// ─── Indexer class ──────────────────────────────────────────────────

export class Indexer {
  private storage: StorageBackend;
  private projectRoot: string;

  constructor(storage: StorageBackend, projectRoot: string) {
    this.storage = storage;
    this.projectRoot = projectRoot;
  }

  /**
   * Run the indexing pipeline:
   * 1. Walk project files (respecting ignores)
   * 2. Compute hashes and identify changed/new/deleted files
   * 3. Parse changed/new files and store in graph
   * 4. Remove deleted file nodes
   * 5. Git intelligence
   * 6. PageRank scoring
   * 7. Embeddings (on by default; pass withEmbeddings: false to skip)
   */
  async index(options?: IndexOptions): Promise<IndexResult> {
    const startTime = Date.now();

    const supportedExts = new Set(getSupportedExtensions());

    // Step 1: Walk directory and collect files with their hashes
    const ig = this.loadIgnoreRules();
    const fileHashes = new Map<string, string>();

    this.walkDirectory(this.projectRoot, ig, supportedExts, fileHashes);

    // Step 2: Identify changed, new, and deleted files
    const { changed, deleted, added } =
      await this.storage.getStaleFiles(fileHashes);

    const filesToProcess = [...changed, ...added];

    // Chunk DB mutations across three transactions (plus a batched middle
    // loop) to bound WAL pressure and let other readers see progress on
    // large repos.
    let symbolsFound = 0;
    let edgesCreated = 0;

    const FILE_BATCH_SIZE = 500;

    // Transaction A: concept staleness + purge (fast, small)
    await this.storage.beginTransaction();
    try {
      // Step 3: Mark agent concepts as stale for changed files
      if (changed.length > 0) {
        await this.storage.markConceptsStale(changed);
      }

      // Step 4: Purge old nodes for changed and deleted files
      const filesToPurge = [...changed, ...deleted];
      if (filesToPurge.length > 0) {
        await this.storage.purgeFileNodes(filesToPurge);
      }

      await this.storage.commitTransaction();
    } catch (e) {
      await this.storage.rollbackTransaction();
      throw e;
    }

    // Step 5: Parse and index new/changed files

    // First pass: create file and symbol nodes, collect import info
    const allSymbolIds = new Set<string>();
    const symbolNameMap = new Map<string, string>(); // symbolName -> symbolNodeId

    // Seed existing-symbol lookup from already-stored symbols in files we are
    // NOT re-indexing. Use the narrower query to avoid loading all symbols
    // (which could be millions on large repos).
    const unchangedFilePaths: string[] = [];
    {
      const fileNodes = await this.storage.queryNodes({ type: "file" });
      const processSet = new Set(filesToProcess);
      for (const n of fileNodes) {
        const path = (n as FileNode).path;
        if (!processSet.has(path)) {
          unchangedFilePaths.push(path);
        }
      }
    }
    if (unchangedFilePaths.length > 0) {
      const existingSymbols = (await this.storage.queryNodesByFilePaths(
        unchangedFilePaths,
      )) as SymbolNode[];
      for (const sym of existingSymbols) {
        allSymbolIds.add(sym.id);
        symbolNameMap.set(sym.name, sym.id);
      }
    }

    const importEdges: Array<{
      fromFileId: string;
      importPath: string;
      fromRelPath: string;
    }> = [];
    const callEdges: Array<{
      fromFileId: string;
      fromFilePath: string;
      callee: string;
      lineNumber: number;
    }> = [];
    // Per-file symbol ranges for attributing calls to their containing symbol.
    // Populated during pass 1 from freshly-parsed files.
    const fileSymbolRanges = new Map<
      string,
      Array<{ id: string; kind: string; lineStart: number; lineEnd: number }>
    >();
    const CALLABLE_KINDS = new Set(["function", "method", "const", "class"]);

    // Transaction B (chunked): parse + upsert files in batches
    for (let i = 0; i < filesToProcess.length; i += FILE_BATCH_SIZE) {
      const batch = filesToProcess.slice(i, i + FILE_BATCH_SIZE);
      await this.storage.beginTransaction();
      try {
        for (const relPath of batch) {
          const absPath = join(this.projectRoot, relPath);
          const hash = fileHashes.get(relPath)!;
          const now = new Date().toISOString();

          // Create file node
          const fileId = `file:${relPath}`;
          const fileNode: FileNode = {
            id: fileId,
            type: "file",
            source: "static",
            name: relPath.split("/").pop() || relPath,
            path: relPath,
            hash,
            lastIndexedAt: now,
            createdAt: now,
            updatedAt: now,
          };
          await this.storage.upsertNode(fileNode);

          // Parse the file
          const parseResult = await parseFile(absPath, relPath);

          // Create symbol nodes and contains edges
          for (const sym of parseResult.symbols) {
            const symbolId = `symbol:${relPath}:${sym.name}`;
            const symbolNode: SymbolNode = {
              id: symbolId,
              type: "symbol",
              source: "static",
              name: sym.name,
              kind: sym.kind,
              filePath: relPath,
              lineStart: sym.lineStart,
              lineEnd: sym.lineEnd,
              signature: sym.signature,
              createdAt: now,
              updatedAt: now,
            };
            await this.storage.upsertNode(symbolNode);
            allSymbolIds.add(symbolId);
            symbolNameMap.set(sym.name, symbolId);

            // Record range for containing-symbol attribution in pass 3
            if (!fileSymbolRanges.has(relPath)) {
              fileSymbolRanges.set(relPath, []);
            }
            fileSymbolRanges.get(relPath)!.push({
              id: symbolId,
              kind: sym.kind,
              lineStart: sym.lineStart,
              lineEnd: sym.lineEnd,
            });

            // Create contains edge: file -> symbol
            const containsEdge: GraphEdge = {
              id: `edge:contains:${fileId}:${symbolId}`,
              type: "contains",
              source: "static",
              fromId: fileId,
              toId: symbolId,
              createdAt: now,
            };
            await this.storage.upsertEdge(containsEdge);
            edgesCreated++;
            symbolsFound++;
          }

          // Collect imports for second pass
          for (const imp of parseResult.imports) {
            importEdges.push({
              fromFileId: fileId,
              importPath: imp,
              fromRelPath: relPath,
            });
          }

          // Collect calls for third pass (attribution happens there)
          for (const call of parseResult.calls) {
            callEdges.push({
              fromFileId: fileId,
              fromFilePath: relPath,
              callee: call.callee,
              lineNumber: call.lineNumber,
            });
          }
        }
        await this.storage.commitTransaction();
      } catch (e) {
        await this.storage.rollbackTransaction();
        throw e;
      }
    }

    // Transaction C: resolution passes (imports + calls)
    await this.storage.beginTransaction();
    try {
      // Second pass: create import edges between file nodes
      const now = new Date().toISOString();

      for (const { fromFileId, importPath, fromRelPath } of importEdges) {
        const resolvedPath = this.resolveImportPath(
          importPath,
          fromRelPath,
          fileHashes,
        );
        if (resolvedPath) {
          const toFileId = `file:${resolvedPath}`;
          const edgeId = `edge:imports:${fromFileId}:${toFileId}`;
          const importEdge: GraphEdge = {
            id: edgeId,
            type: "imports",
            source: "static",
            fromId: fromFileId,
            toId: toFileId,
            createdAt: now,
          };
          await this.storage.upsertEdge(importEdge);
          edgesCreated++;
        }
      }

      // Third pass: create call edges, attributed to the innermost containing
      // symbol (function/method/const/class) of the call site. Module-level
      // calls with no enclosing symbol fall back to a file-level edge.
      for (const {
        fromFileId,
        fromFilePath,
        callee,
        lineNumber,
      } of callEdges) {
        const symbolId = symbolNameMap.get(callee);
        if (!symbolId) continue;

        const ranges = fileSymbolRanges.get(fromFilePath) ?? [];
        let containingId: string | null = null;
        let smallestRangeSize = Number.POSITIVE_INFINITY;
        for (const r of ranges) {
          if (!CALLABLE_KINDS.has(r.kind)) continue;
          // Skip single-line consts — they're usually `const x = someCall()`,
          // not a function body containing calls. The enclosing function should
          // own those calls instead.
          if (r.kind === "const" && r.lineEnd === r.lineStart) continue;
          if (r.lineStart <= lineNumber && lineNumber <= r.lineEnd) {
            const size = r.lineEnd - r.lineStart;
            if (size < smallestRangeSize) {
              smallestRangeSize = size;
              containingId = r.id;
            }
          }
        }

        const fromId = containingId ?? fromFileId;
        const edgeId = `edge:calls:${fromId}:${symbolId}`;
        const callEdge: GraphEdge = {
          id: edgeId,
          type: "calls",
          source: "static",
          fromId,
          toId: symbolId,
          createdAt: now,
        };
        await this.storage.upsertEdge(callEdge);
        edgesCreated++;
      }

      await this.storage.commitTransaction();
    } catch (e) {
      await this.storage.rollbackTransaction();
      throw e;
    }

    // Step 5.5: Symbol summarization (opt-in) — generate LLM summaries
    let summariesResult: IndexResult["summaries"];
    if (options?.withSummaries) {
      try {
        // Collect symbols that need summaries (new/changed files or missing summary)
        const allSymbolNodes = await this.storage.queryNodes({ type: "symbol" });
        const needsSummary = allSymbolNodes.filter((n) => {
          const sym = n as SymbolNode;
          // Summarize if: in a newly processed file, or has no summary yet
          return filesToProcess.includes(sym.filePath) || !sym.summary;
        });

        if (needsSummary.length > 0) {
          const symbolsForSummary = needsSummary.map((n) => {
            const sym = n as SymbolNode;
            return {
              id: sym.id,
              name: sym.name,
              kind: sym.kind,
              signature: sym.signature,
              filePath: sym.filePath,
              lineStart: sym.lineStart,
              lineEnd: sym.lineEnd,
            };
          });

          const newSummaries = await summarizeSymbols(this.projectRoot, symbolsForSummary);

          if (newSummaries.size > 0) {
            await this.storage.beginTransaction();
            try {
              for (const [symbolId, summary] of newSummaries) {
                const node = await this.storage.getNode(symbolId);
                if (!node || node.type !== "symbol") continue;
                const updatedNode: SymbolNode = {
                  ...(node as SymbolNode),
                  summary,
                  updatedAt: new Date().toISOString(),
                };
                await this.storage.upsertNode(updatedNode);
              }
              await this.storage.commitTransaction();
            } catch (e) {
              await this.storage.rollbackTransaction();
            }
          }

          summariesResult = {
            generated: newSummaries.size,
            skipped: needsSummary.length - newSummaries.size,
          };
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        console.warn(`Warning: symbol summarization failed — ${msg}`);
      }
    }

    // Step 6: Git intelligence — hotspots and co-change pairs
    try {
      const gitIntel = await analyzeGitHistory(this.projectRoot);

      await this.storage.beginTransaction();
      try {
        const now = new Date().toISOString();

        // Store hotspot data as metadata on file nodes
        for (const hotspot of gitIntel.hotspots) {
          const fileId = `file:${hotspot.path}`;
          const fileNode = await this.storage.getNode(fileId);
          if (!fileNode || fileNode.type !== "file") continue;

          const updatedNode: FileNode = {
            ...fileNode,
            updatedAt: now,
            hotspot: {
              changeCount: hotspot.changeCount,
              lastChanged: hotspot.lastChanged,
            },
          };
          await this.storage.upsertNode(updatedNode);
        }

        // Store co-change pairs as edges between file nodes
        for (const pair of gitIntel.coChangePairs) {
          const fileAId = `file:${pair.fileA}`;
          const fileBId = `file:${pair.fileB}`;

          // Only create edges between files that exist in the graph
          const fileA = await this.storage.getNode(fileAId);
          const fileB = await this.storage.getNode(fileBId);
          if (!fileA || !fileB) continue;

          const edgeId = `edge:co_changes:${fileAId}:${fileBId}`;
          const coChangeEdge: GraphEdge = {
            id: edgeId,
            type: "co_changes",
            source: "static",
            fromId: fileAId,
            toId: fileBId,
            data: {
              coChangeCount: pair.coChangeCount,
              confidence: pair.confidence,
            },
            createdAt: now,
          };
          await this.storage.upsertEdge(coChangeEdge);
          edgesCreated++;
        }

        await this.storage.commitTransaction();
      } catch (e) {
        await this.storage.rollbackTransaction();
        // Git intelligence is non-critical — don't fail the entire index
      }
    } catch {
      // Git not available or not a git repo — skip silently
    }

    // Step 7: PageRank — compute centrality scores for all nodes
    let pagerankResult: IndexResult["pagerankScore"];
    try {
      const ranks = await computePageRank(this.storage);

      if (ranks.size > 0) {
        // Store pagerank scores on each node's data field via batch update
        await this.storage.beginTransaction();
        try {
          for (const [nodeId, score] of ranks) {
            const node = await this.storage.getNode(nodeId);
            if (!node) continue;
            const updatedNode = {
              ...node,
              updatedAt: new Date().toISOString(),
              pagerankScore: score,
            };
            await this.storage.upsertNode(updatedNode);
          }
          await this.storage.commitTransaction();
        } catch (e) {
          await this.storage.rollbackTransaction();
          // PageRank storage is non-critical — don't fail the entire index
        }

        // Build top-N summary for the result
        const sorted = [...ranks.entries()].sort((a, b) => b[1] - a[1]);
        pagerankResult = {
          computed: ranks.size,
          topNodes: sorted.slice(0, 10).map(([id, score]) => ({ id, score })),
        };
      }
    } catch {
      // PageRank computation is non-critical — skip silently
    }

    // Step 8: Embeddings (opt-in) — incremental semantic index update
    let embeddingsResult: IndexResult["embeddings"];
    if (options?.withEmbeddings !== false) {
      // 8a. Delete embeddings for purged files (changed or deleted).
      //     Non-fatal: if cleanup fails, the stale rows remain but we
      //     still proceed to 8b so new symbols get embedded.
      const purgedFiles = [...changed, ...deleted];
      if (purgedFiles.length > 0) {
        try {
          await deleteEmbeddingsByFilePaths(this.projectRoot, purgedFiles);
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          console.warn(`Warning: embedding cleanup failed — ${msg}`);
        }
      }

      // 8b. Embed only symbols in files we just processed.
      try {
        if (filesToProcess.length > 0) {
          const freshSymbols = (await this.storage.queryNodesByFilePaths(
            filesToProcess,
          )) as SymbolNode[];

          if (freshSymbols.length > 0) {
            const symbolsForEmbedding = freshSymbols.map((sym) => ({
              id: sym.id,
              name: sym.name,
              signature: sym.signature,
              filePath: sym.filePath,
              lineStart: sym.lineStart,
              lineEnd: sym.lineEnd,
              summary: sym.summary,
            }));

            embeddingsResult = await indexEmbeddings(
              this.projectRoot,
              symbolsForEmbedding,
            );
          } else {
            embeddingsResult = { count: 0, durationMs: 0 };
          }
        } else {
          embeddingsResult = { count: 0, durationMs: 0 };
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        console.warn(`Warning: embedding generation failed — ${msg}`);
      }
    }

    const durationMs = Date.now() - startTime;

    return {
      filesIndexed: filesToProcess.length,
      symbolsFound,
      edgesCreated,
      filesDeleted: deleted.length,
      durationMs,
      pagerankScore: pagerankResult,
      embeddings: embeddingsResult,
      summaries: summariesResult,
    };
  }

  // ─── Directory walking ────────────────────────────────────────────

  /**
   * Load .gitignore rules from the project root (if present)
   * and add the always-ignored patterns.
   */
  private loadIgnoreRules(): Ignore {
    const ig = ignore();

    // Always ignore these directories
    for (const pattern of ALWAYS_IGNORED) {
      ig.add(pattern);
    }

    // Load .gitignore if it exists
    const gitignorePath = join(this.projectRoot, ".gitignore");
    if (existsSync(gitignorePath)) {
      const content = readFileSync(gitignorePath, "utf-8");
      ig.add(content);
    }

    return ig;
  }

  /**
   * Recursively walk the directory tree, collecting file paths and their
   * stat-based change keys (mtime + size). This avoids reading file content
   * for the ~90% of files that haven't changed between index runs.
   */
  private walkDirectory(
    dir: string,
    ig: Ignore,
    supportedExts: Set<string>,
    fileHashes: Map<string, string>,
  ): void {
    let entries: string[];
    try {
      entries = readdirSync(dir);
    } catch {
      return; // Permission denied or missing directory
    }

    for (const entry of entries) {
      const absPath = join(dir, entry);
      const relPath = relative(this.projectRoot, absPath);

      // Check if ignored
      if (ig.ignores(relPath)) continue;

      let stat;
      try {
        stat = statSync(absPath);
      } catch {
        continue; // Broken symlink or permission issue
      }

      if (stat.isDirectory()) {
        this.walkDirectory(absPath, ig, supportedExts, fileHashes);
      } else if (stat.isFile()) {
        const ext = extname(absPath);
        if (!supportedExts.has(ext)) continue;

        // Use stat-based fast key instead of reading file content for SHA256
        const hash = `${stat.mtimeMs}:${stat.size}`;
        fileHashes.set(relPath, hash);
      }
    }
  }

  // ─── Import resolution ────────────────────────────────────────────

  /**
   * Resolve a relative import path (e.g., "./math") to an actual
   * project-relative file path, trying common extensions.
   */
  private resolveImportPath(
    importPath: string,
    fromRelPath: string,
    knownFiles: Map<string, string>,
  ): string | null {
    // Only resolve relative imports
    if (!importPath.startsWith(".")) return null;

    const fromDir = dirname(fromRelPath);
    const resolved = join(fromDir, importPath).replace(/\\/g, "/");

    // Try exact match first
    if (knownFiles.has(resolved)) return resolved;

    // Try common extensions
    const extensions = getSupportedExtensions();
    for (const ext of extensions) {
      const withExt = resolved + ext;
      if (knownFiles.has(withExt)) return withExt;
    }

    // Try index files
    for (const ext of extensions) {
      const indexPath = join(resolved, `index${ext}`).replace(/\\/g, "/");
      if (knownFiles.has(indexPath)) return indexPath;
    }

    return null;
  }
}
