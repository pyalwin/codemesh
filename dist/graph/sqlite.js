import Database from "better-sqlite3";
/**
 * Generate unique trigrams (3-character substrings) from text.
 * Used to power partial/substring symbol matching.
 */
function generateTrigrams(text) {
    const normalized = text.toLowerCase();
    const trigrams = [];
    for (let i = 0; i <= normalized.length - 3; i++) {
        trigrams.push(normalized.slice(i, i + 3));
    }
    return [...new Set(trigrams)]; // dedupe
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
export class SqliteBackend {
    db = null;
    dbPath;
    constructor(dbPath) {
        this.dbPath = dbPath;
    }
    // ── Lifecycle ──────────────────────────────────────────────────────
    async initialize() {
        this.db = new Database(this.dbPath);
        this.db.pragma("journal_mode = WAL");
        this.db.pragma("foreign_keys = ON");
        this.createSchema();
    }
    async close() {
        if (this.db) {
            this.db.close();
            this.db = null;
        }
    }
    getDb() {
        if (!this.db) {
            throw new Error("Database not initialized. Call initialize() first.");
        }
        return this.db;
    }
    // ── Schema ─────────────────────────────────────────────────────────
    createSchema() {
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
    serializeNode(node) {
        const rest = {};
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
            path: node.path ?? null,
            hash: node.hash ?? null,
            data: JSON.stringify(rest),
            created_at: node.createdAt,
            updated_at: node.updatedAt,
        };
    }
    /**
     * Reconstruct a typed GraphNode from a database row.
     */
    deserializeNode(row) {
        const data = row.data ? JSON.parse(row.data) : {};
        const base = {
            id: row.id,
            type: row.type,
            source: row.source,
            name: row.name,
            createdAt: row.created_at,
            updatedAt: row.updated_at,
        };
        switch (row.type) {
            case "file": {
                const fileNode = {
                    ...base,
                    type: "file",
                    source: "static",
                    path: row.path,
                    hash: row.hash,
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
                const symbolNode = {
                    ...base,
                    type: "symbol",
                    source: "static",
                    kind: data.kind,
                    filePath: data.filePath ?? row.path ?? "",
                    lineStart: data.lineStart ?? 0,
                    lineEnd: data.lineEnd ?? 0,
                    signature: data.signature ?? "",
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
                };
            case "workflow":
                return {
                    ...base,
                    type: "workflow",
                    source: "agent",
                    description: data.description ?? "",
                    fileSequence: data.fileSequence ?? [],
                    lastWalkedAt: data.lastWalkedAt ?? base.createdAt,
                    stale: data.stale ?? false,
                };
            default:
                throw new Error(`Unknown node type: ${row.type}`);
        }
    }
    // ── Node CRUD ──────────────────────────────────────────────────────
    async upsertNode(node) {
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
        // Maintain trigram index for symbol nodes
        if (node.type === "symbol") {
            const sym = node;
            // Remove old trigrams
            db.prepare("DELETE FROM trigrams WHERE node_id = ?").run(node.id);
            // Generate trigrams from name and signature
            const text = `${sym.name} ${sym.signature ?? ""}`;
            const trigrams = generateTrigrams(text);
            const insertTri = db.prepare("INSERT INTO trigrams (trigram, node_id) VALUES (?, ?)");
            for (const tri of trigrams) {
                insertTri.run(tri, node.id);
            }
        }
        return node.id;
    }
    async getNode(id) {
        const db = this.getDb();
        const row = db
            .prepare("SELECT * FROM nodes WHERE id = ?")
            .get(id);
        if (!row)
            return null;
        return this.deserializeNode(row);
    }
    async queryNodes(filter) {
        const db = this.getDb();
        const conditions = [];
        const params = {};
        if (filter.type) {
            conditions.push("n.type = @type");
            params.type = filter.type;
        }
        if (filter.name) {
            conditions.push("n.name = @name");
            params.name = filter.name;
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
        const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
        const sql = `SELECT * FROM nodes n ${where}`;
        const rows = db.prepare(sql).all(params);
        return rows.map((r) => this.deserializeNode(r));
    }
    async queryNodesByFilePaths(filePaths) {
        if (filePaths.length === 0)
            return [];
        const db = this.getDb();
        const placeholders = filePaths.map((_, i) => `@p${i}`).join(", ");
        const params = {};
        filePaths.forEach((p, i) => {
            params[`p${i}`] = p;
        });
        const sql = `
      SELECT * FROM nodes
      WHERE type = 'symbol'
        AND json_extract(data, '$.filePath') IN (${placeholders})
    `;
        const rows = db.prepare(sql).all(params);
        return rows.map((r) => this.deserializeNode(r));
    }
    async deleteNode(id) {
        const db = this.getDb();
        // Remove trigrams for this node
        db.prepare("DELETE FROM trigrams WHERE node_id = ?").run(id);
        // Foreign key cascade will delete associated edges
        db.prepare("DELETE FROM nodes WHERE id = ?").run(id);
    }
    // ── Edge CRUD ──────────────────────────────────────────────────────
    async upsertEdge(edge) {
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
        return edge.id;
    }
    async getEdges(nodeId, direction, edgeTypes) {
        const db = this.getDb();
        const conditions = [];
        const params = {};
        if (direction === "out") {
            conditions.push("e.from_id = @nodeId");
        }
        else if (direction === "in") {
            conditions.push("e.to_id = @nodeId");
        }
        else {
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
        const rows = db.prepare(sql).all(params);
        return rows.map((r) => this.deserializeEdge(r));
    }
    async deleteEdgesByNode(nodeId) {
        const db = this.getDb();
        const result = db
            .prepare("DELETE FROM edges WHERE from_id = ? OR to_id = ?")
            .run(nodeId, nodeId);
        return result.changes;
    }
    deserializeEdge(row) {
        return {
            id: row.id,
            type: row.type,
            source: row.source,
            fromId: row.from_id,
            toId: row.to_id,
            data: row.data ? JSON.parse(row.data) : undefined,
            createdAt: row.created_at,
        };
    }
    // ── Traversal ──────────────────────────────────────────────────────
    async traverse(startId, depth, edgeTypes) {
        const results = [];
        const visited = new Set([startId]);
        // BFS queue: [nodeId, currentDepth, edgePath]
        const queue = [[startId, 0, []]];
        while (queue.length > 0) {
            const [currentId, currentDepth, currentPath] = queue.shift();
            if (currentDepth >= depth)
                continue;
            const outEdges = await this.getEdges(currentId, "out", edgeTypes);
            for (const edge of outEdges) {
                const nextId = edge.toId;
                if (visited.has(nextId))
                    continue;
                visited.add(nextId);
                const node = await this.getNode(nextId);
                if (!node)
                    continue;
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
    async search(query, _scope) {
        const db = this.getDb();
        // Escape FTS5 special characters and build a simple query
        const ftsQuery = query
            .replace(/['"]/g, "")
            .split(/\s+/)
            .filter((t) => t.length > 0)
            .map((t) => `"${t}"`)
            .join(" OR ");
        if (!ftsQuery)
            return [];
        const sql = `
      SELECT
        f.node_id,
        f.name       AS fts_name,
        f.signature  AS fts_signature,
        f.summary    AS fts_summary,
        f.description AS fts_description,
        bm25(nodes_fts) AS rank
      FROM nodes_fts f
      WHERE nodes_fts MATCH @query
      ORDER BY bm25(nodes_fts)
    `;
        const rows = db.prepare(sql).all({ query: ftsQuery });
        const results = [];
        for (const row of rows) {
            const node = await this.getNode(row.node_id);
            if (!node)
                continue;
            // Determine which field matched by checking each FTS column
            const queryTerms = query.toLowerCase().split(/\s+/);
            let matchedField = "name";
            if (queryTerms.some((t) => row.fts_summary?.toLowerCase().includes(t))) {
                matchedField = "summary";
            }
            else if (queryTerms.some((t) => row.fts_signature?.toLowerCase().includes(t))) {
                matchedField = "signature";
            }
            else if (queryTerms.some((t) => row.fts_description?.toLowerCase().includes(t))) {
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
            const trigramResults = await this.searchTrigrams(query);
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
        return results;
    }
    /**
     * Search using trigram index for partial/substring symbol matching.
     * Finds nodes whose name or signature contains the query as a substring.
     */
    async searchTrigrams(query) {
        const db = this.getDb();
        const queryTrigrams = generateTrigrams(query);
        if (queryTrigrams.length === 0)
            return [];
        // Build a query that finds nodes matching the most trigrams
        const placeholders = queryTrigrams.map((_, i) => `@tri${i}`).join(", ");
        const params = {};
        queryTrigrams.forEach((tri, i) => {
            params[`tri${i}`] = tri;
        });
        params.triCount = queryTrigrams.length;
        const sql = `
      SELECT
        node_id,
        COUNT(*) AS match_count,
        CAST(COUNT(*) AS REAL) / @triCount AS match_ratio
      FROM trigrams
      WHERE trigram IN (${placeholders})
      GROUP BY node_id
      HAVING match_ratio >= 0.6
      ORDER BY match_count DESC
      LIMIT 20
    `;
        const rows = db.prepare(sql).all(params);
        const results = [];
        for (const row of rows) {
            const node = await this.getNode(row.node_id);
            if (!node)
                continue;
            results.push({
                node,
                rank: row.match_ratio,
                matchedField: "trigram",
            });
        }
        return results;
    }
    // ── Transactions ───────────────────────────────────────────────────
    async beginTransaction() {
        this.getDb().exec("BEGIN TRANSACTION");
    }
    async commitTransaction() {
        this.getDb().exec("COMMIT");
    }
    async rollbackTransaction() {
        this.getDb().exec("ROLLBACK");
    }
    // ── Maintenance ────────────────────────────────────────────────────
    async getStaleFiles(currentHashes) {
        const db = this.getDb();
        const storedFiles = db
            .prepare("SELECT path, hash FROM nodes WHERE type = 'file'")
            .all();
        const storedPaths = new Set();
        const changed = [];
        const deleted = [];
        const added = [];
        for (const row of storedFiles) {
            storedPaths.add(row.path);
            const currentHash = currentHashes.get(row.path);
            if (currentHash === undefined) {
                deleted.push(row.path);
            }
            else if (currentHash !== row.hash) {
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
    async markConceptsStale(filePaths) {
        if (filePaths.length === 0)
            return 0;
        const db = this.getDb();
        // Find concept nodes that describe symbols in the given file paths.
        // A concept is related to a file if it has a "describes" edge to a symbol
        // whose filePath is in the given list.
        const placeholders = filePaths.map((_, i) => `@fp${i}`).join(", ");
        const params = {};
        filePaths.forEach((fp, i) => {
            params[`fp${i}`] = fp;
        });
        // Find concept IDs that describe symbols in these files
        const sql = `
      SELECT DISTINCT e.from_id AS concept_id
      FROM edges e
      JOIN nodes n ON e.to_id = n.id
      WHERE e.type = 'describes'
        AND n.type = 'symbol'
        AND json_extract(n.data, '$.filePath') IN (${placeholders})
    `;
        const conceptRows = db.prepare(sql).all(params);
        let count = 0;
        const updateStmt = db.prepare(`
      UPDATE nodes
      SET data = json_set(data, '$.stale', json('true')),
          updated_at = @updatedAt
      WHERE id = @id AND type = 'concept'
    `);
        for (const row of conceptRows) {
            const result = updateStmt.run({
                id: row.concept_id,
                updatedAt: new Date().toISOString(),
            });
            count += result.changes;
        }
        return count;
    }
    async purgeFileNodes(filePaths) {
        if (filePaths.length === 0)
            return 0;
        const db = this.getDb();
        let count = 0;
        const purge = db.transaction(() => {
            for (const filePath of filePaths) {
                // Find and delete symbol nodes belonging to this file
                const symbols = db
                    .prepare("SELECT id FROM nodes WHERE type = 'symbol' AND json_extract(data, '$.filePath') = ?")
                    .all(filePath);
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
        return count;
    }
    async getStats() {
        const db = this.getDb();
        // Node counts by type
        const nodeRows = db
            .prepare("SELECT type, COUNT(*) as cnt FROM nodes GROUP BY type")
            .all();
        const nodeCount = {};
        for (const row of nodeRows) {
            nodeCount[row.type] = row.cnt;
        }
        // Edge counts by type
        const edgeRows = db
            .prepare("SELECT type, COUNT(*) as cnt FROM edges GROUP BY type")
            .all();
        const edgeCount = {};
        for (const row of edgeRows) {
            edgeCount[row.type] = row.cnt;
        }
        // Stale count (concepts + workflows with stale=true)
        const staleRow = db
            .prepare("SELECT COUNT(*) as cnt FROM nodes WHERE json_extract(data, '$.stale') = json('true')")
            .get();
        // Last indexed file
        const lastIndexed = db
            .prepare("SELECT json_extract(data, '$.lastIndexedAt') as ts FROM nodes WHERE type = 'file' ORDER BY json_extract(data, '$.lastIndexedAt') DESC LIMIT 1")
            .get();
        return {
            nodeCount,
            edgeCount,
            staleCount: staleRow.cnt,
            lastIndexedAt: lastIndexed?.ts ?? null,
        };
    }
}
//# sourceMappingURL=sqlite.js.map