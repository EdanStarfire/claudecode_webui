#!/usr/bin/env bash
#
# check-pid: Check if a process is running and report its info.
#
# Usage: check-pid.sh <PID>
#
# Exit codes:
#   0 - Process is running
#   1 - Process is not running
#   2 - Invalid arguments
#
# Output: structured KEY=VALUE pairs on stdout.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: check-pid.sh <PID>" >&2
    exit 2
fi

PID="$1"

if ! [[ "$PID" =~ ^[0-9]+$ ]]; then
    echo "Error: PID must be a number" >&2
    exit 2
fi

emit() {
    echo "$1=$2"
}

emit "TARGET_PID" "$PID"

if ! ps -p "$PID" >/dev/null 2>&1; then
    emit "PROC_STATUS" "not_running"
    exit 1
fi

# Process is running — collect info
CMD=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
USER=$(ps -p "$PID" -o user= 2>/dev/null || echo "unknown")
CPU=$(ps -p "$PID" -o %cpu= 2>/dev/null || echo "0")
MEM=$(ps -p "$PID" -o %mem= 2>/dev/null || echo "0")
ELAPSED=$(ps -p "$PID" -o etime= 2>/dev/null || echo "unknown")
FULL_CMD=$(ps -p "$PID" -o args= 2>/dev/null || echo "unknown")

emit "PROC_STATUS" "running"
emit "PROCESS_CMD" "$CMD"
emit "PROCESS_USER" "$USER"
emit "PROCESS_CPU" "$CPU"
emit "PROCESS_MEM" "$MEM"
emit "PROCESS_ELAPSED" "$ELAPSED"
emit "PROCESS_FULL_CMD" "$FULL_CMD"
