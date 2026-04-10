#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TASKS_FILE="$SCRIPT_DIR/tasks.json"
MCP_CONFIG="$SCRIPT_DIR/mcp-config.json"
RESULTS_DIR="$SCRIPT_DIR/results"
TARGET_CWD="/tmp/pydantic"

CODEMESH_PREAMBLE="You have access to codemesh_* MCP tools. Before using Grep or Read, first try codemesh_query to find relevant files. After reading code, use codemesh_enrich to record what you learned. "

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Codemesh Eval Framework ===${NC}"
echo ""

# Validate prerequisites
if ! command -v claude &> /dev/null; then
    echo -e "${RED}Error: 'claude' CLI not found. Install Claude Code first.${NC}"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    echo -e "${RED}Error: 'jq' not found. Install it with: brew install jq${NC}"
    exit 1
fi

if [ ! -d "$TARGET_CWD" ]; then
    echo -e "${RED}Error: Pydantic not found at $TARGET_CWD. Clone it first.${NC}"
    exit 1
fi

if [ ! -f "$TASKS_FILE" ]; then
    echo -e "${RED}Error: tasks.json not found at $TASKS_FILE${NC}"
    exit 1
fi

# Create output directories
mkdir -p "$RESULTS_DIR/baseline"
mkdir -p "$RESULTS_DIR/codemesh"

# Count tasks
TASK_COUNT=$(jq '.tasks | length' "$TASKS_FILE")
echo -e "${GREEN}Found $TASK_COUNT tasks to evaluate${NC}"
echo ""

# Optional: only run specific task IDs (pass as args)
FILTER_TASKS=("$@")

run_task() {
    local idx=$1
    local mode=$2  # "baseline" or "codemesh"

    local task_id
    task_id=$(jq -r ".tasks[$idx].id" "$TASKS_FILE")

    # Skip if filter is set and this task isn't in it
    if [ ${#FILTER_TASKS[@]} -gt 0 ]; then
        local found=false
        for ft in "${FILTER_TASKS[@]}"; do
            if [ "$ft" = "$task_id" ]; then
                found=true
                break
            fi
        done
        if [ "$found" = false ]; then
            return 0
        fi
    fi

    local category
    category=$(jq -r ".tasks[$idx].category" "$TASKS_FILE")
    local prompt
    prompt=$(jq -r ".tasks[$idx].prompt" "$TASKS_FILE")

    local outfile="$RESULTS_DIR/$mode/${task_id}.jsonl"
    local errfile="$RESULTS_DIR/$mode/${task_id}.stderr"

    echo -e "  ${YELLOW}[$mode]${NC} Running task ${BLUE}$task_id${NC} ($category)..."

    local full_prompt="$prompt"
    local mcp_args=()

    if [ "$mode" = "codemesh" ]; then
        full_prompt="${CODEMESH_PREAMBLE}${prompt}"
        mcp_args=(--mcp-config "$MCP_CONFIG")
    fi

    # Run Claude Code in headless mode with stream-json output
    if claude -p "$full_prompt" \
        --output-format stream-json \
        --max-turns 25 \
        --cwd "$TARGET_CWD" \
        --no-input \
        "${mcp_args[@]}" \
        > "$outfile" 2>"$errfile"; then
        echo -e "  ${GREEN}[OK]${NC} $mode/$task_id completed"
    else
        echo -e "  ${RED}[FAIL]${NC} $mode/$task_id failed (exit code $?)"
        echo -e "  ${RED}       Check $errfile for details${NC}"
    fi
}

# Run baseline evaluations
echo -e "${BLUE}--- Phase 1: Baseline (no Codemesh) ---${NC}"
for ((i=0; i<TASK_COUNT; i++)); do
    run_task "$i" "baseline"
done
echo ""

# Run codemesh evaluations
echo -e "${BLUE}--- Phase 2: Codemesh-augmented ---${NC}"
for ((i=0; i<TASK_COUNT; i++)); do
    run_task "$i" "codemesh"
done
echo ""

# Run analysis
echo -e "${BLUE}--- Phase 3: Analysis ---${NC}"
if command -v bun &> /dev/null; then
    bun run "$SCRIPT_DIR/analyze.ts"
else
    echo -e "${RED}Error: 'bun' not found. Install it to run analysis.${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}=== Eval complete ===${NC}"
