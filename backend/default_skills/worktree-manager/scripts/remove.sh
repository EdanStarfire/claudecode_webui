#!/usr/bin/env bash
#
# worktree-remove: Safely remove a worktree for a completed issue.
#
# Usage: remove.sh <issue_number> [--force]
#
# Exit codes:
#   0 - Worktree removed successfully
#   1 - Worktree not found
#   2 - Invalid arguments
#   3 - Uncommitted changes (use --force to override)
#   4 - Removal failed
#
# Output: structured KEY=VALUE pairs on stdout.

set -euo pipefail

# --- Argument parsing ---

ISSUE=""
FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            FORCE=true
            shift
            ;;
        *)
            if [[ -z "$ISSUE" ]]; then
                ISSUE="$1"
                shift
            else
                echo "Unknown argument: $1" >&2
                exit 2
            fi
            ;;
    esac
done

if [[ -z "$ISSUE" ]]; then
    echo "Usage: remove.sh <issue_number> [--force]" >&2
    exit 2
fi

if ! [[ "$ISSUE" =~ ^[0-9]+$ ]]; then
    echo "Error: issue_number must be a number" >&2
    exit 2
fi

emit() {
    echo "$1=$2"
}

WORKTREE_PATH="worktrees/issue-$ISSUE"

emit "ISSUE" "$ISSUE"
emit "WORKTREE_PATH" "$WORKTREE_PATH"

# --- Step 1: Check worktree exists ---

if ! git worktree list 2>/dev/null | grep -q "$WORKTREE_PATH"; then
    emit "REMOVE_STATUS" "error"
    emit "ERROR_CODE" "not_found"
    emit "DETAILS" "Worktree $WORKTREE_PATH does not exist."
    exit 1
fi

# Get branch name before removal
BRANCH_NAME=$(git worktree list 2>/dev/null | grep "$WORKTREE_PATH" | awk '{print $3}' | tr -d '[]')
emit "BRANCH_NAME" "$BRANCH_NAME"

# --- Step 2: Check for uncommitted changes ---

if [[ -d "$WORKTREE_PATH" ]]; then
    DIRTY=$(cd "$WORKTREE_PATH" && git status --short 2>/dev/null || echo "")

    if [[ -n "$DIRTY" && "$FORCE" == "false" ]]; then
        emit "REMOVE_STATUS" "error"
        emit "ERROR_CODE" "uncommitted_changes"
        emit "DETAILS" "$DIRTY"
        exit 3
    fi

    if [[ -n "$DIRTY" ]]; then
        echo "Warning: discarding uncommitted changes (--force)." >&2
    fi
fi

# --- Step 3: Remove worktree ---

echo "Removing worktree $WORKTREE_PATH..." >&2
if [[ "$FORCE" == "true" ]]; then
    if ! git worktree remove --force "$WORKTREE_PATH" >&2 2>&1; then
        emit "REMOVE_STATUS" "error"
        emit "ERROR_CODE" "remove_failed"
        emit "DETAILS" "git worktree remove --force failed."
        exit 4
    fi
else
    if ! git worktree remove "$WORKTREE_PATH" >&2 2>&1; then
        emit "REMOVE_STATUS" "error"
        emit "ERROR_CODE" "remove_failed"
        emit "DETAILS" "git worktree remove failed. Try --force if the worktree is locked."
        exit 4
    fi
fi

# --- Step 4: Prune stale references ---

git worktree prune >&2 2>&1 || true

# --- Step 5: Clean up empty worktrees directory ---

if [[ -d "worktrees" ]] && [ -z "$(ls -A worktrees/ 2>/dev/null)" ]; then
    rmdir worktrees/ 2>/dev/null || true
    echo "Removed empty worktrees/ directory." >&2
fi

# --- Step 6: Verify ---

if git worktree list 2>/dev/null | grep -q "$WORKTREE_PATH"; then
    emit "REMOVE_STATUS" "error"
    emit "ERROR_CODE" "remove_failed"
    emit "DETAILS" "Worktree still appears in git worktree list."
    exit 4
fi

emit "REMOVE_STATUS" "success"
echo "Worktree removed." >&2
