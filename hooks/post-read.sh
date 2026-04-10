#!/usr/bin/env bash
# Codemesh Post-Read Hook — Nudge to enrich the graph after reading
# Reads tool input from stdin (JSON with tool_input.file_path)
# Checks if the file has a fresh concept cached
# If not, suggests calling codemesh_enrich()
# Always exits 0 (advisory only, never blocks)

set -euo pipefail

# Parse file_path from stdin JSON using python3
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    fp = data.get('tool_input', {}).get('file_path', '')
    print(fp)
except:
    print('')
" 2>/dev/null || echo "")

if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# Find .codemesh directory by walking up from cwd
find_codemesh_dir() {
    local dir="$PWD"
    while [ "$dir" != "/" ]; do
        if [ -d "$dir/.codemesh" ] && [ -f "$dir/.codemesh/codemesh.db" ]; then
            echo "$dir/.codemesh/codemesh.db"
            return 0
        fi
        dir=$(dirname "$dir")
    done
    return 1
}

DB_PATH=$(find_codemesh_dir 2>/dev/null) || exit 0

# Make file_path relative to project root
PROJECT_ROOT=$(dirname "$(dirname "$DB_PATH")")
REL_PATH="${FILE_PATH#$PROJECT_ROOT/}"

# Check if file has a fresh (non-stale) concept
HAS_FRESH_CONCEPT=$(sqlite3 "$DB_PATH" "
    SELECT COUNT(*)
    FROM nodes n
    JOIN edges e ON e.from_id = n.id
    JOIN nodes target ON e.to_id = target.id
    WHERE n.type = 'concept'
      AND e.type = 'describes'
      AND target.path = '$REL_PATH'
      AND COALESCE(json_extract(n.data, '$.stale'), 0) != 1;
" 2>/dev/null || echo "0")

if [ "$HAS_FRESH_CONCEPT" = "0" ]; then
    echo "[Codemesh] This file has no cached summary. Consider calling codemesh_enrich() with what you learned."
fi

exit 0
