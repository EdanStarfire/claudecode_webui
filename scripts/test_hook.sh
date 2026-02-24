#!/usr/bin/env bash
# test_hook.sh — Development/testing hook script for Claude Code.
#
# Reads hook input JSON from stdin, logs it to a file, and exits cleanly.
# Use this to capture real hook_started/hook_response SystemMessage structures
# from all event types for fixture creation and field name verification.
#
# Usage in .claude/settings.json:
#   {
#     "hooks": {
#       "PreToolUse":  [{ "matcher": "", "hooks": ["bash /path/to/test_hook.sh"] }],
#       "PostToolUse": [{ "matcher": "", "hooks": ["bash /path/to/test_hook.sh"] }],
#       ...
#     }
#   }
#
# Log output goes to /tmp/claude_hook_log.jsonl (one JSON object per line).

LOG_FILE="${CLAUDE_HOOK_LOG:-/tmp/claude_hook_log.jsonl}"

# Read all of stdin (hook input JSON)
INPUT=$(cat)

# Append timestamped entry to log
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
printf '{"ts":"%s","input":%s}\n' "$TIMESTAMP" "$INPUT" >> "$LOG_FILE"

# Exit 0 = hook approved / no-op
exit 0
