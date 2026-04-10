#!/usr/bin/env bash
# Codemesh Session-Start Hook — Check index freshness on session start
# If no DB exists, prompts to run `codemesh index`
# If DB exists, checks git for changed files since last index
# Always exits 0 (advisory only, never blocks)

set -euo pipefail

# Find .codemesh directory by walking up from cwd
find_codemesh_dir() {
    local dir="$PWD"
    while [ "$dir" != "/" ]; do
        if [ -d "$dir/.codemesh" ]; then
            echo "$dir/.codemesh"
            return 0
        fi
        dir=$(dirname "$dir")
    done
    return 1
}

CODEMESH_DIR=$(find_codemesh_dir 2>/dev/null) || true
DB_PATH="${CODEMESH_DIR}/codemesh.db"
PROJECT_ROOT=$(dirname "${CODEMESH_DIR:-/nonexistent}")

# Case 1: No .codemesh directory or no DB
if [ -z "${CODEMESH_DIR:-}" ] || [ ! -f "$DB_PATH" ]; then
    echo "[Codemesh] No code knowledge graph found. Run \`codemesh index\` to build one."
    exit 0
fi

# Case 2: DB exists — check git for changes since last index
LAST_INDEXED=$(sqlite3 "$DB_PATH" "
    SELECT json_extract(data, '$.lastIndexedAt')
    FROM nodes
    WHERE type = 'file'
    ORDER BY json_extract(data, '$.lastIndexedAt') DESC
    LIMIT 1;
" 2>/dev/null || echo "")

if [ -z "$LAST_INDEXED" ]; then
    echo "[Codemesh] Graph exists but no files indexed. Run \`codemesh index\`."
    exit 0
fi

# Check if we're in a git repo
if ! git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    exit 0
fi

# Count files changed since last index timestamp
# Convert ISO timestamp to git-compatible format
CHANGED_COUNT=$(git -C "$PROJECT_ROOT" diff --name-only --diff-filter=ACMR HEAD 2>/dev/null | wc -l | tr -d ' ')
UNTRACKED_COUNT=$(git -C "$PROJECT_ROOT" ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')
TOTAL_CHANGES=$((CHANGED_COUNT + UNTRACKED_COUNT))

if [ "$TOTAL_CHANGES" -gt 0 ]; then
    echo "[Codemesh] ${TOTAL_CHANGES} file(s) changed since last index. Consider running \`codemesh index\` to update the graph."
fi

exit 0
