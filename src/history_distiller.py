"""
History Distiller - Converts raw messages.jsonl into human-readable markdown.

Extracts user messages, agent responses, inbound/outbound comms, and select
system messages into a greppable .md file for archived sessions.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# System message subtypes to exclude from distillation
_EXCLUDED_SYSTEM_SUBTYPES = frozenset({
    "task_started",
    "task_progress",
    "task_notification",
    "status_update",
    "local_command_response",
})


def _format_timestamp(raw_ts) -> str:
    """Convert a raw timestamp (unix float/int or ISO string) to ISO format."""
    if raw_ts is None:
        return "unknown"
    if isinstance(raw_ts, (int, float)):
        try:
            return datetime.fromtimestamp(raw_ts, tz=UTC).isoformat()
        except (ValueError, OSError):
            return str(raw_ts)
    return str(raw_ts)


def _extract_content(msg: dict) -> str:
    """Extract displayable content from a message dict."""
    content = msg.get("content", "")
    if isinstance(content, list):
        # Handle structured content blocks (e.g., [{type: "text", text: "..."}])
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content) if content else ""


async def distill_session_history(
    messages_jsonl_path: Path,
    output_path: Path,
    session_id: str,
    archive_timestamp: str,
) -> bool:
    """
    Distill raw messages.jsonl into a human-readable markdown summary.

    Args:
        messages_jsonl_path: Path to the source messages.jsonl file
        output_path: Path to write the output markdown file
        session_id: Session identifier for the header
        archive_timestamp: ISO timestamp of the archive event

    Returns:
        True on success, False on failure
    """
    try:
        if not messages_jsonl_path.exists():
            logger.warning(f"Messages file not found for distillation: {messages_jsonl_path}")
            return False

        entries = []
        stats = {
            "total": 0,
            "user": 0,
            "agent": 0,
            "system": 0,
            "comm_inbound": 0,
            "comm_outbound": 0,
        }
        first_ts = None
        last_ts = None

        with open(messages_jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")
                metadata = msg.get("metadata", {}) or {}
                timestamp = msg.get("timestamp")

                # Track first/last timestamps for duration
                if timestamp is not None:
                    ts_val = timestamp
                    if first_ts is None or ts_val < first_ts:
                        first_ts = ts_val
                    if last_ts is None or ts_val > last_ts:
                        last_ts = ts_val

                formatted_ts = _format_timestamp(timestamp)
                content = _extract_content(msg)

                if msg_type == "user":
                    if metadata.get("comm"):
                        # Inbound comm
                        comm_data = metadata["comm"]
                        sender = comm_data.get("from_display_name", "unknown")
                        entries.append(
                            f"## {formatted_ts} - Comm (Inbound from {sender})\n{content}\n"
                        )
                        stats["comm_inbound"] += 1
                        stats["total"] += 1
                    else:
                        entries.append(f"## {formatted_ts} - User\n{content}\n")
                        stats["user"] += 1
                        stats["total"] += 1

                elif msg_type == "assistant":
                    entries.append(f"## {formatted_ts} - Agent\n{content}\n")
                    stats["agent"] += 1
                    stats["total"] += 1

                elif msg_type == "system":
                    subtype = metadata.get("subtype", "")
                    if subtype not in _EXCLUDED_SYSTEM_SUBTYPES:
                        entries.append(f"## {formatted_ts} - System\n{content}\n")
                        stats["system"] += 1
                        stats["total"] += 1

                elif msg_type == "tool_use":
                    tool_name = metadata.get("tool_name", "")
                    if tool_name == "mcp__legion__send_comm":
                        tool_input = metadata.get("tool_input", {}) or {}
                        recipient = tool_input.get("to_minion_name", "unknown")
                        summary = tool_input.get("summary", "")
                        comm_content = tool_input.get("content", "")
                        body = ""
                        if summary:
                            body += f"**Summary:** {summary}\n"
                        if comm_content:
                            body += f"{comm_content}\n"
                        if not body:
                            body = content + "\n"
                        entries.append(
                            f"## {formatted_ts} - Comm (Outbound to {recipient})\n{body}"
                        )
                        stats["comm_outbound"] += 1
                        stats["total"] += 1

        # Calculate duration
        duration_str = "unknown"
        if first_ts is not None and last_ts is not None:
            try:
                duration_secs = float(last_ts) - float(first_ts)
                if duration_secs >= 0:
                    hours = int(duration_secs // 3600)
                    minutes = int((duration_secs % 3600) // 60)
                    duration_str = f"{hours}h {minutes}m"
            except (ValueError, TypeError):
                pass

        total_comms = stats["comm_inbound"] + stats["comm_outbound"]

        # Build output
        lines = [
            f"# Session History - {session_id} - Archived {archive_timestamp}\n",
        ]

        for entry in entries:
            lines.append(entry)

        lines.append("---\n")
        lines.append("# Statistics")
        lines.append(f"- Total messages: {stats['total']}")
        lines.append(f"- User messages: {stats['user']}")
        lines.append(f"- Agent messages: {stats['agent']}")
        lines.append(f"- System messages: {stats['system']}")
        lines.append(f"- Comms: {total_comms}")
        lines.append(f"  - Inbound: {stats['comm_inbound']}")
        lines.append(f"  - Outbound: {stats['comm_outbound']}")
        lines.append(f"- Session duration: {duration_str}")
        lines.append(f"- Archive timestamp: {archive_timestamp}")
        lines.append("- History version: 1.0\n")

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info(
            f"Distilled session history for {session_id}: "
            f"{stats['total']} messages -> {output_path}"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to distill session history for {session_id}: {e}")
        return False
