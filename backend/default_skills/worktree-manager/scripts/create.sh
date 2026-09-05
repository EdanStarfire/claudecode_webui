#!/usr/bin/env bash
#
# worktree-create: Create an isolated worktree for an issue.
#
# Usage: create.sh <issue_number> [--prefix <prefix>] [--branch <default-branch>]
#
# Exit codes:
#   0 - Worktree created successfully
#   1 - Worktree already exists
#   2 - Invalid arguments
#   3 - Fetch failed
#   4 - Worktree creation failed (branch exists, git error, etc.)
#
# Output: structured KEY=VALUE pairs on stdout.

set -euo pipefail

# --- Argument parsing ---

ISSUE=""
PREFIX="feat"
BRANCH_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)
            PREFIX="$2"
            shift 2
            ;;
        --branch)
            BRANCH_OVERRIDE="$2"
            shift 2
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
    echo "Usage: create.sh <issue_number> [--prefix <prefix>] [--branch <default-branch>]" >&2
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
BRANCH_NAME="$PREFIX/issue-$ISSUE"

emit "ISSUE" "$ISSUE"
emit "WORKTREE_PATH" "$WORKTREE_PATH"
emit "BRANCH_NAME" "$BRANCH_NAME"

# --- Step 1: Check if worktree already exists ---

if git worktree list 2>/dev/null | grep -q "$WORKTREE_PATH"; then
    emit "CREATE_STATUS" "error"
    emit "ERROR_CODE" "already_exists"
    EXISTING_BRANCH=$(git worktree list 2>/dev/null | grep "$WORKTREE_PATH" | awk '{print $3}' | tr -d '[]')
    emit "EXISTING_BRANCH" "$EXISTING_BRANCH"
    exit 1
fi

# --- Step 2: Detect default branch ---

if [[ -n "$BRANCH_OVERRIDE" ]]; then
    DEFAULT_BRANCH="$BRANCH_OVERRIDE"
else
    if ref=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null); then
        DEFAULT_BRANCH="${ref#origin/}"
    else
        echo "origin/HEAD not set, querying remote..." >&2
        if line=$(git remote show origin 2>/dev/null | grep 'HEAD branch:'); then
            DEFAULT_BRANCH="${line##*: }"
        else
            emit "CREATE_STATUS" "error"
            emit "ERROR_CODE" "no_default_branch"
            emit "DETAILS" "Could not determine default branch. Use --branch flag."
            exit 2
        fi
    fi
fi

emit "DEFAULT_BRANCH" "$DEFAULT_BRANCH"

# --- Step 3: Fetch latest ---

echo "Fetching origin/$DEFAULT_BRANCH..." >&2
if ! git fetch origin "$DEFAULT_BRANCH" >&2 2>&1; then
    emit "CREATE_STATUS" "error"
    emit "ERROR_CODE" "fetch_failed"
    emit "DETAILS" "git fetch origin $DEFAULT_BRANCH failed."
    exit 3
fi

# --- Step 4: Ensure worktrees directory exists ---

mkdir -p worktrees

# --- Step 5: Create worktree ---

echo "Creating worktree at $WORKTREE_PATH on branch $BRANCH_NAME..." >&2
if ! git worktree add -b "$BRANCH_NAME" "$WORKTREE_PATH" "origin/$DEFAULT_BRANCH" >&2 2>&1; then
    emit "CREATE_STATUS" "error"
    emit "ERROR_CODE" "create_failed"
    # Check if the branch already exists (common cause)
    if git branch --list "$BRANCH_NAME" 2>/dev/null | grep -q "$BRANCH_NAME"; then
        emit "DETAILS" "Branch $BRANCH_NAME already exists. Remove it first or use a different prefix."
    else
        emit "DETAILS" "git worktree add failed."
    fi
    exit 4
fi

# --- Step 6: Verify ---

if [[ ! -d "$WORKTREE_PATH" ]]; then
    emit "CREATE_STATUS" "error"
    emit "ERROR_CODE" "create_failed"
    emit "DETAILS" "Worktree directory was not created."
    exit 4
fi

BASE_COMMIT=$(cd "$WORKTREE_PATH" && git log -1 --oneline 2>/dev/null || echo "unknown")

emit "CREATE_STATUS" "success"
emit "BASE_COMMIT" "$BASE_COMMIT"
echo "Worktree created." >&2
