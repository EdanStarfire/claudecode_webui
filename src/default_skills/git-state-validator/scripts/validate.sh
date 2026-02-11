#!/usr/bin/env bash
#
# git-state-validator: Collect git repository state into structured output.
#
# Exit codes:
#   0 - Clean state (no issues)
#   1 - Dirty working directory (uncommitted changes, no conflicts)
#   2 - Merge conflicts detected
#   3 - Not a git repository
#
# Output: structured KEY=VALUE pairs on stdout.
# Informational messages go to stderr.

set -euo pipefail

# --- Argument parsing ---

DO_FETCH=false
DO_HEALTH=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fetch)
            DO_FETCH=true
            shift
            ;;
        --health)
            DO_HEALTH=true
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 3
            ;;
    esac
done

# --- Helper: output a key=value pair ---

emit() {
    echo "$1=$2"
}

# --- Step 1: Check if this is a git repository ---

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    emit "GIT_STATE_IS_REPO" "false"
    exit 3
fi

emit "GIT_STATE_IS_REPO" "true"

# --- Step 2: Branch info ---

BRANCH=$(git branch --show-current 2>/dev/null || echo "")
if [[ -n "$BRANCH" ]]; then
    emit "GIT_STATE_BRANCH" "$BRANCH"
    emit "GIT_STATE_DETACHED" "false"
else
    COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    emit "GIT_STATE_BRANCH" "$COMMIT"
    emit "GIT_STATE_DETACHED" "true"
fi

# --- Step 3: Optional fetch ---

if [[ "$DO_FETCH" == "true" ]]; then
    echo "Fetching from origin..." >&2
    if ! git fetch origin >&2 2>&1; then
        echo "Warning: fetch failed, using cached remote-tracking refs." >&2
    fi
fi

# --- Step 4: Working directory status ---

PORCELAIN=$(git status --porcelain 2>/dev/null || echo "")

STAGED=0
UNSTAGED=0
UNTRACKED=0
CONFLICTS=0
CONFLICTED_FILES=""

if [[ -n "$PORCELAIN" ]]; then
    while IFS= read -r line; do
        X="${line:0:1}"
        Y="${line:1:1}"
        FILE="${line:3}"

        # Conflict markers: UU, AA, DD, AU, UA, DU, UD
        if [[ "$X" == "U" || "$Y" == "U" || ("$X" == "A" && "$Y" == "A") || ("$X" == "D" && "$Y" == "D") ]]; then
            CONFLICTS=$((CONFLICTS + 1))
            if [[ -n "$CONFLICTED_FILES" ]]; then
                CONFLICTED_FILES="$CONFLICTED_FILES"$'\n'"$FILE"
            else
                CONFLICTED_FILES="$FILE"
            fi
        elif [[ "$X" == "?" ]]; then
            UNTRACKED=$((UNTRACKED + 1))
        else
            # Staged changes (index column)
            if [[ "$X" != " " && "$X" != "?" ]]; then
                STAGED=$((STAGED + 1))
            fi
            # Unstaged changes (worktree column)
            if [[ "$Y" != " " && "$Y" != "?" ]]; then
                UNSTAGED=$((UNSTAGED + 1))
            fi
        fi
    done <<< "$PORCELAIN"
fi

IS_CLEAN="true"
if [[ $STAGED -gt 0 || $UNSTAGED -gt 0 || $CONFLICTS -gt 0 ]]; then
    IS_CLEAN="false"
fi

emit "GIT_STATE_CLEAN" "$IS_CLEAN"
emit "GIT_STATE_STAGED" "$STAGED"
emit "GIT_STATE_UNSTAGED" "$UNSTAGED"
emit "GIT_STATE_UNTRACKED" "$UNTRACKED"
emit "GIT_STATE_CONFLICTS" "$CONFLICTS"

if [[ $CONFLICTS -gt 0 ]]; then
    emit "GIT_STATE_CONFLICTED_FILES" "$CONFLICTED_FILES"
fi

# --- Step 5: Remote sync status ---

AHEAD=0
BEHIND=0

if [[ -n "$BRANCH" ]]; then
    UPSTREAM=$(git rev-parse --abbrev-ref "@{upstream}" 2>/dev/null || echo "")
    if [[ -n "$UPSTREAM" ]]; then
        AHEAD=$(git rev-list --count "$UPSTREAM..HEAD" 2>/dev/null || echo "0")
        BEHIND=$(git rev-list --count "HEAD..$UPSTREAM" 2>/dev/null || echo "0")
    fi
fi

emit "GIT_STATE_AHEAD" "$AHEAD"
emit "GIT_STATE_BEHIND" "$BEHIND"

# --- Step 6: File list (always last, may be multi-line) ---

if [[ -n "$PORCELAIN" ]]; then
    emit "GIT_STATE_FILES" "$PORCELAIN"
fi

# --- Step 7: Optional health check ---

if [[ "$DO_HEALTH" == "true" ]]; then
    echo "Running repository health check..." >&2
    if git fsck --full >&2 2>&1; then
        emit "GIT_STATE_FSCK" "ok"
    else
        emit "GIT_STATE_FSCK" "errors"
    fi

    OBJECTS_OUTPUT=$(git count-objects -v 2>/dev/null || echo "")
    if [[ -n "$OBJECTS_OUTPUT" ]]; then
        COUNT=$(echo "$OBJECTS_OUTPUT" | grep '^count:' | awk '{print $2}')
        PACKS=$(echo "$OBJECTS_OUTPUT" | grep '^packs:' | awk '{print $2}')
        SIZE_PACK=$(echo "$OBJECTS_OUTPUT" | grep '^size-pack:' | awk '{print $2}')
        emit "GIT_STATE_OBJECTS" "${COUNT:-0}"
        emit "GIT_STATE_PACKS" "${PACKS:-0}"
        emit "GIT_STATE_SIZE_PACK_KB" "${SIZE_PACK:-0}"
    fi
fi

# --- Determine exit code ---

if [[ $CONFLICTS -gt 0 ]]; then
    exit 2
elif [[ "$IS_CLEAN" == "false" ]]; then
    exit 1
else
    exit 0
fi
