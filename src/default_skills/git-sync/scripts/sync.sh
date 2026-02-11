#!/usr/bin/env bash
#
# git-sync: Synchronize local default branch with remote.
#
# Exit codes:
#   0 - Success
#   1 - Uncommitted changes on default branch
#   2 - Cannot determine default branch
#   3 - Fast-forward failed (local has diverged from remote)
#   4 - Fetch failed (network/remote unreachable)
#
# Output: structured key=value pairs on stdout.
# Informational messages go to stderr.

set -euo pipefail

# --- Argument parsing ---

BRANCH_OVERRIDE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)
            BRANCH_OVERRIDE="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

# --- Helper: output a key=value pair ---

emit() {
    echo "$1=$2"
}

# --- Step 1: Detect default branch ---

if [[ -n "$BRANCH_OVERRIDE" ]]; then
    DEFAULT_BRANCH="$BRANCH_OVERRIDE"
    echo "Using branch override: $DEFAULT_BRANCH" >&2
else
    # Try symbolic-ref first (fast, local-only)
    if ref=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null); then
        DEFAULT_BRANCH="${ref#origin/}"
    else
        # Fallback: query remote (slower, requires network)
        echo "origin/HEAD not set, querying remote..." >&2
        if line=$(git remote show origin 2>/dev/null | grep 'HEAD branch:'); then
            DEFAULT_BRANCH="${line##*: }"
        else
            emit "GIT_SYNC_STATUS" "error"
            emit "ERROR_CODE" "no_default_branch"
            emit "DETAILS" "Could not determine default branch. origin/HEAD is not set and remote query failed."
            exit 2
        fi
    fi
fi

emit "DEFAULT_BRANCH" "$DEFAULT_BRANCH"

# --- Step 2: Record current branch ---

ORIGINAL_BRANCH=$(git branch --show-current 2>/dev/null || echo "")
if [[ -z "$ORIGINAL_BRANCH" ]]; then
    ORIGINAL_BRANCH="(detached HEAD)"
fi
emit "ORIGINAL_BRANCH" "$ORIGINAL_BRANCH"

# --- Step 3: Switch to default branch if needed ---

if [[ "$ORIGINAL_BRANCH" != "$DEFAULT_BRANCH" ]]; then
    echo "Switching from $ORIGINAL_BRANCH to $DEFAULT_BRANCH..." >&2
    git checkout "$DEFAULT_BRANCH" --quiet >&2 2>&1
fi

# --- Step 4: Check for uncommitted changes ---

DIRTY=$(git status --short)
if [[ -n "$DIRTY" ]]; then
    emit "GIT_SYNC_STATUS" "error"
    emit "ERROR_CODE" "uncommitted_changes"
    emit "DETAILS" "$DIRTY"
    # Switch back before exiting
    if [[ "$ORIGINAL_BRANCH" != "$DEFAULT_BRANCH" && "$ORIGINAL_BRANCH" != "(detached HEAD)" ]]; then
        git checkout "$ORIGINAL_BRANCH" --quiet >&2 2>&1
    fi
    exit 1
fi

# --- Step 5: Fetch from remote ---

echo "Fetching from origin..." >&2
if ! git fetch origin >&2 2>&1; then
    emit "GIT_SYNC_STATUS" "error"
    emit "ERROR_CODE" "fetch_failed"
    emit "DETAILS" "git fetch origin failed. Check network connectivity."
    if [[ "$ORIGINAL_BRANCH" != "$DEFAULT_BRANCH" && "$ORIGINAL_BRANCH" != "(detached HEAD)" ]]; then
        git checkout "$ORIGINAL_BRANCH" --quiet >&2 2>&1
    fi
    exit 4
fi

# --- Step 6: Check how far behind ---

COMMITS_BEHIND=$(git rev-list --count "HEAD..origin/$DEFAULT_BRANCH" 2>/dev/null || echo "0")
COMMITS_AHEAD=$(git rev-list --count "origin/$DEFAULT_BRANCH..HEAD" 2>/dev/null || echo "0")

emit "COMMITS_BEHIND" "$COMMITS_BEHIND"
emit "COMMITS_AHEAD" "$COMMITS_AHEAD"

if [[ "$COMMITS_BEHIND" == "0" && "$COMMITS_AHEAD" == "0" ]]; then
    emit "GIT_SYNC_STATUS" "success"
    emit "COMMITS_PULLED" "0"
    LATEST=$(git log -1 --oneline 2>/dev/null || echo "(empty)")
    emit "LATEST_COMMIT" "$LATEST"
    echo "Already up to date." >&2
    if [[ "$ORIGINAL_BRANCH" != "$DEFAULT_BRANCH" && "$ORIGINAL_BRANCH" != "(detached HEAD)" ]]; then
        git checkout "$ORIGINAL_BRANCH" --quiet >&2 2>&1
    fi
    exit 0
fi

# --- Step 7: Pull with fast-forward only ---

echo "Pulling $COMMITS_BEHIND commit(s)..." >&2
if ! git pull origin "$DEFAULT_BRANCH" --ff-only >&2 2>&1; then
    # Collect divergence info for the LLM
    LOCAL_COMMITS=$(git log "origin/$DEFAULT_BRANCH..HEAD" --oneline 2>/dev/null || echo "")
    REMOTE_COMMITS=$(git log "HEAD..origin/$DEFAULT_BRANCH" --oneline 2>/dev/null || echo "")
    emit "GIT_SYNC_STATUS" "error"
    emit "ERROR_CODE" "ff_failed"
    emit "LOCAL_COMMITS" "$LOCAL_COMMITS"
    emit "REMOTE_COMMITS" "$REMOTE_COMMITS"
    if [[ "$ORIGINAL_BRANCH" != "$DEFAULT_BRANCH" && "$ORIGINAL_BRANCH" != "(detached HEAD)" ]]; then
        git checkout "$ORIGINAL_BRANCH" --quiet >&2 2>&1
    fi
    exit 3
fi

# --- Step 8: Verify and report ---

LATEST=$(git log -1 --oneline 2>/dev/null || echo "(empty)")
REMOTE_LATEST=$(git log "origin/$DEFAULT_BRANCH" -1 --oneline 2>/dev/null || echo "(empty)")

emit "GIT_SYNC_STATUS" "success"
emit "COMMITS_PULLED" "$COMMITS_BEHIND"
emit "LATEST_COMMIT" "$LATEST"

if [[ "$LATEST" != "$REMOTE_LATEST" ]]; then
    echo "Warning: local and remote HEAD differ after pull." >&2
    emit "SYNC_WARNING" "head_mismatch"
fi

# --- Step 9: Return to original branch ---

if [[ "$ORIGINAL_BRANCH" != "$DEFAULT_BRANCH" && "$ORIGINAL_BRANCH" != "(detached HEAD)" ]]; then
    echo "Returning to $ORIGINAL_BRANCH..." >&2
    git checkout "$ORIGINAL_BRANCH" --quiet >&2 2>&1
fi

echo "Sync complete." >&2
