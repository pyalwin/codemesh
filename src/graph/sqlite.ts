import Database from "better-sqlite3";
import type {
  GraphNode,
  GraphEdge,
  NodeFilter,
  SearchResult,
  SearchOptions,
  TraversalResult,
  FileNode,
  SymbolNode,
  ConceptNode,
  WorkflowNode,
  NodeType,
} from "./types.js";
import type { StorageBackend } from "./storage.js";

/**
 * Generate unique trigrams (3-character substrings) from text.
 * Used to power partial/substring symbol matching.
 */
function generateTrigrams(text: string): string[] {
  const normalized = text.toLowerCase();
  const trigrams: string[] = [];
  for (let i = 0; i <= normalized.length - 3; i++) {
    trigrams.push(normalized.slice(i, i + 3));
  }
  return [...new Set(trigrams)]; // dedupe
}

/** All aliases here are internal SQL identifiers; path values stay bound parameters. */
function searchFilter(scope: string | undefined, path: string | undefined): { sql: string; params: Record<string, string> } {
  const conditions: string[] = [];
  const params: Record<string, string> = {};
  const type = scope === "files" ? "file" : scope === "symbols" ? "symbol" : scope === "workflows" ? "workflow" : undefined;
  if (type) {
    conditions.push("n.type = @searchType");
    params.searchType = type;
  }
  if (path !== undefined) {
    params.searchPath = path;
    params.searchPrefix = `${path}/`;
    params.searchUpper = `${path}0`;
    const within = (column: string) => `(${column} = @searchPath OR (${column} >= @searchPrefix AND ${column} < @searchUpper))`;
    conditions.push(`(
      (n.type = 'file' AND ${within("n.path")}) OR
      (n.type = 'symbol' AND ${within("json_extract(n.data, '$.filePath')")}) OR
      (n.type = 'concept' AND EXISTS (
        SELECT 1 FROM nodes target WHERE target.id = json_extract(n.data, '$.targetId')
        AND ${within("CASE WHEN target.type = 'file' THEN target.path ELSE json_extract(target.data, '$.filePath') END")}
      )) OR
      (n.type = 'workflow' AND EXISTS (
        SELECT 1 FROM json_each(n.data, '$.fileSequence') files WHERE ${within("files.value")}
      ))
    )`);
  }
  return { sql: conditions.length ? ` AND ${conditions.join(" AND ")}` : "", params };
}

// Fields stored directly in the nodes table (not in the JSON `data` column)
const BASE_FIELDS = new Set([
  "id",
  "type",
  "source",
  "name",
  "createdAt",
  "updatedAt",
]);

/**
 * SQLite storage backend for the code knowledge graph.
 * Uses better-sqlite3 (synchronous API) with WAL mode and FTS5 full-text search.
 */
export class SqliteBackend implements StorageBackend {
  private db: Database.Database | null = null;
  private readonly dbPath: string;
  private inTransaction = false;
  private graphDirty = false;

  constructor(dbPath: string) {
    this.dbPath = dbPath;
  }

  // ── Lifecycle ──────────────────────────────────────────────────────

  async initialize(): Promise<void> {
    this.db = new Database(this.dbPath);
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("busy_timeout = 5000");
    this.db.pragma("foreign_keys = ON");
    this.createSchema();
  }

  async close(): Promise<void> {
    if (this.db) {
      this.db.close();
      this.db = null;
    }
  }

  private markGraphDirty(): void {
    if (this.inTransaction) {
      this.graphDirty = true;
    } else {
      this.getDb().prepare("UPDATE cache_meta SET value = value + 1 WHERE key = 'graph_version'").run();
    }
  }

  private getDb(): Database.Database {
    if (!this.db) {
      throw new Error("Database not initialized. Call initialize() first.");
    }
    return this.db;
  }

  // ── Schema ─────────────────────────────────────────────────────────

