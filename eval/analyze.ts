import { readdir, readFile } from "fs/promises";
import { join } from "path";

// ── Types ───────────────────────────────────────────────────────────────────

interface Task {
  id: string;
  category: string;
  prompt: string;
  expected_files: string[];
  rubric: string;
}

interface TasksFile {
  tasks: Task[];
}

interface StreamEvent {
  type: string;
  subtype?: string;
  tool?: { name: string; input?: Record<string, unknown> };
  message?: { content: Array<{ type: string; text?: string }> };
  content?: string;
  cost_usd?: number;
  total_cost_usd?: number;
  duration_ms?: number;
  duration_api_ms?: number;
  num_turns?: number;
  result?: string;
  is_error?: boolean;
  session_id?: string;
}

interface TaskMetrics {
  taskId: string;
  category: string;
  toolCalls: Record<string, number>;
  totalToolCalls: number;
  explorationCalls: number; // Read + Grep + Glob
  costUsd: number;
  durationMs: number;
  durationApiMs: number;
  numTurns: number;
  resultText: string;
  isError: boolean;
}

interface Comparison {
  taskId: string;
  category: string;
  baseline: TaskMetrics;
  codemesh: TaskMetrics;
  explorationReduction: number; // percentage
  totalCallReduction: number; // percentage
  costReduction: number; // percentage
  turnReduction: number; // percentage
}

// ── Constants ───────────────────────────────────────────────────────────────

const EVAL_DIR = import.meta.dir;
const RESULTS_DIR = join(EVAL_DIR, "results");
const TASKS_FILE = join(EVAL_DIR, "tasks.json");

const EXPLORATION_TOOLS = new Set(["Read", "Grep", "Glob"]);
const CODEMESH_TOOLS_PREFIX = "mcp__codemesh__";

// ── Parsing ─────────────────────────────────────────────────────────────────

function parseStreamJsonl(content: string): StreamEvent[] {
  const events: StreamEvent[] = [];
  for (const line of content.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      events.push(JSON.parse(trimmed) as StreamEvent);
    } catch {
      // Skip malformed lines (stderr leakage, etc.)
    }
  }
  return events;
}

function extractMetrics(events: StreamEvent[], taskId: string, category: string): TaskMetrics {
  const toolCalls: Record<string, number> = {};
  let totalToolCalls = 0;
  let costUsd = 0;
  let durationMs = 0;
  let durationApiMs = 0;
  let numTurns = 0;
  let resultText = "";
  let isError = false;

  for (const event of events) {
    if (event.type === "tool_use") {
      const name = event.tool?.name ?? "unknown";
      toolCalls[name] = (toolCalls[name] ?? 0) + 1;
      totalToolCalls++;
    }

    if (event.type === "result") {
      costUsd = event.cost_usd ?? event.total_cost_usd ?? 0;
      durationMs = event.duration_ms ?? 0;
      durationApiMs = event.duration_api_ms ?? 0;
      numTurns = event.num_turns ?? 0;
      resultText = event.result ?? "";
      isError = event.is_error ?? false;
    }
  }

  const explorationCalls = [...EXPLORATION_TOOLS].reduce(
    (sum, tool) => sum + (toolCalls[tool] ?? 0),
    0
  );

  return {
    taskId,
    category,
    toolCalls,
    totalToolCalls,
    explorationCalls,
    costUsd,
    durationMs,
    durationApiMs,
    numTurns,
    resultText,
    isError,
  };
}

// ── Loading ─────────────────────────────────────────────────────────────────

async function loadTaskMetrics(
  mode: "baseline" | "codemesh",
  tasks: Task[]
): Promise<Map<string, TaskMetrics>> {
  const dir = join(RESULTS_DIR, mode);
  const metrics = new Map<string, TaskMetrics>();

  let files: string[];
  try {
    files = await readdir(dir);
  } catch {
    console.error(`  No results found in ${dir}`);
    return metrics;
  }

  for (const task of tasks) {
    const filename = `${task.id}.jsonl`;
    if (!files.includes(filename)) {
      console.error(`  Missing result: ${mode}/${filename}`);
      continue;
    }

    const content = await readFile(join(dir, filename), "utf-8");
    const events = parseStreamJsonl(content);
    const m = extractMetrics(events, task.id, task.category);
    metrics.set(task.id, m);
  }

  return metrics;
}

