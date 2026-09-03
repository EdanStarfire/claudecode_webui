#!/usr/bin/env bash
#
# find-port: Find processes listening on a given port.
#
# Usage: find-port.sh <port>
#
# Exit codes:
#   0 - Process(es) found on port
#   1 - Port is free (nothing listening)
#   2 - Invalid arguments
#   3 - Tool not available (lsof/ss not found)
#
# Output: structured KEY=VALUE pairs on stdout.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: find-port.sh <port>" >&2
    exit 2
fi

PORT="$1"

if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
    echo "Error: port must be a number" >&2
    exit 2
fi

emit() {
    echo "$1=$2"
}

emit "PORT" "$PORT"

# Try lsof first (available on macOS and most Linux)
if command -v lsof >/dev/null 2>&1; then
    OUTPUT=$(lsof -i :"$PORT" -sTCP:LISTEN -P -n 2>/dev/null || true)

    if [[ -z "$OUTPUT" ]]; then
        emit "PROC_STATUS" "free"
        emit "PROCESS_COUNT" "0"
        exit 1
    fi

    # Parse lsof output (skip header line)
    COUNT=0
    PIDS=""
    DETAILS=""
    while IFS= read -r line; do
        # Skip header
        [[ "$line" == COMMAND* ]] && continue

        PID=$(echo "$line" | awk '{print $2}')
        CMD=$(echo "$line" | awk '{print $1}')
        USER=$(echo "$line" | awk '{print $3}')

        # Deduplicate PIDs (lsof can show multiple lines per process)
        if [[ ! " $PIDS " =~ " $PID " ]]; then
            COUNT=$((COUNT + 1))
            PIDS="$PIDS $PID"
            if [[ -n "$DETAILS" ]]; then
                DETAILS="$DETAILS"$'\n'"PID=$PID CMD=$CMD USER=$USER"
            else
                DETAILS="PID=$PID CMD=$CMD USER=$USER"
            fi
        fi
    done <<< "$OUTPUT"

    emit "PROC_STATUS" "in_use"
    emit "PROCESS_COUNT" "$COUNT"
    emit "PROCESSES" "$DETAILS"
    exit 0

# Fallback to ss (common on Linux when lsof isn't installed)
elif command -v ss >/dev/null 2>&1; then
    OUTPUT=$(ss -tlnp "sport = :$PORT" 2>/dev/null || true)

    # ss always outputs a header; check if there's more than that
    LINE_COUNT=$(echo "$OUTPUT" | wc -l)
    if [[ "$LINE_COUNT" -le 1 ]]; then
        emit "PROC_STATUS" "free"
        emit "PROCESS_COUNT" "0"
        exit 1
    fi

    COUNT=0
    DETAILS=""
    while IFS= read -r line; do
        [[ "$line" == State* ]] && continue
        [[ -z "$line" ]] && continue

        # Extract pid and process name from the users: column
        if [[ "$line" =~ pid=([0-9]+) ]]; then
            PID="${BASH_REMATCH[1]}"
        else
            continue
        fi

        CMD="unknown"
        if [[ "$line" =~ \"([^\"]+)\" ]]; then
            CMD="${BASH_REMATCH[1]}"
        fi

        COUNT=$((COUNT + 1))
        if [[ -n "$DETAILS" ]]; then
            DETAILS="$DETAILS"$'\n'"PID=$PID CMD=$CMD"
        else
            DETAILS="PID=$PID CMD=$CMD"
        fi
    done <<< "$OUTPUT"

    emit "PROC_STATUS" "in_use"
    emit "PROCESS_COUNT" "$COUNT"
    emit "PROCESSES" "$DETAILS"
    exit 0

else
    echo "Error: neither lsof nor ss found" >&2
    emit "PROC_STATUS" "error"
    emit "ERROR" "no_tool"
    exit 3
fi