  private createSchema(): void {
    const db = this.getDb();

    db.exec(`
      CREATE TABLE IF NOT EXISTS nodes (
        id         TEXT PRIMARY KEY,
        type       TEXT NOT NULL,
        source     TEXT NOT NULL,
        name       TEXT NOT NULL,
        path       TEXT,
        hash       TEXT,
        data       TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );

      CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
      CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(path);
      CREATE INDEX IF NOT EXISTS idx_nodes_type_path ON nodes(type, path);
      CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);

      CREATE TABLE IF NOT EXISTS edges (
        id         TEXT PRIMARY KEY,
        type       TEXT NOT NULL,
        source     TEXT NOT NULL,
        from_id    TEXT NOT NULL,
        to_id      TEXT NOT NULL,
        data       TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (from_id) REFERENCES nodes(id) ON DELETE CASCADE,
        FOREIGN KEY (to_id)   REFERENCES nodes(id) ON DELETE CASCADE
      );

      CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
      CREATE INDEX IF NOT EXISTS idx_edges_to   ON edges(to_id);
      CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);

      CREATE TABLE IF NOT EXISTS cache_meta (
        key TEXT PRIMARY KEY,
        value INTEGER NOT NULL
      );
      INSERT OR IGNORE INTO cache_meta(key, value) VALUES ('graph_version', 0);
      CREATE TABLE IF NOT EXISTS retrieval_cache (
        key TEXT PRIMARY KEY,
        graph_version INTEGER NOT NULL,
        results TEXT NOT NULL,
        hit_count INTEGER NOT NULL DEFAULT 0,
        last_used_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_retrieval_cache_used ON retrieval_cache(last_used_at);
      CREATE TABLE IF NOT EXISTS session_deliveries (
        session_id TEXT NOT NULL,
        context_epoch TEXT NOT NULL,
        node_id TEXT NOT NULL,
        mode TEXT NOT NULL,
        source_hash TEXT NOT NULL,
        delivered_at TEXT NOT NULL,
        PRIMARY KEY (session_id, context_epoch, node_id, mode, source_hash)
      );
      CREATE INDEX IF NOT EXISTS idx_session_deliveries_session ON session_deliveries(session_id, context_epoch);
      CREATE TABLE IF NOT EXISTS context_metrics (
        key TEXT PRIMARY KEY,
        value INTEGER NOT NULL DEFAULT 0
      );

      -- Trigram index for partial/substring symbol matching
      CREATE TABLE IF NOT EXISTS trigrams (
        trigram TEXT NOT NULL,
        node_id TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_trigrams_tri ON trigrams(trigram);
      CREATE INDEX IF NOT EXISTS idx_trigrams_node ON trigrams(node_id);

      -- FTS5 virtual table for full-text search on node content
      CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
        node_id UNINDEXED,
        name,
        signature,
        summary,
        description,
        tokenize='porter'
      );

      -- Triggers to keep FTS5 in sync with the nodes table
      CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
        INSERT INTO nodes_fts(node_id, name, signature, summary, description)
        VALUES (
          new.id,
          new.name,
          COALESCE(json_extract(new.data, '$.signature'), ''),
          COALESCE(json_extract(new.data, '$.summary'), ''),
          COALESCE(json_extract(new.data, '$.description'), '')
        );
      END;

      CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
        DELETE FROM nodes_fts WHERE node_id = old.id;
      END;

      CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
        DELETE FROM nodes_fts WHERE node_id = old.id;
        INSERT INTO nodes_fts(node_id, name, signature, summary, description)
        VALUES (
          new.id,
          new.name,
          COALESCE(json_extract(new.data, '$.signature'), ''),
          COALESCE(json_extract(new.data, '$.summary'), ''),
          COALESCE(json_extract(new.data, '$.description'), '')
        );
      END;
    `);
  }

  // ── Node Serialization ─────────────────────────────────────────────

