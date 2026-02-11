#!/usr/bin/env bash
#
# worktree-list: List all worktrees with structured status info.
#
# Usage: list.sh
#
# Exit codes:
#   0 - Success
#
# Output: structured KEY=VALUE pairs on stdout, one block per worktree.
# Worktree blocks are separated by a "---" delimiter line.

set -euo pipefail

emit() {
    echo "$1=$2"
}

WORKTREE_OUTPUT=$(git worktree list 2>/dev/null || echo "")

if [[ -z "$WORKTREE_OUTPUT" ]]; then
    emit "WORKTREE_COUNT" "0"
    exit 0
fi

TOTAL=0
ISSUE_COUNT=0
FIRST=true

while IFS= read -r line; do
    [[ -z "$line" ]] && continue

    PATH_COL=$(echo "$line" | awk '{print $1}')
    COMMIT_COL=$(echo "$line" | awk '{print $2}')
    BRANCH_COL=$(echo "$line" | awk '{print $3}' | tr -d '[]')

    TOTAL=$((TOTAL + 1))

    # Skip the main repo entry (not a worktree subdirectory)
    if ! echo "$PATH_COL" | grep -q "/worktrees/"; then
        continue
    fi

    ISSUE_COUNT=$((ISSUE_COUNT + 1))

    if [[ "$FIRST" == "true" ]]; then
        FIRST=false
    else
        echo "---"
    fi

    emit "WORKTREE_PATH" "$PATH_COL"
    emit "WORKTREE_BRANCH" "$BRANCH_COL"
    emit "WORKTREE_COMMIT" "$COMMIT_COL"

    # Get status within the worktree
    if [[ -d "$PATH_COL" ]]; then
        STATUS=$(cd "$PATH_COL" && git status --short 2>/dev/null || echo "")
        LAST_COMMIT=$(cd "$PATH_COL" && git log -1 --oneline 2>/dev/null || echo "unknown")
        AHEAD=$(cd "$PATH_COL" && git rev-list --count "@{upstream}..HEAD" 2>/dev/null || echo "0")

        if [[ -z "$STATUS" ]]; then
            emit "WORKTREE_CLEAN" "true"
            emit "WORKTREE_MODIFIED" "0"
        else
            emit "WORKTREE_CLEAN" "false"
            MODIFIED=$(echo "$STATUS" | wc -l | tr -d ' ')
            emit "WORKTREE_MODIFIED" "$MODIFIED"
            emit "WORKTREE_FILES" "$STATUS"
        fi

        emit "WORKTREE_LAST_COMMIT" "$LAST_COMMIT"
        emit "WORKTREE_AHEAD" "$AHEAD"
    else
        emit "WORKTREE_CLEAN" "unknown"
        emit "WORKTREE_LAST_COMMIT" "unknown"
        emit "WORKTREE_AHEAD" "0"
    fi
done <<< "$WORKTREE_OUTPUT"

# Summary at the end
echo "---"
emit "WORKTREE_COUNT" "$ISSUE_COUNT"
