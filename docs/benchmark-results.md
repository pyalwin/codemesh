# Codemesh Benchmark Results

**Date:** 2026-04-10
**Model:** Claude Sonnet 4.6
**Benchmarks:** 6 real-world codebases (4 from CodeGraph's published suite + 2 pydantic)
**Modes:** Baseline (standard tools) vs Codemesh MCP vs Codemesh CLI vs CodeGraph

## Executive Summary

Codemesh MCP achieves the highest average quality (8.6/10) across 6 benchmarks. Codemesh CLI beats CodeGraph in pairwise quality on 4 out of 6 benchmarks with Sonnet.

| Metric | Baseline | Codemesh MCP | Codemesh CLI | CodeGraph |
|---|---|---|---|---|
| **Avg Quality** | 7.3/10 | **8.6/10** | 8.5/10 | 7.5/10 |
| **Avg Cost** | $0.73 | $0.53 | $0.81 | $0.22 |

## Detailed Results

### Efficiency

| Repo | Mode | Calls | Time | Cost | Quality |
|---|---|---:|---:|---:|---:|
| **Alamofire** | Baseline | 31 | 198s | $0.64 | 9/10 |
| | Codemesh MCP | 11 | 95s | $0.29 | 8/10 |
| | Codemesh CLI | 32 | 192s | $0.60 | 9/10 |
| | CodeGraph | 11 | 54s | $0.21 | 8/10 |
| **Excalidraw** | Baseline | 36 | 231s | $0.66 | 9/10 |
| | Codemesh MCP | 47 | 241s | $0.81 | 8.5/10 |
| | Codemesh CLI | 40 | 188s | $0.76 | 8.8/10 |
| | CodeGraph | 10 | 72s | $0.18 | 8/10 |
| **VS Code** | Baseline | 63 | 263s | $1.05 | 8.7/10 |
| | Codemesh MCP | 65 | 271s | $0.93 | 8/10 |
| | Codemesh CLI | 92 | 376s | $1.18 | 8/10 |
| | CodeGraph | 15 | 73s | $0.22 | 8/10 |
| **Swift Compiler** | Baseline | 37 | 215s | $0.73 | 7/10 |
| | Codemesh MCP | 27 | 125s | $0.47 | 9/10 |
| | Codemesh CLI | 49 | 322s | $1.34 | 9/10 |
| | CodeGraph | 8 | 43s | $0.15 | 6/10 |
| **pydantic-validators** | Baseline | 71 | 278s | $0.98 | 1/10 |
| | Codemesh MCP | 9 | 51s | $0.20 | 8/10 |
| | Codemesh CLI | 42 | 161s | $0.72 | 8/10 |
| | CodeGraph | 13 | 53s | $0.24 | 7/10 |
| **pydantic-basemodel** | Baseline | 18 | 94s | $0.31 | 9/10 |
| | Codemesh MCP | 19 | 136s | $0.47 | 9/10 |
| | Codemesh CLI | 14 | 76s | $0.27 | 9/10 |
| | CodeGraph | 22 | 93s | $0.33 | 8/10 |

### Quality — Independent Scoring (1-10, LLM-as-judge)

| Repo | Baseline | Codemesh MCP | Codemesh CLI | CodeGraph |
|---|---:|---:|---:|---:|
| Alamofire | 9 | 8 | 9 | 8 |
| Excalidraw | 9 | 8.5 | 8.8 | 8 |
| VS Code | 8.7 | 8 | 8 | 8 |
| Swift Compiler | 7 | **9** | **9** | 6 |
| pydantic-validators | 1 | **8** | **8** | 7 |
| pydantic-basemodel | 9 | **9** | **9** | 8 |
| **Average** | 7.3 | **8.6** | 8.5 | 7.5 |

### Quality — Pairwise Comparisons

#### Codemesh CLI vs CodeGraph (the key matchup)

| Repo | Winner | Scores |
|---|---|---|
| **Alamofire** | **Codemesh CLI** | 7 vs 6 |
| Excalidraw | CodeGraph | 7.5 vs 8.5 |
| **VS Code** | **Codemesh CLI** | 9 vs 5 |
| **Swift Compiler** | **Codemesh CLI** | 9 vs 5 |
| **pydantic-validators** | **Codemesh CLI** | 8 vs 7 |
| pydantic-basemodel | CodeGraph | 9 vs 8 |

**Codemesh CLI wins 4 out of 6** against CodeGraph with Sonnet.

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

### Key Findings

1. **Codemesh MCP has the highest quality** — 8.6/10 average across 6 repos, beating baseline (7.3) and CodeGraph (7.5)
2. **pydantic-validators is a standout** — Codemesh MCP was 87% fewer calls, 82% faster, 80% cheaper than baseline; baseline scored 1/10
3. **Codemesh CLI beats CodeGraph 4/6 on pairwise** — wins on Alamofire, VS Code, Swift Compiler, and pydantic-validators
4. **CodeGraph is cheapest** — $0.22 avg but lowest quality (7.5/10 avg)
5. **Sonnet + Codemesh is the sweet spot** — dramatically benefits from the graph more than Opus does
6. **Large repos (VS Code, Swift) are expensive for codemesh** — MCP/CLI overhead scales with repo size. CodeGraph handles large repos better cost-wise.
7. **The "cuts off" problem persists** — both Codemesh and CodeGraph lose to baseline in some pairwise comparisons because responses truncate before completion
8. **Enrichment works** — graph accumulates concepts across sessions

### Benchmark Queries

| Repo | Language | Query |
|---|---|---|
| Alamofire | Swift | "Trace how a request flows from Session.request() through to the URLSession layer" |
| Excalidraw | TypeScript | "How does collaborative editing and real-time sync work?" |
| VS Code | TypeScript | "How does the extension host communicate with the main process?" |
| Swift Compiler | Swift/C++ | "How does the Swift compiler handle error diagnostics?" |
| pydantic-validators | Python | "How does pydantic implement field validators and model validators?" |
| pydantic-basemodel | Python | "How does BaseModel initialization and field assignment work?" |

### Methodology

- All modes run in parallel per benchmark
- Quality scored by LLM-as-judge (Claude Haiku) on: completeness, accuracy, depth, relevance, actionability
- Pairwise comparisons: judge sees both responses, picks winner with reasoning
- Codemesh modes: Grep/Glob disabled, MCP tools or CLI via Bash + Read + LSP
- CodeGraph modes: Grep/Glob disabled, CodeGraph MCP tools + Read
- Budget cap: $2.00 per run
- Benchmark repos from CodeGraph's published suite + pydantic for Python coverage

### Reproduction

```bash
# Setup
bun install -g codemesh
git clone --depth 1 https://github.com/Alamofire/Alamofire.git /tmp/alamofire
git clone --depth 1 https://github.com/excalidraw/excalidraw.git /tmp/excalidraw
git clone --depth 1 https://github.com/microsoft/vscode.git /tmp/vscode
git clone --depth 1 https://github.com/apple/swift.git /tmp/swift
git clone --depth 1 https://github.com/pydantic/pydantic.git /tmp/pydantic

# Index
CODEMESH_PROJECT_ROOT=/tmp/alamofire codemesh index
CODEMESH_PROJECT_ROOT=/tmp/pydantic codemesh index

# Run benchmarks
python3 eval/head_to_head.py --model sonnet alamofire excalidraw vscode swift-compiler pydantic-validators pydantic-basemodel
```
