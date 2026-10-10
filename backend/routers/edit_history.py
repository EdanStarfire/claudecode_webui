"""Edit history endpoint: /api/sessions/{session_id}/edit-history"""

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions

from ..models.messages import LegacyMessageFormatError

# Heuristic for classifying Bash commands as likely file-modifying.
# Conservative — false positives (treating non-modifying as modifying)
# are preferred over false negatives (hiding modifying calls).
_MODIFYING_BASH_RE = re.compile(
    r"(?:^|[\s|;&])("
    r"sed\s+-i|"
    r"awk\s+-i|"
    r"perl\s+-i|"
    r"cp\s|mv\s|rm\s|mkdir\s|touch\s|"
    r"tee\s|"
    r"chmod\s|chown\s|"
    r"git\s+(?:add|commit|checkout|reset|rm|mv|merge|rebase|stash|apply|am|cherry-pick|revert|clean)|"
    r"npm\s+(?:install|uninstall|update|ci)|"
    r"pip\s+install|uv\s+(?:add|sync|lock)|"
    r"make\b|cmake\b|cargo\s+(?:build|add|remove)|"
    r"dd\s|mkfs\s"
    r")|"
    r"(?<!>)>{1,2}(?!>)\s*(?!/dev/null(?:[\s;&|)]|$))(?!&)\S"  # output redirection (excl. /dev/null, fd dup)
)


def _classify_bash(command: str) -> bool:
    """Return True if the bash command is likely to modify files."""
    if not command:
        return False
    return bool(_MODIFYING_BASH_RE.search(command))


_TERMINAL_SUCCESS = {"completed"}
_TERMINAL_FAILURE = {"failed", "denied", "interrupted"}


def _resolve_status(
    tid: str | None, tool_call_statuses: dict[str, str], results: dict[str, bool]
) -> tuple[str, bool | None]:
    """Resolve (status, succeeded) for a tool_use_id.

    Prefers the canonical `tool_call` status; falls back to legacy
    `tool_result`-presence inference when no `tool_call` record exists.
    """
    status = tool_call_statuses.get(tid)
    if status is not None:
        if status in _TERMINAL_SUCCESS:
            succeeded: bool | None = True
        elif status in _TERMINAL_FAILURE:
            succeeded = False
        else:
            succeeded = None
        return status, succeeded

    succeeded = results.get(tid)
    if succeeded is None:
        return "pending", None
    return ("completed" if succeeded else "failed"), succeeded


def _build_entry(block: dict, ts: float | None) -> dict:
    """Build an edit-history entry from a tool_use block."""
    name = block.get("name")
    inp = block.get("input") or {}
    entry: dict = {
        "tool_use_id": block.get("id"),
        "tool_name": name,
        "timestamp": ts,
        "input": inp,
    }
    if name == "Edit":
        entry["file_path"] = inp.get("file_path")
    elif name == "Write":
        entry["file_path"] = inp.get("file_path")
        entry["line_count"] = len((inp.get("content") or "").splitlines())
    elif name == "Bash":
        entry["command"] = inp.get("command", "")
        entry["likely_modifying"] = _classify_bash(entry["command"])
    return entry


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sessions/{session_id}/edit-history")
    @handle_exceptions("get session edit history")
    async def get_edit_history(session_id: str):
        """Return chronological list of file-modifying tool calls.

        Filters Edit, Write, and Bash tool_use blocks from messages.jsonl.
        Diffs are NOT computed here — frontend reconstructs them from
        old_string/new_string. Bash entries carry the command only.
        """
        ctx = await webui.service.get_session_diff_context(session_id)
        if not ctx.get("exists"):
            raise HTTPException(status_code=404, detail="Session not found")

        messages_path = await webui.service.get_session_messages_path(session_id)
        if not messages_path or not Path(messages_path).exists():
            return {"entries": [], "tool_count": 0}

        entries = []
        # Map tool_use_id -> succeeded flag from tool_result blocks
        results: dict[str, bool] = {}
        # Map tool_use_id -> canonical ToolState status from tool_call records
        tool_call_statuses: dict[str, str] = {}

        with open(messages_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                stored_type = msg.get("_type")
                if stored_type:
                    raise LegacyMessageFormatError(session_id, stored_type)

                msg_type = msg.get("type")
                ts = msg.get("timestamp")

                if msg_type == "assistant":
                    # Fallback: legacy prepare_for_storage() shape used by mock SDK
                    metadata = msg.get("metadata") or {}
                    for tu in metadata.get("tool_uses") or []:
                        if not isinstance(tu, dict):
                            continue
                        if tu.get("name") not in ("Edit", "Write", "Bash"):
                            continue
                        entries.append(_build_entry(tu, ts))

                elif msg_type == "user":
                    metadata = msg.get("metadata") or {}
                    for tr in metadata.get("tool_results") or []:
                        if not isinstance(tr, dict):
                            continue
                        tid = tr.get("tool_use_id")
                        if tid:
                            results[tid] = not tr.get("is_error", False)

                elif msg_type == "tool_call":
                    tid = msg.get("tool_use_id")
                    status = msg.get("status")
                    if tid and status:
                        tool_call_statuses[tid] = status

        # Resolve terminal status + success flag per entry
        for entry in entries:
            tid = entry.get("tool_use_id")
            entry["status"], entry["succeeded"] = _resolve_status(
                tid, tool_call_statuses, results
            )

        return {
            "entries": entries,
            "tool_count": len(entries),
        }

    return router