// ── Analysis ────────────────────────────────────────────────────────────────

function percentChange(baseline: number, codemesh: number): number {
  if (baseline === 0) return 0;
  return ((baseline - codemesh) / baseline) * 100;
}

function buildComparisons(
  baseline: Map<string, TaskMetrics>,
  codemesh: Map<string, TaskMetrics>,
  tasks: Task[]
): Comparison[] {
  const comparisons: Comparison[] = [];

  for (const task of tasks) {
    const b = baseline.get(task.id);
    const c = codemesh.get(task.id);
    if (!b || !c) continue;

    comparisons.push({
      taskId: task.id,
      category: task.category,
      baseline: b,
      codemesh: c,
      explorationReduction: percentChange(b.explorationCalls, c.explorationCalls),
      totalCallReduction: percentChange(b.totalToolCalls, c.totalToolCalls),
      costReduction: percentChange(b.costUsd, c.costUsd),
      turnReduction: percentChange(b.numTurns, c.numTurns),
    });
  }

  return comparisons;
}

// ── Formatting ──────────────────────────────────────────────────────────────

function pad(s: string, len: number): string {
  return s.padEnd(len);
}

function rpad(s: string, len: number): string {
  return s.padStart(len);
}

function fmtPct(pct: number): string {
  const sign = pct >= 0 ? "-" : "+";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

function fmtCost(usd: number): string {
  return `$${usd.toFixed(4)}`;
}

function fmtDuration(ms: number): string {
  if (ms === 0) return "n/a";
  const secs = ms / 1000;
  if (secs < 60) return `${secs.toFixed(1)}s`;
  return `${Math.floor(secs / 60)}m${Math.round(secs % 60)}s`;
}

function printToolBreakdown(label: string, metrics: TaskMetrics): void {
  const entries = Object.entries(metrics.toolCalls).sort(
    ([, a], [, b]) => b - a
  );
  console.log(`    ${label}:`);
  for (const [tool, count] of entries) {
    const isCodemesh = tool.startsWith(CODEMESH_TOOLS_PREFIX) || tool.startsWith("codemesh_");
    const marker = EXPLORATION_TOOLS.has(tool)
      ? " (exploration)"
      : isCodemesh
        ? " (codemesh)"
        : "";
    console.log(`      ${tool}: ${count}${marker}`);
  }
}

function printReport(comparisons: Comparison[]): void {
  if (comparisons.length === 0) {
    console.log("\nNo completed comparisons found. Run the eval first.\n");
    return;
  }

  // ── Summary table ──
  console.log("\n## Codemesh Eval Results\n");
  console.log(
    "| Task | Category | Baseline Explore | Codemesh Explore | Reduction | Baseline Cost | Codemesh Cost | Cost Saved |"
  );
  console.log(
    "|------|----------|-----------------|-----------------|-----------|---------------|---------------|------------|"
  );

  for (const c of comparisons) {
    console.log(
      `| ${pad(c.taskId, 18)} | ${pad(c.category, 14)} | ${rpad(
        String(c.baseline.explorationCalls),
        15
      )} | ${rpad(String(c.codemesh.explorationCalls), 15)} | ${rpad(
        fmtPct(c.explorationReduction),
        9
      )} | ${rpad(fmtCost(c.baseline.costUsd), 13)} | ${rpad(
        fmtCost(c.codemesh.costUsd),
        13
      )} | ${rpad(fmtPct(c.costReduction), 10)} |`
    );
  }

  // ── Aggregates ──
  const avgExploreReduction =
    comparisons.reduce((s, c) => s + c.explorationReduction, 0) /
    comparisons.length;
  const avgCostReduction =
    comparisons.reduce((s, c) => s + c.costReduction, 0) /
    comparisons.length;
  const avgTotalCallReduction =
    comparisons.reduce((s, c) => s + c.totalCallReduction, 0) /
    comparisons.length;
  const avgTurnReduction =
    comparisons.reduce((s, c) => s + c.turnReduction, 0) /
    comparisons.length;

  const totalBaselineCost = comparisons.reduce(
    (s, c) => s + c.baseline.costUsd,
    0
  );
  const totalCodemeshCost = comparisons.reduce(
    (s, c) => s + c.codemesh.costUsd,
    0
  );

  console.log("\n### Aggregate Metrics\n");
  console.log(`- **Avg exploration call reduction:** ${fmtPct(avgExploreReduction)}`);
  console.log(`- **Avg total tool call reduction:** ${fmtPct(avgTotalCallReduction)}`);
  console.log(`- **Avg turn reduction:** ${fmtPct(avgTurnReduction)}`);
  console.log(`- **Avg cost reduction:** ${fmtPct(avgCostReduction)}`);
  console.log(
    `- **Total baseline cost:** ${fmtCost(totalBaselineCost)}`
  );
  console.log(
    `- **Total codemesh cost:** ${fmtCost(totalCodemeshCost)}`
  );
  console.log(
    `- **Total saved:** ${fmtCost(totalBaselineCost - totalCodemeshCost)}`
  );

  // ── Per-task detail ──
  console.log("\n### Per-Task Detail\n");
  for (const c of comparisons) {
    console.log(`#### ${c.taskId} (${c.category})`);
    console.log(`  Baseline: ${c.baseline.totalToolCalls} tool calls, ${c.baseline.explorationCalls} exploration, ${fmtCost(c.baseline.costUsd)}, ${fmtDuration(c.baseline.durationMs)}, ${c.baseline.numTurns} turns`);
    console.log(`  Codemesh: ${c.codemesh.totalToolCalls} tool calls, ${c.codemesh.explorationCalls} exploration, ${fmtCost(c.codemesh.costUsd)}, ${fmtDuration(c.codemesh.durationMs)}, ${c.codemesh.numTurns} turns`);
    console.log(`  Explore reduction: ${fmtPct(c.explorationReduction)}, Cost reduction: ${fmtPct(c.costReduction)}`);
    printToolBreakdown("Baseline tools", c.baseline);
    printToolBreakdown("Codemesh tools", c.codemesh);
    console.log("");
  }

  // ── Codemesh tool usage summary ──
  const codemeshToolTotals: Record<string, number> = {};
  for (const c of comparisons) {
    for (const [tool, count] of Object.entries(c.codemesh.toolCalls)) {
      if (tool.startsWith(CODEMESH_TOOLS_PREFIX) || tool.startsWith("codemesh_")) {
        codemeshToolTotals[tool] = (codemeshToolTotals[tool] ?? 0) + count;
      }
    }
  }

  if (Object.keys(codemeshToolTotals).length > 0) {
    console.log("### Codemesh Tool Usage (total across all tasks)\n");
    for (const [tool, count] of Object.entries(codemeshToolTotals).sort(
      ([, a], [, b]) => b - a
    )) {
      console.log(`  ${tool}: ${count}`);
    }
    console.log("");
  }
}

// ── Main ────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  console.log("Codemesh Eval Analyzer");
  console.log("======================\n");

  // Load tasks
  const tasksContent = await readFile(TASKS_FILE, "utf-8");
  const { tasks } = JSON.parse(tasksContent) as TasksFile;
  console.log(`Loaded ${tasks.length} tasks from tasks.json\n`);

  // Load results
  console.log("Loading baseline results...");
  const baseline = await loadTaskMetrics("baseline", tasks);
  console.log(`  Found ${baseline.size} baseline results\n`);

  console.log("Loading codemesh results...");
  const codemesh = await loadTaskMetrics("codemesh", tasks);
  console.log(`  Found ${codemesh.size} codemesh results\n`);

  // Build comparisons
  const comparisons = buildComparisons(baseline, codemesh, tasks);

  // Print report
  printReport(comparisons);
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
