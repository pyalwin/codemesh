#!/usr/bin/env node
/** Replay recorded successful observations through the current CLI formatter. */

import { readFileSync } from 'node:fs';
import { compactExploreResult } from '../dist/cli/compact.js';

const source = process.argv[2];
if (!source) {
  process.stderr.write('Usage: node eval/replay_compact.mjs <runs.jsonl>\n');
  process.exit(1);
}

const rows = readFileSync(source, 'utf8').trim().split('\n').map(JSON.parse);
const allowed = new Set(['map', 'search', 'source', 'recall']);
const graded = new Set(['complete', 'turn_limit', 'context_limit']);
const totals = {};
for (const row of rows) {
  if (row.mode !== 'codemesh' || !graded.has(row.status)) continue;
  for (const phase of row.phases ?? []) {
    for (const action of phase.actions ?? []) {
      if (!allowed.has(action.action) || action.error) continue;
      let parsed;
      try { parsed = JSON.parse(action.observation); } catch { continue; }
      const compact = compactExploreResult(action.action, parsed);
      const group = totals[action.action] ??= { calls: 0, beforeBytes: 0, afterBytes: 0 };
      group.calls++;
      group.beforeBytes += Buffer.byteLength(action.observation);
      group.afterBytes += Buffer.byteLength(JSON.stringify(compact));
    }
  }
}
const all = { calls: 0, beforeBytes: 0, afterBytes: 0 };
for (const group of Object.values(totals)) {
  all.calls += group.calls;
  all.beforeBytes += group.beforeBytes;
  all.afterBytes += group.afterBytes;
}
const withPct = (group) => ({ ...group, reductionPct: group.beforeBytes
  ? Math.round((1 - group.afterBytes / group.beforeBytes) * 1000) / 10 : 0 });
console.log(JSON.stringify({ byAction: Object.fromEntries(
  Object.entries(totals).map(([name, group]) => [name, withPct(group)])),
  total: withPct(all) }, null, 2));
