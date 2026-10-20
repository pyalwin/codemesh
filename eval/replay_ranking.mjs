#!/usr/bin/env node
// Replay saved search queries against copies of their pinned indexes. No model calls.
import { build } from "esbuild";
import { constants, copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { values } = parseArgs({ options: {
  input: { type: "string", default: "eval/results/vnext-agent-full/report.json" },
  output: { type: "string", default: "eval/results/vnext-ranking-replay.json" },
  markdown: { type: "string", default: "eval/VNEXT_RANKING_REPLAY.md" },
} });
const inputPath = resolve(root, values.input);
const original = JSON.parse(readFileSync(inputPath, "utf8"));
const targets = {
  kebab_alias: { symbol: "symbol:pydantic/alias_generators.py:to_snake", file: "pydantic/alias_generators.py" },
  negative_bytesize: { symbol: "symbol:pydantic/types.py:ByteSize.human_readable", file: "pydantic/types.py" },
  factory_validated_data: { symbol: "symbol:pydantic/fields.py:FieldInfo.get_default", file: "pydantic/fields.py" },
};
const outputPath = resolve(root, values.output);
mkdirSync(dirname(outputPath), { recursive: true });
// Keeping temporary modules below the repository lets external dependencies resolve.
const temp = mkdtempSync(join(root, "eval/results/ranking-replay-"));
const stores = new Map();

async function previousSearch(storage, query) {
  const exact = new Map();
  const add = (node) => { if (node) exact.set(node.id, { node, rank: Infinity, matchedField: "exact" }); };
  if (query.startsWith("file:") || query.startsWith("symbol:")) add(await storage.getNode(query));
  if (query.includes("/") || query.includes(".")) add(await storage.getNode(`file:${query}`));
  for (const node of await storage.queryNodes({ type: "symbol", name: query })) add(node);
  return [...exact.values(), ...(await storage.search(query, "all")).filter((hit) => !exact.has(hit.node.id))]
    .sort((a, b) => {
      if (a.matchedField === "exact" && b.matchedField !== "exact") return -1;
      if (b.matchedField === "exact" && a.matchedField !== "exact") return 1;
      return ((b.node.pagerankScore ?? 0) - (a.node.pagerankScore ?? 0))
        || (a.rank !== b.rank ? b.rank - a.rank : 0)
        || a.node.id.localeCompare(b.node.id);
    }).slice(0, 20).map((hit) => hit.node);
}

function position(hits, predicate) {
  const index = hits.findIndex(predicate);
  return index === -1 ? null : index + 1;
}

try {
  await build({
    entryPoints: { query: join(root, "src/tools/query.ts"), sqlite: join(root, "src/graph/sqlite.ts") },
    outdir: temp, outExtension: { ".js": ".mjs" }, bundle: true,
    platform: "node", format: "esm", packages: "external", logLevel: "silent",
  });
  const { handleQuery } = await import(pathToFileURL(join(temp, "query.mjs")).href);
  const { SqliteBackend } = await import(pathToFileURL(join(temp, "sqlite.mjs")).href);
  const seen = new Set();
  const rows = [];
  for (const run of original.results) {
    const target = targets[run.task];
    if (!target) continue;
    for (const phase of run.phases ?? []) for (const action of phase.actions ?? []) {
      if (action.action !== "search") continue;
      const query = action.input.query.trim();
      const key = `${run.task}:${query}`;
      if (seen.has(key)) continue;
      seen.add(key);
      if (!stores.has(run.task)) {
        const path = join(temp, `${run.task}.db`);
        copyFileSync(join(dirname(inputPath), "indexes", `${run.task}.db`), path, constants.COPYFILE_FICLONE);
        const storage = new SqliteBackend(path);
        await storage.initialize();
        stores.set(run.task, storage);
      }
      const storage = stores.get(run.task);
      const before = await previousSearch(storage, query);
      const after = (await handleQuery(storage, { query, limit: 20 })).results;
      const recorded = JSON.parse(action.observation).results;
      const symbol = (hit) => hit.id === target.symbol || hit.id.startsWith(`${target.symbol}@L`);
      const file = (hit) => hit.filePath === target.file || hit.path === target.file || hit.id === `file:${target.file}`;
      rows.push({
        task: run.task, query, target,
        recordedSymbolRank: position(recorded, symbol),
        beforeSymbolRank: position(before, symbol), afterSymbolRank: position(after, symbol),
        beforeFileRank: position(before, file), afterFileRank: position(after, file),
        beforeTop3: before.slice(0, 3).map((hit) => hit.id),
        afterTop3: after.slice(0, 3).map((hit) => hit.id),
      });
    }
  }
  const countAt = (key, limit) => rows.filter((row) => row[key] !== null && row[key] <= limit).length;
  const report = {
    input: values.input,
    method: "Unique saved task/query pairs; old and new ranking on the same copied task indexes; 20-result diagnostic pages.",
    caveats: [
      "Offline navigation diagnostic; no model calls, repair acceptance, token measurements, or cost claims.",
      "Target symbols come from the three repair tasks; overload IDs with @L are treated as the same symbol family.",
      "A broad investigative query can usefully find a test or parent class before the eventual edit symbol.",
      "Saved index snapshots can differ from index state at the original action; recordedSymbolRank is retained separately.",
      "OR lexical search still allows partial matches and cannot infer behavioral concepts absent from indexed text.",
    ],
    summary: {
      queries: rows.length,
      targetSymbolTop8: { before: countAt("beforeSymbolRank", 8), after: countAt("afterSymbolRank", 8) },
      targetSymbolTop20: { before: countAt("beforeSymbolRank", 20), after: countAt("afterSymbolRank", 20) },
      targetFileTop8: { before: countAt("beforeFileRank", 8), after: countAt("afterFileRank", 8) },
    }, rows,
  };
  writeFileSync(outputPath, JSON.stringify(report, null, 2) + "\n");
  const label = (value) => value ?? "—";
  const markdown = [
    "# Search ranking replay", "",
    "Run: `node eval/replay_ranking.mjs`", "",
    `Replayed **${rows.length} unique historical task/query pairs** against copied pinned Pydantic indexes. The previous comparator and the current handler both receive the same index snapshot. The original indexes and dist files are untouched.`, "",
    "The change preserves exact identifiers, ranks lexical results by normalized BM25, treats trigrams as a separate fallback stage, and uses PageRank only on equal relevance. Repeated query words are deduplicated case-insensitively before lexical search; exact lookups retain the original query.", "",
    "| Navigation diagnostic | Before | After |", "|---|---:|---:|",
    ...Object.entries(report.summary).filter(([key]) => key !== "queries").map(([key, value]) => `| ${key} | ${value.before}/${rows.length} | ${value.after}/${rows.length} |`), "",
    "| Task | Historical query | Target symbol rank before | After | Target file rank before | After |",
    "|---|---|---:|---:|---:|---:|",
    ...rows.map((row) => `| ${row.task} | ${row.query.replaceAll("|", "\\|")} | ${label(row.beforeSymbolRank)} | ${label(row.afterSymbolRank)} | ${label(row.beforeFileRank)} | ${label(row.afterFileRank)} |`), "",
    "A dash means absent from the first 20 results. Symbol targets: `to_snake`, `ByteSize.human_readable`, and `FieldInfo.get_default` (including overload IDs). File rank is the first hit from the task's implementation file.", "",
    "## Interpretation and limits", "",
    ...report.caveats.map((caveat) => `- ${caveat}`), "",
    "The repeated-word issue was visible in `kebab case snake case alias conversion`: two OR clauses for `case` doubled generic case-match relevance. Deduplication prevents accidental weighting from phrasing. This does not introduce test, legacy-path, or repository-specific ranking penalties.", "",
    `Detailed results: \`${values.output}\`.`, "",
  ].join("\n");
  writeFileSync(resolve(root, values.markdown), markdown);
  console.log(JSON.stringify(report.summary, null, 2));
} finally {
  for (const storage of stores.values()) await storage.close();
  rmSync(temp, { recursive: true, force: true });
}