  /**
   * Serialize a GraphNode into table columns.
   * Common fields go into dedicated columns; the rest into a JSON `data` blob.
   */
  private serializeNode(node: GraphNode): {
    id: string;
    type: string;
    source: string;
    name: string;
    path: string | null;
    hash: string | null;
    data: string;
    created_at: string;
    updated_at: string;
  } {
    const rest: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(node)) {
      if (!BASE_FIELDS.has(key) && key !== "path" && key !== "hash") {
        rest[key] = value;
      }
    }
    return {
      id: node.id,
      type: node.type,
      source: node.source,
      name: node.name,
      path: (node as FileNode).path ?? null,
      hash: (node as FileNode).hash ?? null,
      data: JSON.stringify(rest),
      created_at: node.createdAt,
      updated_at: node.updatedAt,
    };
  }

  /**
   * Reconstruct a typed GraphNode from a database row.
   */
  private deserializeNode(row: {
    id: string;
    type: string;
    source: string;
    name: string;
    path: string | null;
    hash: string | null;
    data: string | null;
    created_at: string;
    updated_at: string;
  }): GraphNode {
    const data = row.data ? JSON.parse(row.data) : {};

    const base = {
      id: row.id,
      type: row.type as NodeType,
      source: row.source,
      name: row.name,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    };

    switch (row.type) {
      case "file": {
        const fileNode: FileNode = {
          ...base,
          type: "file",
          source: "static",
          path: row.path!,
          hash: row.hash!,
          lastIndexedAt: data.lastIndexedAt ?? base.createdAt,
        };
        if (data.hotspot) {
          fileNode.hotspot = data.hotspot;
        }
        if (data.pagerankScore !== undefined) {
          fileNode.pagerankScore = data.pagerankScore;
        }
        return fileNode;
      }

      case "symbol": {
        const symbolNode: SymbolNode = {
          ...base,
          type: "symbol",
          source: "static",
          kind: data.kind,
          filePath: data.filePath ?? row.path ?? "",
          lineStart: data.lineStart ?? 0,
          lineEnd: data.lineEnd ?? 0,
          signature: data.signature ?? "",
          bodyHash: data.bodyHash,
          signatureHash: data.signatureHash,
        };
        if (data.summary !== undefined) {
          symbolNode.summary = data.summary;
        }
        if (data.pagerankScore !== undefined) {
          symbolNode.pagerankScore = data.pagerankScore;
        }
        return symbolNode;
      }

      case "concept":
        return {
          ...base,
          type: "concept",
          source: "agent",
          summary: data.summary ?? "",
          lastUpdatedBy: data.lastUpdatedBy ?? "",
          stale: data.stale ?? false,
          targetId: data.targetId,
          targetHash: data.targetHash,
          status: data.status,
          history: data.history,
        } as ConceptNode;

      case "workflow":
        return {
          ...base,
          type: "workflow",
          source: "agent",
          description: data.description ?? "",
          fileSequence: data.fileSequence ?? [],
          lastWalkedAt: data.lastWalkedAt ?? base.createdAt,
          stale: data.stale ?? false,
          fileHashes: data.fileHashes,
          status: data.status,
          history: data.history,
        } as WorkflowNode;

      default:
        throw new Error(`Unknown node type: ${row.type}`);
    }
  }

  // ── Node CRUD ──────────────────────────────────────────────────────

  async upsertNode(node: GraphNode): Promise<string> {
    const db = this.getDb();
    const row = this.serializeNode(node);

    db.prepare(`
      INSERT INTO nodes (id, type, source, name, path, hash, data, created_at, updated_at)
      VALUES (@id, @type, @source, @name, @path, @hash, @data, @created_at, @updated_at)
      ON CONFLICT(id) DO UPDATE SET
        type       = excluded.type,
        source     = excluded.source,
        name       = excluded.name,
        path       = excluded.path,
        hash       = excluded.hash,
        data       = excluded.data,
        updated_at = excluded.updated_at
    `).run(row);
    this.markGraphDirty();

    // Maintain trigram index for symbol nodes
    if (node.type === "symbol") {
      const sym = node as SymbolNode;
      // Remove old trigrams
      db.prepare("DELETE FROM trigrams WHERE node_id = ?").run(node.id);
      // Generate trigrams from name and signature
      const text = `${sym.name} ${sym.signature ?? ""}`;
      const trigrams = generateTrigrams(text);
      const insertTri = db.prepare(
        "INSERT INTO trigrams (trigram, node_id) VALUES (?, ?)"
      );
      for (const tri of trigrams) {
        insertTri.run(tri, node.id);
      }
    }

    return node.id;
  }

  async getNode(id: string): Promise<GraphNode | null> {
    const db = this.getDb();
    const row = db
      .prepare("SELECT * FROM nodes WHERE id = ?")
      .get(id) as ReturnType<typeof this.deserializeNode> extends infer R
      ? Parameters<typeof this.deserializeNode>[0] | undefined
      : never;

    if (!row) return null;
    return this.deserializeNode(row);
  }

  async queryNodes(filter: NodeFilter): Promise<GraphNode[]> {
    const db = this.getDb();
    const conditions: string[] = [];
    const params: Record<string, unknown> = {};

    if (filter.type) {
      conditions.push("n.type = @type");
      params.type = filter.type;
    }
    if (filter.name) {
      conditions.push("n.name = @name");
      params.name = filter.name;
    }
    if (filter.leafName) {
      conditions.push("(n.name = @leafName OR substr(n.name, -length(@leafName) - 1) = '.' || @leafName)");
      params.leafName = filter.leafName;
    }
    if (filter.pathPrefix !== undefined) {
      const scoped = searchFilter(undefined, filter.pathPrefix);
      conditions.push(scoped.sql.slice(5));
      Object.assign(params, scoped.params);
    }
    if (filter.path) {
      conditions.push("n.path = @path");
      params.path = filter.path;
    }
    if (filter.kind) {
      conditions.push("json_extract(n.data, '$.kind') = @kind");
      params.kind = filter.kind;
    }
    if (filter.stale !== undefined) {
      conditions.push("json_extract(n.data, '$.stale') = @stale");
      params.stale = filter.stale ? 1 : 0;
    }

    const where =
      conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
    const sql = `SELECT * FROM nodes n ${where}`;

    const rows = db.prepare(sql).all(params) as Parameters<
      typeof this.deserializeNode
    >[0][];
    return rows.map((r) => this.deserializeNode(r));
  }

  async queryFilePaths(directory?: string): Promise<string[]> {
    const db = this.getDb();
    // The trailing slash makes this a directory-boundary query. SQLite can
    // satisfy both forms from the covering (type, path) index, without loading
    // the full node JSON for every file in the repository.
    const rows = directory === undefined
      ? db.prepare("SELECT path FROM nodes WHERE type = 'file' ORDER BY path").all()
      : db.prepare(`
          SELECT path FROM nodes
          WHERE type = 'file' AND path >= ? AND path < ?
          ORDER BY path
        `).all(`${directory}/`, `${directory}0`);
    return (rows as Array<{ path: string }>).map((row) => row.path);
  }

  async queryNodesByFilePaths(filePaths: string[]): Promise<GraphNode[]> {
    if (filePaths.length === 0) return [];

    const db = this.getDb();
    const placeholders = filePaths.map((_, i) => `@p${i}`).join(", ");
    const params: Record<string, string> = {};
    filePaths.forEach((p, i) => {
      params[`p${i}`] = p;
    });

    const sql = `
      SELECT * FROM nodes
      WHERE type = 'symbol'
        AND json_extract(data, '$.filePath') IN (${placeholders})
    `;

    const rows = db.prepare(sql).all(params) as Parameters<
      typeof this.deserializeNode
    >[0][];
    return rows.map((r) => this.deserializeNode(r));
  }

  async deleteNode(id: string): Promise<void> {
    const db = this.getDb();
    // Remove trigrams for this node
    db.prepare("DELETE FROM trigrams WHERE node_id = ?").run(id);
    // Foreign key cascade will delete associated edges
    db.prepare("DELETE FROM nodes WHERE id = ?").run(id);
    this.markGraphDirty();
  }

  // ── Edge CRUD ──────────────────────────────────────────────────────

  async upsertEdge(edge: GraphEdge): Promise<string> {
    const db = this.getDb();

    db.prepare(`
      INSERT INTO edges (id, type, source, from_id, to_id, data, created_at)
      VALUES (@id, @type, @source, @from_id, @to_id, @data, @created_at)
      ON CONFLICT(id) DO UPDATE SET
        type       = excluded.type,
        source     = excluded.source,
        from_id    = excluded.from_id,
        to_id      = excluded.to_id,
        data       = excluded.data,
        created_at = excluded.created_at
    `).run({
      id: edge.id,
      type: edge.type,
      source: edge.source,
      from_id: edge.fromId,
      to_id: edge.toId,
      data: edge.data ? JSON.stringify(edge.data) : null,
      created_at: edge.createdAt,
    });
    this.markGraphDirty();

    return edge.id;
  }

  async getEdges(
    nodeId: string,
    direction: "in" | "out" | "both",
    edgeTypes?: string[]
  ): Promise<GraphEdge[]> {
    const db = this.getDb();
    const conditions: string[] = [];
    const params: Record<string, unknown> = {};

    if (direction === "out") {
      conditions.push("e.from_id = @nodeId");
    } else if (direction === "in") {
      conditions.push("e.to_id = @nodeId");
    } else {
      conditions.push("(e.from_id = @nodeId OR e.to_id = @nodeId)");
    }
    params.nodeId = nodeId;

    if (edgeTypes && edgeTypes.length > 0) {
      const placeholders = edgeTypes
        .map((_, i) => `@edgeType${i}`)
        .join(", ");
      conditions.push(`e.type IN (${placeholders})`);
      edgeTypes.forEach((t, i) => {
        params[`edgeType${i}`] = t;
      });
    }

    const where = conditions.join(" AND ");
    const sql = `SELECT * FROM edges e WHERE ${where}`;
    const rows = db.prepare(sql).all(params) as Array<{
      id: string;
      type: string;
      source: string;
      from_id: string;
      to_id: string;
      data: string | null;
      created_at: string;
    }>;

    return rows.map((r) => this.deserializeEdge(r));
  }

  async deleteEdgesByNode(nodeId: string): Promise<number> {
    const db = this.getDb();
    const result = db
      .prepare("DELETE FROM edges WHERE from_id = ? OR to_id = ?")
      .run(nodeId, nodeId);
    if (result.changes) this.markGraphDirty();
    return result.changes;
  }

  private deserializeEdge(row: {
    id: string;
    type: string;
    source: string;
    from_id: string;
    to_id: string;
    data: string | null;
    created_at: string;
  }): GraphEdge {
    return {
      id: row.id,
      type: row.type as GraphEdge["type"],
      source: row.source as GraphEdge["source"],
      fromId: row.from_id,
      toId: row.to_id,
      data: row.data ? JSON.parse(row.data) : undefined,
      createdAt: row.created_at,
    };
  }

  // ── Traversal ──────────────────────────────────────────────────────

  async traverse(
    startId: string,
    depth: number,
    edgeTypes?: string[]
  ): Promise<TraversalResult[]> {
    const results: TraversalResult[] = [];
    const visited = new Set<string>([startId]);

    // BFS queue: [nodeId, currentDepth, edgePath]
    const queue: Array<[string, number, GraphEdge[]]> = [[startId, 0, []]];

    while (queue.length > 0) {
      const [currentId, currentDepth, currentPath] = queue.shift()!;

      if (currentDepth >= depth) continue;

      const outEdges = await this.getEdges(currentId, "out", edgeTypes);

      for (const edge of outEdges) {
        const nextId = edge.toId;
        if (visited.has(nextId)) continue;
        visited.add(nextId);

        const node = await this.getNode(nextId);
        if (!node) continue;

        const edgePath = [...currentPath, edge];
        results.push({
          node,
          depth: currentDepth + 1,
          path: edgePath,
        });

        queue.push([nextId, currentDepth + 1, edgePath]);
      }
    }

    return results;
  }

  // ── FTS5 Search ────────────────────────────────────────────────────

  async recordDelivery(entry: {
    sessionId: string;
    contextEpoch: string;
    nodeId: string;
    mode: string;
    sourceHash: string;
  }): Promise<void> {
    this.getDb().prepare(`INSERT OR IGNORE INTO session_deliveries
      (session_id, context_epoch, node_id, mode, source_hash, delivered_at)
      VALUES (?, ?, ?, ?, ?, ?)`)
      .run(entry.sessionId, entry.contextEpoch, entry.nodeId, entry.mode, entry.sourceHash, new Date().toISOString());
  }

  async incrementMetric(key: "source_bytes_delivered" | "source_bytes_avoided" | "conditional_hits", amount: number): Promise<void> {
    this.getDb().prepare(`INSERT INTO context_metrics(key, value) VALUES (?, ?)
      ON CONFLICT(key) DO UPDATE SET value = value + excluded.value`).run(key, amount);
  }

  async search(query: string, scope?: string, options: SearchOptions = {}): Promise<SearchResult[]> {
    const db = this.getDb();
    const normalizedQuery = query.trim().toLowerCase().replace(/\s+/g, " ");
    const cacheKey = options.path === undefined && (scope === undefined || scope === "all")
      ? `${scope ?? "all"}:${normalizedQuery}`
      : JSON.stringify(["scoped-v2", scope ?? "all", normalizedQuery, options.path ?? null]);
    const scoped = searchFilter(scope, options.path);
    const version = (db.prepare("SELECT value FROM cache_meta WHERE key = 'graph_version'").get() as { value: number }).value;
    const cached = db.prepare("SELECT results FROM retrieval_cache WHERE key = ? AND graph_version = ?")
      .get(cacheKey, version) as { results: string } | undefined;
    if (cached) {
      db.prepare("UPDATE retrieval_cache SET hit_count = hit_count + 1, last_used_at = ? WHERE key = ?")
        .run(new Date().toISOString(), cacheKey);
      const rows = JSON.parse(cached.results) as Array<{ id: string; rank: number; matchedField: string }>;
      const hits: SearchResult[] = [];
      for (const row of rows) {
        const node = await this.getNode(row.id);
        if (node) hits.push({ node, rank: row.rank, matchedField: row.matchedField });
      }
      return hits;
    }

    // Escape FTS5 special characters and build a simple query
    const ftsQuery = query
      .replace(/['"]/g, "")
      .split(/\s+/)
      .filter((t) => t.length > 0)
      .map((t) => `"${t}"`)
      .join(" OR ");

    if (!ftsQuery) return [];

    const sql = `
      SELECT
        f.node_id,
        f.name       AS fts_name,
        f.signature  AS fts_signature,
        f.summary    AS fts_summary,
        f.description AS fts_description,
        bm25(nodes_fts) AS rank
      FROM nodes_fts f JOIN nodes n ON n.id = f.node_id
      WHERE nodes_fts MATCH @query ${scoped.sql}
      ORDER BY bm25(nodes_fts), f.node_id
      LIMIT 200
    `;

    const rows = db.prepare(sql).all({ query: ftsQuery, ...scoped.params }) as Array<{
      node_id: string;
      fts_name: string;
      fts_signature: string;
      fts_summary: string;
      fts_description: string;
      rank: number;
    }>;

    const results: SearchResult[] = [];
    for (const row of rows) {
      const node = await this.getNode(row.node_id);
      if (!node) continue;

      // Determine which field matched by checking each FTS column
      const queryTerms = query.toLowerCase().split(/\s+/);
      let matchedField = "name";
      if (
        queryTerms.some((t) => row.fts_summary?.toLowerCase().includes(t))
      ) {
        matchedField = "summary";
      } else if (
        queryTerms.some((t) => row.fts_signature?.toLowerCase().includes(t))
      ) {
        matchedField = "signature";
      } else if (
        queryTerms.some(
          (t) => row.fts_description?.toLowerCase().includes(t)
        )
      ) {
        matchedField = "description";
      }

      results.push({
        node,
        rank: -row.rank, // BM25 rank is negative (lower = better), invert so higher = more relevant
        matchedField,
      });
    }

    // Sort by rank descending (highest = most relevant)
    results.sort((a, b) => b.rank - a.rank);

    // If FTS5 returned fewer than 5 results, augment with trigram search
    if (results.length < 5) {
      const trigramResults = await this.searchTrigrams(query, scope, options);
      const existingIds = new Set(results.map((r) => r.node.id));
      for (const tr of trigramResults) {
        if (!existingIds.has(tr.node.id)) {
          results.push(tr);
          existingIds.add(tr.node.id);
        }
      }
      // Re-sort merged results
      results.sort((a, b) => b.rank - a.rank);
    }

    db.prepare(`INSERT INTO retrieval_cache(key, graph_version, results, last_used_at)
      VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET graph_version=excluded.graph_version,
      results=excluded.results, last_used_at=excluded.last_used_at, hit_count=0`)
      .run(cacheKey, version, JSON.stringify(results.map((r) => ({ id: r.node.id, rank: r.rank, matchedField: r.matchedField }))), new Date().toISOString());
    db.prepare("DELETE FROM retrieval_cache WHERE key IN (SELECT key FROM retrieval_cache ORDER BY last_used_at DESC LIMIT -1 OFFSET 500)").run();
    return results;
  }

  /**
   * Search using trigram index for partial/substring symbol matching.
   * Finds nodes whose name or signature contains the query as a substring.
   */
  async searchTrigrams(query: string, scope?: string, options: SearchOptions = {}): Promise<SearchResult[]> {
    const db = this.getDb();

    const queryTrigrams = generateTrigrams(query);
    if (queryTrigrams.length === 0) return [];

    // Build a query that finds nodes matching the most trigrams
    const placeholders = queryTrigrams.map((_, i) => `@tri${i}`).join(", ");
    const params: Record<string, unknown> = {};
    queryTrigrams.forEach((tri, i) => {
      params[`tri${i}`] = tri;
    });
    params.triCount = queryTrigrams.length;
    const scoped = searchFilter(scope, options.path);
    Object.assign(params, scoped.params);

    const sql = `
      SELECT
        t.node_id,
        COUNT(*) AS match_count,
        CAST(COUNT(*) AS REAL) / @triCount AS match_ratio
      FROM trigrams t JOIN nodes n ON n.id = t.node_id
      WHERE trigram IN (${placeholders}) ${scoped.sql}
      GROUP BY t.node_id
      HAVING match_ratio >= 0.6
      ORDER BY match_count DESC, t.node_id
      LIMIT 20
    `;

    const rows = db.prepare(sql).all(params) as Array<{
      node_id: string;
      match_count: number;
      match_ratio: number;
    }>;

    const results: SearchResult[] = [];
    for (const row of rows) {
      const node = await this.getNode(row.node_id);
      if (!node) continue;

      results.push({
        node,
        rank: row.match_ratio,
        matchedField: "trigram",
      });
    }

    return results;
  }

  // ── Transactions ───────────────────────────────────────────────────

  async beginTransaction(): Promise<void> {
    this.getDb().exec("BEGIN TRANSACTION");
    this.inTransaction = true;
    this.graphDirty = false;
  }

  async commitTransaction(): Promise<void> {
    if (this.graphDirty) {
      this.getDb().prepare("UPDATE cache_meta SET value = value + 1 WHERE key = 'graph_version'").run();
    }
    this.getDb().exec("COMMIT");
    this.inTransaction = false;
    this.graphDirty = false;
  }

  async rollbackTransaction(): Promise<void> {
    this.getDb().exec("ROLLBACK");
    this.inTransaction = false;
    this.graphDirty = false;
  }

  // ── Maintenance ────────────────────────────────────────────────────

  async getStaleFiles(
    currentHashes: Map<string, string>
  ): Promise<{ changed: string[]; deleted: string[]; added: string[] }> {
    const db = this.getDb();

    const storedFiles = db
      .prepare("SELECT path, hash FROM nodes WHERE type = 'file'")
      .all() as Array<{ path: string; hash: string }>;

    const storedPaths = new Set<string>();
    const changed: string[] = [];
    const deleted: string[] = [];
    const added: string[] = [];

    for (const row of storedFiles) {
      storedPaths.add(row.path);
      const currentHash = currentHashes.get(row.path);
      if (currentHash === undefined) {
        deleted.push(row.path);
      } else if (currentHash !== row.hash) {
        changed.push(row.path);
      }
    }

    for (const filePath of currentHashes.keys()) {
      if (!storedPaths.has(filePath)) {
        added.push(filePath);
      }
    }

    return { changed, deleted, added };
  }

  async markConceptsStale(filePaths: string[]): Promise<number> {
    if (filePaths.length === 0) return 0;

    const db = this.getDb();

    // Find concepts describing either a file or one of its symbols. Resolve
    // these before purge, since foreign-key cascades remove describes edges.
    const conceptIds = new Set<string>();
    // Keep bound parameters below SQLite builds' variable limits on large
    // indexing runs. A concept may describe more than one affected target.
    for (let start = 0; start < filePaths.length; start += 500) {
      const paths = filePaths.slice(start, start + 500);
      const placeholders = paths.map((_, i) => `@fp${i}`).join(", ");
      const params: Record<string, string> = {};
      paths.forEach((fp, i) => {
        params[`fp${i}`] = fp;
      });

      const sql = `
        SELECT DISTINCT e.from_id AS concept_id
        FROM edges e
        JOIN nodes n ON e.to_id = n.id
        WHERE e.type = 'describes'
          AND (
            (n.type = 'symbol' AND json_extract(n.data, '$.filePath') IN (${placeholders}))
            OR (n.type = 'file' AND n.path IN (${placeholders}))
          )
      `;
      const rows = db.prepare(sql).all(params) as Array<{ concept_id: string }>;
      for (const row of rows) conceptIds.add(row.concept_id);
    }

    let count = 0;
    const updateStmt = db.prepare(`
      UPDATE nodes
      SET data = json_set(data, '$.stale', json('true')),
          updated_at = @updatedAt
      WHERE id = @id AND type = 'concept'
    `);

    for (const id of conceptIds) {
      const result = updateStmt.run({
        id,
        updatedAt: new Date().toISOString(),
      });
      count += result.changes;
    }

    if (count) this.markGraphDirty();

    return count;
  }

  async purgeFileNodes(filePaths: string[]): Promise<number> {
    if (filePaths.length === 0) return 0;

    const db = this.getDb();
    let count = 0;

    const purge = db.transaction(() => {
      for (const filePath of filePaths) {
        // Find and delete symbol nodes belonging to this file
        const symbols = db
          .prepare(
            "SELECT id FROM nodes WHERE type = 'symbol' AND json_extract(data, '$.filePath') = ?"
          )
          .all(filePath) as Array<{ id: string }>;

        for (const sym of symbols) {
          db.prepare("DELETE FROM nodes WHERE id = ?").run(sym.id);
          count++;
        }

        // Delete the file node itself
        const result = db
          .prepare("DELETE FROM nodes WHERE type = 'file' AND path = ?")
          .run(filePath);
        count += result.changes;
      }
    });

    purge();
    if (count) this.markGraphDirty();
    return count;
  }

  async getStats(): Promise<{
    nodeCount: Record<string, number>;
    edgeCount: Record<string, number>;
    staleCount: number;
    lastIndexedAt: string | null;
    metrics: Record<string, number>;
  }> {
    const db = this.getDb();

    // Node counts by type
    const nodeRows = db
      .prepare("SELECT type, COUNT(*) as cnt FROM nodes GROUP BY type")
      .all() as Array<{ type: string; cnt: number }>;
    const nodeCount: Record<string, number> = {};
    for (const row of nodeRows) {
      nodeCount[row.type] = row.cnt;
    }

    // Edge counts by type
    const edgeRows = db
      .prepare("SELECT type, COUNT(*) as cnt FROM edges GROUP BY type")
      .all() as Array<{ type: string; cnt: number }>;
    const edgeCount: Record<string, number> = {};
    for (const row of edgeRows) {
      edgeCount[row.type] = row.cnt;
    }

    // Stale count (concepts + workflows with stale=true)
    const staleRow = db
      .prepare(
        "SELECT COUNT(*) as cnt FROM nodes WHERE json_extract(data, '$.stale') = json('true')"
      )
      .get() as { cnt: number };

    // Last indexed file
    const lastIndexed = db
      .prepare(
        "SELECT json_extract(data, '$.lastIndexedAt') as ts FROM nodes WHERE type = 'file' ORDER BY json_extract(data, '$.lastIndexedAt') DESC LIMIT 1"
      )
      .get() as { ts: string | null } | undefined;

    const metrics: Record<string, number> = {};
    for (const row of db.prepare("SELECT key, value FROM context_metrics").all() as Array<{ key: string; value: number }>) {
      metrics[row.key] = row.value;
    }
    const cacheRow = db.prepare("SELECT COUNT(*) AS entries, COALESCE(SUM(hit_count), 0) AS hits FROM retrieval_cache")
      .get() as { entries: number; hits: number };
    metrics.retrieval_cache_entries = cacheRow.entries;
    metrics.retrieval_cache_hits = cacheRow.hits;

    return {
      nodeCount,
      edgeCount,
      staleCount: staleRow.cnt,
      lastIndexedAt: lastIndexed?.ts ?? null,
      metrics,
    };
  }
}
