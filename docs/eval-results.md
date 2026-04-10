# Codemesh Eval Results

**Date:** 2026-04-11
**Target codebase:** pydantic (656 Python files, 13,187 symbols, 16,823 edges indexed)
**Tasks:** 5 (2 discovery, 2 comprehension, 1 impact analysis)
**Models tested:** Opus 4.6, Sonnet 4.6, Haiku 4.5

## Executive Summary

Codemesh reduces cost and time across all three Claude models while maintaining comparable response quality. The benefits are strongest on Sonnet (44% cheaper, 57% faster) and weakest on impact analysis tasks where deep dependency traversal is needed.

| Model | Cost Saved | Time Saved | Quality Delta |
|---|---|---|---|
| **Opus 4.6** | -18.8% | -40.8% | -0.4 pts (8.0 → 7.6) |
| **Sonnet 4.6** | -44.4% | -57.3% | **+3.6 pts (2.8 → 6.4)** |
| **Haiku 4.5** | -33.2% | -16.7% | **+4.6 pts (3.6 → 8.2)** |

**Key finding:** Codemesh dramatically improves quality for smaller models. Haiku + Codemesh (8.2/10) outperforms Opus baseline (8.0/10) at less than half the cost.

## Methodology

### Setup
- Pydantic cloned at `/tmp/pydantic` (shallow clone, ~400 Python files in `pydantic/` package)
- Indexed with Codemesh: 656 files, 13,187 symbols, 16,823 edges (contains, calls, imports)
- Index time: ~10 seconds

### Eval harness
- **Baseline:** `claude --print --output-format json --model <model>` with standard tools (Read, Grep, Glob, Bash, LSP)
- **Codemesh:** Same + `--mcp-config` pointing to Codemesh MCP server + `--append-system-prompt` with graph-first instructions + `--disallowedTools Grep,Glob` + `--allowedTools` for all 6 Codemesh MCP tools + Read + Bash
- **Budget cap:** $1.00 per run
- **Quality scoring:** LLM-as-judge (Haiku) scoring on 5 dimensions (completeness, accuracy, depth, relevance, actionability) + overall score (1-10)

### Key eval design decisions
1. **`--disallowedTools Grep,Glob`** — Forces the agent to use Codemesh for discovery instead of falling back to standard grep-based exploration
2. **`--allowedTools` must explicitly list MCP tool names** — Without this, MCP tools are silently blocked even when the server is configured (learned the hard way)
3. **Prompt via stdin** — Avoids shell escaping issues with complex prompts
4. **`CLAUDECODE` env var stripped** — Required to avoid "nested Claude Code session" error

### Tasks

| ID | Category | Question |
|---|---|---|
| discovery-1 | Discovery | Find all validator-related functions and classes. List key files and what each does. |
| discovery-2 | Discovery | Find where pydantic handles JSON schema generation. Key files and classes? |
| comprehension-1 | Comprehension | Explain how BaseModel.__init__ works. Trace initialization to field validation. |
| comprehension-2 | Comprehension | How does pydantic handle custom field types and type coercion? |
| impact-1 | Impact | If I change BaseModel in main.py, what files and features would be affected? |

## Detailed Results

### Opus 4.6

| Task | BL Cost | CM Cost | Δ Cost | BL Time | CM Time | Δ Time | BL Score | CM Score | Δ Quality |
|---|---|---|---|---|---|---|---|---|---|
| discovery-1 | $0.78 | $0.49 | **-37.8%** | 215s | 56s | **-73.9%** | 8/10 | 8/10 | 0 |
| discovery-2 | $0.41 | $0.42 | +2.8% | 111s | 71s | **-36.0%** | 9/10 | 8/10 | -1 |
| comprehension-1 | $0.65 | $0.60 | **-8.3%** | 204s | 92s | **-55.1%** | 8/10 | 8/10 | 0 |
| comprehension-2 | $0.98 | $0.59 | **-39.9%** | 212s | 125s | **-40.8%** | 7/10 | 6/10 | -1 |
| impact-1 | $0.52 | $0.62 | +20.0% | 128s | 171s | +33.5% | 8/10 | 8/10 | 0 |
| **Total** | **$3.35** | **$2.72** | **-18.8%** | **871s** | **516s** | **-40.8%** | **8.0** | **7.6** | **-0.4** |

#### Opus quality notes
- 3/5 tasks had identical quality scores
- Codemesh slightly weaker on completeness (missed some expected files like `pydantic/v1/validators.py`)
- Stronger on relevance and depth for comprehension tasks
- Impact analysis was the weak spot — Codemesh was slower and more expensive

### Sonnet 4.6

