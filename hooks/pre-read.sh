#!/usr/bin/env bash
# Codemesh Pre-Read Hook — Advisory enrichment before file reads
# Reads tool input from stdin (JSON with tool_input.file_path)
# Queries the codemesh graph for cached summaries and workflows
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

# Make file_path relative to project root (strip project root prefix)
PROJECT_ROOT=$(dirname "$(dirname "$DB_PATH")")
REL_PATH="${FILE_PATH#$PROJECT_ROOT/}"

# Query for concept summary of the target file
SUMMARY=$(sqlite3 "$DB_PATH" "
    SELECT json_extract(n.data, '$.summary')
    FROM nodes n
    JOIN edges e ON e.from_id = n.id
    JOIN nodes target ON e.to_id = target.id
    WHERE n.type = 'concept'
      AND e.type = 'describes'
      AND target.path = '$REL_PATH'
    LIMIT 1;
" 2>/dev/null || echo "")

if [ -n "$SUMMARY" ]; then
    echo "[Codemesh] Previous agent summary for ${REL_PATH}: ${SUMMARY}"
fi

# Query for workflows involving this file
WORKFLOWS=$(sqlite3 "$DB_PATH" "
    SELECT n.name || ': ' || json_extract(n.data, '$.description')
    FROM nodes n
    WHERE n.type = 'workflow'
      AND json_extract(n.data, '$.fileSequence') LIKE '%${REL_PATH}%';
" 2>/dev/null || echo "")

if [ -n "$WORKFLOWS" ]; then
    echo "[Codemesh] Known workflows involving ${REL_PATH}:"
    echo "$WORKFLOWS" | while IFS= read -r line; do
        [ -n "$line" ] && echo "  - $line"
    done
fi

exit 0
