#!/usr/bin/env bash
#
# kill-pid: Kill a process by PID with graceful→force fallback and verification.
#
# Usage: kill-pid.sh <PID> [--force] [--timeout <seconds>]
#
# Exit codes:
#   0 - Process terminated successfully
#   1 - Process still running after all attempts
#   2 - Invalid arguments
#   3 - Permission denied
#   4 - Process not found (already dead)
#
# Output: structured KEY=VALUE pairs on stdout.

set -euo pipefail

# --- Argument parsing ---

PID=""
FORCE=false
TIMEOUT=5

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            FORCE=true
            shift
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        *)
            if [[ -z "$PID" ]]; then
                PID="$1"
                shift
            else
                echo "Unknown argument: $1" >&2
                exit 2
            fi
            ;;
    esac
done

if [[ -z "$PID" ]]; then
    echo "Usage: kill-pid.sh <PID> [--force] [--timeout <seconds>]" >&2
    exit 2
fi

if ! [[ "$PID" =~ ^[0-9]+$ ]]; then
    echo "Error: PID must be a number" >&2
    exit 2
fi

emit() {
    echo "$1=$2"
}

emit "TARGET_PID" "$PID"

# --- Check if process exists ---

if ! ps -p "$PID" >/dev/null 2>&1; then
    emit "KILL_STATUS" "not_found"
    emit "DETAILS" "Process $PID does not exist"
    exit 4
fi

# Get process info before killing
CMD=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
emit "PROCESS_CMD" "$CMD"

# --- Kill process ---

if [[ "$FORCE" == "true" ]]; then
    echo "Force killing PID $PID..." >&2
    if ! kill -9 "$PID" 2>/tmp/claude/kill_err_$$; then
        if grep -qi "permission denied\|operation not permitted" /tmp/claude/kill_err_$$ 2>/dev/null; then
            emit "KILL_STATUS" "permission_denied"
            emit "DETAILS" "Cannot kill PID $PID - permission denied"
            rm -f /tmp/claude/kill_err_$$
            exit 3
        fi
    fi
    rm -f /tmp/claude/kill_err_$$
else
    # Graceful kill (SIGTERM)
    echo "Sending SIGTERM to PID $PID..." >&2
    if ! kill "$PID" 2>/tmp/claude/kill_err_$$; then
        if grep -qi "permission denied\|operation not permitted" /tmp/claude/kill_err_$$ 2>/dev/null; then
            emit "KILL_STATUS" "permission_denied"
            emit "DETAILS" "Cannot kill PID $PID - permission denied"
            rm -f /tmp/claude/kill_err_$$
            exit 3
        fi
    fi
    rm -f /tmp/claude/kill_err_$$

    # Wait for graceful shutdown
    echo "Waiting ${TIMEOUT}s for graceful shutdown..." >&2
    ELAPSED=0
    while [[ $ELAPSED -lt $TIMEOUT ]]; do
        if ! ps -p "$PID" >/dev/null 2>&1; then
            emit "KILL_STATUS" "terminated"
            emit "KILL_METHOD" "SIGTERM"
            exit 0
        fi
        sleep 1
        ELAPSED=$((ELAPSED + 1))
    done

    # Still running — force kill
    if ps -p "$PID" >/dev/null 2>&1; then
        echo "Process still running, sending SIGKILL..." >&2
        kill -9 "$PID" 2>/dev/null || true
        sleep 1
    fi
fi

# --- Verify termination ---

if ps -p "$PID" >/dev/null 2>&1; then
    emit "KILL_STATUS" "failed"
    emit "DETAILS" "Process $PID still running after SIGKILL"
    exit 1
else
    if [[ "$FORCE" == "true" ]]; then
        emit "KILL_STATUS" "terminated"
        emit "KILL_METHOD" "SIGKILL"
    else
        emit "KILL_STATUS" "terminated"
        emit "KILL_METHOD" "SIGKILL_fallback"
    fi
    exit 0
fi
