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

// ─── Public types ───────────────────────────────────────────────────

export interface IndexResult {
  filesIndexed: number;
  symbolsFound: number;
  edgesCreated: number;
  filesDeleted: number;
  durationMs: number;
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
   */
  async index(): Promise<IndexResult> {
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

    // Step 3: Mark agent concepts as stale for changed files
    if (changed.length > 0) {
      await this.storage.markConceptsStale(changed);
    }

    // Step 4: Purge old nodes for changed and deleted files
    const filesToPurge = [...changed, ...deleted];
    if (filesToPurge.length > 0) {
      await this.storage.purgeFileNodes(filesToPurge);
    }

    // Step 5: Parse and index new/changed files
    let symbolsFound = 0;
    let edgesCreated = 0;

    // First pass: create file and symbol nodes, collect import info
    const allSymbolIds = new Set<string>();
    const symbolNameMap = new Map<string, string>(); // symbolName -> symbolNodeId

    // We need existing symbols too (from files not being re-indexed)
    const existingSymbols = await this.storage.queryNodes({ type: "symbol" });
    for (const sym of existingSymbols) {
      const symbolNode = sym as SymbolNode;
      allSymbolIds.add(symbolNode.id);
      symbolNameMap.set(symbolNode.name, symbolNode.id);
    }

    const importEdges: Array<{
      fromFileId: string;
      importPath: string;
      fromRelPath: string;
    }> = [];
    const callEdges: Array<{
      fromFileId: string;
      callee: string;
    }> = [];

    for (const relPath of filesToProcess) {
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

      // Collect calls for second pass
      for (const call of parseResult.calls) {
        callEdges.push({
          fromFileId: fileId,
          callee: call.callee,
        });
      }
    }

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

    // Third pass: create call edges matching callee names to symbols
    for (const { fromFileId, callee } of callEdges) {
      // Try exact match first, then try without object prefix (e.g., "MathHelper.square" -> "MathHelper.square")
      const symbolId = symbolNameMap.get(callee);
      if (symbolId) {
        const edgeId = `edge:calls:${fromFileId}:${symbolId}`;
        const callEdge: GraphEdge = {
          id: edgeId,
          type: "calls",
          source: "static",
          fromId: fromFileId,
          toId: symbolId,
          createdAt: now,
        };
        await this.storage.upsertEdge(callEdge);
        edgesCreated++;
      }
    }

    const durationMs = Date.now() - startTime;

    return {
      filesIndexed: filesToProcess.length,
      symbolsFound,
      edgesCreated,
      filesDeleted: deleted.length,
      durationMs,
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
