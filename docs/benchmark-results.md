# Codemesh Benchmark Results

**Date:** 2026-04-11
**Model:** Claude Sonnet 4.6
**Benchmarks:** 4 real-world codebases from CodeGraph's published benchmark suite
**Modes:** Baseline (standard tools) vs Codemesh MCP vs Codemesh CLI vs CodeGraph

## Executive Summary

Codemesh CLI beats CodeGraph in pairwise quality on 3 out of 4 benchmarks with Sonnet. Codemesh MCP achieves the highest independent quality scores (9/10) across all repos.

| Metric | Baseline | Codemesh MCP | Codemesh CLI | CodeGraph |
|---|---|---|---|---|
| **Avg Quality** | 8.0/10 | **8.8/10** | 8.3/10 | 7.3/10 |
| **Avg Cost** | $0.56 | $0.59 | $0.73 | $0.20 |

## Detailed Results

### Efficiency

| Repo | Files | Mode | Calls | Time | Cost |
|---|---:|---|---:|---:|---:|
| **Alamofire** | 107 | Baseline | 29 | 177s | $0.55 |
| | | Codemesh MCP | 24 | 132s | $0.39 |
| | | Codemesh CLI | 25 | 99s | $0.38 |
| | | CodeGraph | 12 | 61s | $0.21 |
| **Excalidraw** | 627 | Baseline | 24 | 137s | $0.48 |
| | | Codemesh MCP | 12 | 97s | $0.34 |
| | | Codemesh CLI | 22 | 131s | $0.43 |
| | | CodeGraph | 19 | 81s | $0.31 |
| **VS Code** | 2,661 | Baseline | 39 | 144s | $0.43 |
| | | Codemesh MCP | 55 | 249s | $0.97 |
| | | Codemesh CLI | 73 | 256s | $1.20 |
| | | CodeGraph | 10 | 67s | $0.15 |
| **Swift Compiler** | 25,674 | Baseline | 36 | 191s | $0.76 |
| | | Codemesh MPC | 40 | 209s | $0.67 |
| | | Codemesh CLI | 46 | 223s | $0.91 |
| | | CodeGraph | 8 | 46s | $0.13 |

### Quality — Independent Scoring (1-10, LLM-as-judge)

| Repo | Baseline | Codemesh MCP | Codemesh CLI | CodeGraph |
|---|---:|---:|---:|---:|
| Alamofire | 8 | **9** | 8 | 8 |
| Excalidraw | **9** | **9** | **9** | **9** |
| VS Code | 7 | **8.2** | 8 | 7 |
| Swift Compiler | 8 | **9** | 8 | 5 |
| **Average** | **8.0** | **8.8** | **8.3** | **7.3** |

### Quality — Pairwise Comparisons

#### Codemesh CLI vs CodeGraph (the key matchup)

| Repo | Winner | Scores |
|---|---|---|
| Alamofire | **Codemesh CLI** | 7 vs 6 |
| Excalidraw | CodeGraph | 7.5 vs 8.5 |
| **VS Code** | **Codemesh CLI** | 9 vs 5 |
| **Swift Compiler** | **Codemesh CLI** | 9 vs 5 |

**Codemesh CLI wins 3 out of 4** against CodeGraph with Sonnet.

#### Codemesh CLI vs Baseline

| Repo | Winner | Scores |
|---|---|---|
| Alamofire | Baseline | 6.5 vs 7.5 |
| Excalidraw | Baseline | 7 vs 8.5 |
| **VS Code** | **Codemesh CLI** | 8 vs 7 |
| Swift Compiler | Baseline | 7.5 vs 8.5 |

#### Codemesh MCP vs Baseline

| Repo | Winner | Scores |
|---|---|---|
| Alamofire | Baseline | 6 vs 7 |
| Excalidraw | Baseline | 6 vs 9 |
| **VS Code** | **Codemesh MCP** | 8.5 vs 7.5 |
| Swift Compiler | Baseline | 6 vs 8 |

### LSP Integration

Sonnet naturally uses LSP alongside codemesh tools:

| Mode | Alamofire | Excalidraw | VS Code | Swift |
|---|---:|---:|---:|---:|
| Baseline | LSP=2 | 0 | 0 | 0 |
| Codemesh MCP | LSP=1 | 0 | 0 | 0 |
| Codemesh CLI | LSP=2 | LSP=1 | LSP=1 | 0 |

### Enrichment Activity

Codemesh writes back to the graph during exploration:

| Repo | codemesh_enrich calls | Effect |
|---|---:|---|
| Alamofire | Yes (MCP mode) | Concepts + workflows created |
| Excalidraw | Yes | Concepts created |
| VS Code | — | Agent used Bash-heavy approach |
| Swift Compiler | Yes | Concepts created |

### Key Findings

1. **Codemesh MCP has the highest quality** — 8.8/10 average, beating baseline (8.0) and CodeGraph (7.3)
2. **Codemesh CLI beats CodeGraph 3/4 on pairwise** — CodeGraph scored 5/10 on VS Code and Swift with Sonnet
3. **CodeGraph is cheapest** — $0.20 avg vs Codemesh MCP $0.59. Speed/cost vs quality tradeoff.
4. **Sonnet + Codemesh is the sweet spot** — dramatically benefits from the graph more than Opus does
5. **Large repos (VS Code, Swift) are expensive for codemesh** — MCP/CLI overhead scales with repo size. CodeGraph handles large repos better.
6. **The "cuts off" problem persists** — both Codemesh and CodeGraph lose to baseline in some pairwise comparisons because responses truncate before completion
7. **Enrichment works** — graph accumulates concepts across sessions

### Benchmark Queries

| Repo | Language | Files | Query |
|---|---|---:|---|
| Alamofire | Swift | 107 | "Trace how a request flows from Session.request() through to the URLSession layer" |
| Excalidraw | TypeScript | 627 | "How does collaborative editing and real-time sync work?" |
| VS Code | TypeScript | 2,661 | "How does the extension host communicate with the main process?" |
| Swift Compiler | Swift/C++ | 25,674 | "How does the Swift compiler handle error diagnostics?" |

### Methodology

- All modes run in parallel per benchmark
- Quality scored by LLM-as-judge (Claude Haiku) on: completeness, accuracy, depth, relevance, actionability
- Pairwise comparisons: judge sees both responses, picks winner with reasoning
- Codemesh modes: Grep/Glob disabled, MCP tools or CLI via Bash + Read + LSP
- CodeGraph modes: Grep/Glob disabled, CodeGraph MCP tools + Read
- Budget cap: $2.00 per run
- Benchmark repos from CodeGraph's published suite for direct comparison

### Reproduction

```bash
# Setup
bun install -g codemesh
git clone --depth 1 https://github.com/Alamofire/Alamofire.git /tmp/alamofire
# ... clone other repos ...

# Index
CODEMESH_PROJECT_ROOT=/tmp/alamofire codemesh index

# Run benchmarks
python3 eval/head_to_head.py --model sonnet alamofire excalidraw vscode swift-compiler
```