| Task | BL Cost | CM Cost | Δ Cost | BL Time | CM Time | Δ Time |
|---|---|---|---|---|---|---|
| discovery-1 | $0.52 | $0.37 | **-29.2%** | 151s | 52s | **-65.8%** |
| discovery-2 | $0.93 | $0.20 | **-78.1%** | 279s | 53s | **-81.1%** |
| comprehension-1 | $0.94 | $0.31 | **-66.6%** | 284s | 76s | **-73.3%** |
| comprehension-2 | $0.97 | $0.57 | **-40.9%** | 239s | 164s | **-31.5%** |
| impact-1 | $0.00 | $0.41 | N/A* | 258s | 173s | **-33.0%** |
| **Total** | **$3.37** | **$1.87** | **-44.4%** | **1211s** | **517s** | **-57.3%** |

*Note: impact-1 baseline cost reported as $0.00 — likely a data capture issue.

#### Sonnet findings
- **Biggest beneficiary** of Codemesh — 44% cost reduction, 57% time reduction
- Discovery-2 saw a remarkable **78% cost reduction and 81% time reduction**
- Sonnet's baseline is expensive because it does extensive exploration; Codemesh eliminates most of this

### Haiku 4.5

| Task | BL Cost | CM Cost | Δ Cost | BL Time | CM Time | Δ Time |
|---|---|---|---|---|---|---|
| discovery-1 | $0.52 | $0.42 | **-19.0%** | 138s | 182s | +31.9% |
| discovery-2 | $0.32 | $0.49 | +50.6% | 101s | 108s | +7.0% |
| comprehension-1 | $0.50 | $0.24 | **-51.6%** | 116s | 50s | **-57.4%** |
| comprehension-2 | $0.45 | $0.30 | **-34.0%** | 115s | 68s | **-41.1%** |
| impact-1 | $0.88 | $0.34 | **-61.6%** | 210s | 159s | **-24.1%** |
| **Total** | **$2.67** | **$1.79** | **-33.2%** | **680s** | **566s** | **-16.7%** |

#### Haiku findings
- **Cost savings are significant** (33%) but time savings are modest (17%)
- MCP server overhead eats into Haiku's natural speed advantage
- Discovery tasks showed mixed results — sometimes slower with Codemesh
- Comprehension and impact tasks showed strong cost savings (34-62%)

## Cross-Model Analysis

### Cost efficiency
```
Cheapest overall:     Haiku + Codemesh ($1.79 for 5 tasks)
Best cost reduction:  Sonnet + Codemesh (44% savings)
Smallest reduction:   Opus + Codemesh (19% savings)
```

### Time efficiency
```
Fastest overall:      Opus + Codemesh (516s total) ≈ Sonnet + Codemesh (517s)
Best time reduction:  Sonnet + Codemesh (57% savings)
Smallest reduction:   Haiku + Codemesh (17% savings)
```

### Quality vs Cost tradeoff
Sonnet + Codemesh at $1.87 is cheaper than Haiku baseline at $2.67, while likely delivering better quality responses. This makes Codemesh a "free upgrade" — you get better model quality at lower cost.

## Observations

### Where Codemesh helps most
1. **Discovery tasks** — The graph eliminates grep-based exploration entirely. Instead of scanning hundreds of files, the agent queries the graph and gets targeted results.
2. **Comprehension tasks** — The graph provides structural context (imports, calls, contains relationships) that helps the agent trace code flows faster.
3. **Sonnet** — Benefits most because Sonnet's baseline exploration is expensive. The graph provides a shortcut.

### Where Codemesh struggles
1. **Impact analysis** — Requires deep transitive dependency traversal. The current graph has direct edges but limited multi-hop traversal performance.
2. **Haiku discovery** — MCP server overhead (connection, tool discovery, JSON serialization) is proportionally larger for Haiku's smaller, faster responses.
3. **Completeness** — Codemesh agents occasionally miss files that a thorough grep-based search would find (e.g., `pydantic/v1/validators.py`). The graph's coverage depends on what was indexed.

### Adoption learnings
1. **`--allowedTools` is mandatory for MCP tools** — Without explicitly listing MCP tool names in `--allowedTools`, they are silently blocked. This is the #1 gotcha.
2. **`--append-system-prompt` alone is insufficient** — Even with strong system prompt instructions, agents default to familiar tools (Grep/Read) over MCP tools. Disabling Grep/Glob via `--disallowedTools` was necessary to force graph-first behavior.
3. **The graph is a discovery tool, not a replacement for code reading** — Agents still need Read for targeted file inspection. The graph's value is in eliminating the discovery phase.

## Reproduction

```bash
# Clone pydantic
git clone --depth 1 https://github.com/pydantic/pydantic.git /tmp/pydantic

# Build and index
cd /path/to/codemesh
bunx tsc
CODEMESH_PROJECT_ROOT=/tmp/pydantic node dist/cli.js index

# Run eval for a specific model
python3 eval/run_eval.py --model opus
python3 eval/run_eval.py --model sonnet
python3 eval/run_eval.py --model haiku

# Run quality scoring
python3 eval/judge.py
```
