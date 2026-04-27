"""AuditWriter: async-batched writer of audit_events rows.

Subscribes to four hook sources:
  1. SessionManager state-change callbacks
  2. DataStorageManager.on_append (message-level events)
  3. CommRouter audit hook (Legion comms)
  4. SessionWatchdogService on_alert callbacks

All hook calls are fire-and-forget from the caller's perspective.
AuditWriter errors are caught and logged; they never propagate to sessions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from .turn_tracker import TurnTracker

if TYPE_CHECKING:
    from .database import AnalyticsDB

logger = logging.getLogger(__name__)

# Flush every 200 ms or when batch reaches 50 rows
_FLUSH_INTERVAL = 0.2
_FLUSH_COUNT = 50

_SUMMARY_MAX = 256


def _truncate(s: str | None, n: int = _SUMMARY_MAX) -> str | None:
    if s and len(s) > n:
        return s[: n - 1] + "…"
    return s


def _compact_json(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return None


class AuditWriter:
    """Collects audit events from multiple sources and batch-inserts them into analytics.db.

    Design goals:
    - Non-blocking: hook methods schedule coroutines; they don't await DB directly.
    - Failure-isolated: exceptions inside any hook are caught and logged.
    - Graceful degradation: if `db` is None the writer is a no-op.
    """

    def __init__(self, db: AnalyticsDB | None) -> None:
        self._db = db
        self._tracker = TurnTracker()
        self._batch: list[tuple] = []
        self._flush_task: asyncio.Task | None = None
        self._running = False
        # (state_name, is_processing) per session — skip identical consecutive events
        self._session_state_cache: dict[str, tuple[str, bool]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start background flush task. Call after the event loop is running."""
        if self._db is None or self._running:
            return
        self._running = True
        self._flush_task = asyncio.ensure_future(self._flush_loop())
        logger.info("AuditWriter started")

    async def stop(self) -> None:
        """Flush remaining rows and stop."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._flush_now()
        logger.info("AuditWriter stopped")

    # ------------------------------------------------------------------
    # Hook: session state changes (from SessionManager)
    # ------------------------------------------------------------------

    async def on_session_state_change(self, session_id: str, new_state: Any, is_processing: bool = False, project_id: str | None = None) -> None:
        """Called by SessionManager when a session changes state."""
        if self._db is None:
            return
        try:
            state_name = new_state.value if hasattr(new_state, "value") else str(new_state)
            current = (state_name, is_processing)
            if self._session_state_cache.get(session_id) == current:
                return  # Nothing changed — skip duplicate
            self._session_state_cache[session_id] = current
            # Close any open turn when session terminates
            if state_name in ("terminated", "error"):
                self._tracker.on_result(session_id)
                self._session_state_cache.pop(session_id, None)
            self._enqueue(
                session_id=session_id,
                project_id=project_id,
                legion_id=None,
                turn_id=None,
                event_type="lifecycle",
                tool_name=None,
                status=state_name,
                summary=_truncate(f"Session state → {state_name}"),
                message_id=None,
                extra={"state": state_name, "is_processing": is_processing},
            )
        except Exception:
            logger.exception("AuditWriter.on_session_state_change error (non-fatal)")

    # ------------------------------------------------------------------
    # Hook: DataStorageManager.on_append (message-level events)
    # ------------------------------------------------------------------

    async def on_message_append(
        self,
        session_id: str,
        project_id: str | None,
        message_data: dict[str, Any],
    ) -> None:
        """Called by DataStorageManager after each message is appended."""
        if self._db is None:
            return
        try:
            await self._process_message(session_id, project_id, message_data)
        except Exception:
            logger.exception("AuditWriter.on_message_append error (non-fatal)")

    async def _process_message(
        self,
        session_id: str,
        project_id: str | None,
        msg: dict[str, Any],
    ) -> None:
        msg_type = msg.get("type", "")
        stored_type = msg.get("_type", "")
        timestamp = msg.get("timestamp") or time.time()
        source_ts = msg.get("source_ts")
        message_id = msg.get("message_id")

        # Track turns
        if msg_type == "user":
            self._tracker.on_user_message(session_id)
        elif msg_type in ("result", "session_end"):
            self._tracker.on_result(session_id)

        turn_id = self._tracker.current_turn_id(session_id)

        # ToolCallUpdate: authoritative source for tool lifecycle events (#1160)
        if stored_type == "ToolCallUpdate":
            data = msg.get("data", {})
            tool_name = data.get("name")
            tool_use_id = data.get("tool_use_id")
            tc_status = data.get("status", "pending")
            tool_input = data.get("input") or {}

            if tc_status == "pending":
                summary = _make_tool_summary(tool_name, tool_input)
                extra = {"tool_name": tool_name, "tool_use_id": tool_use_id}
                self._enqueue_with_ts(
                    timestamp, source_ts, session_id, project_id, None, turn_id,
                    "tool_call", tool_name, "started", summary, message_id, extra,
                )
            elif tc_status == "awaiting_permission":
                summary = _truncate(f"Permission requested: {tool_name}")
                extra = {"tool_name": tool_name, "tool_use_id": tool_use_id}
                self._enqueue_with_ts(
                    timestamp, source_ts, session_id, project_id, None, turn_id,
                    "permission", tool_name, "requested", summary, message_id, extra,
                )
            elif tc_status == "denied":
                summary = _truncate(f"Permission denied: {tool_name}")
                extra = {"tool_name": tool_name, "tool_use_id": tool_use_id}
                self._enqueue_with_ts(
                    timestamp, source_ts, session_id, project_id, None, turn_id,
                    "permission", tool_name, "denied", summary, message_id, extra,
                )
            elif tc_status in ("completed", "failed", "interrupted"):
                is_error = tc_status != "completed"
                audit_status = {"completed": "ok", "failed": "error", "interrupted": "interrupted"}[tc_status]
                summary = _make_tool_result_summary(tool_name, {}, is_error)
                extra = {"tool_name": tool_name, "tool_use_id": tool_use_id}
                self._enqueue_with_ts(
                    timestamp, source_ts, session_id, project_id, None, turn_id,
                    "tool_call", tool_name, audit_status, summary, message_id, extra,
                )
            # running state: skip — covered by started/permission events
            return

        # Map message type to event_type + summary
        if msg_type in ("tool_use",):
            # Legacy type kept for backward compatibility with old JSONL files
            metadata = msg.get("metadata", {})
            tool_name = metadata.get("tool_name") or msg.get("tool_name")
            tool_input = metadata.get("tool_input") or {}
            status = "started"
            summary = _make_tool_summary(tool_name, tool_input)
            extra = {"tool_name": tool_name, "tool_use_id": metadata.get("tool_use_id")}
            self._enqueue_with_ts(
                timestamp, source_ts, session_id, project_id, None, turn_id,
                "tool_call", tool_name, status, summary, message_id, extra,
            )

        elif msg_type in ("tool_result", "tool_error"):
            # Legacy type kept for backward compatibility with old JSONL files
            metadata = msg.get("metadata", {})
            tool_name = metadata.get("tool_name") or msg.get("tool_name")
            is_error = msg_type == "tool_error" or metadata.get("is_error", False)
            status = "error" if is_error else "ok"
            summary = _make_tool_result_summary(tool_name, metadata, is_error)
            extra = {"tool_name": tool_name, "tool_use_id": metadata.get("tool_use_id")}
            self._enqueue_with_ts(
                timestamp, source_ts, session_id, project_id, None, turn_id,
                "tool_call", tool_name, status, summary, message_id, extra,
            )

        elif msg_type == "permission_request":
            metadata = msg.get("metadata", {})
            tool_name = metadata.get("tool_name")
            summary = _truncate(f"Permission requested: {tool_name}")
            extra = {"tool_name": tool_name}
            self._enqueue_with_ts(
                timestamp, source_ts, session_id, project_id, None, turn_id,
                "permission", tool_name, "requested", summary, message_id, extra,
            )

        elif msg_type == "permission_response":
            metadata = msg.get("metadata", {})
            decision = metadata.get("decision", "unknown")
            tool_name = metadata.get("tool_name")
            status = "denied" if decision == "deny" else "allowed"
            summary = _truncate(f"Permission {status}: {tool_name}")
            extra = {"tool_name": tool_name, "decision": decision}
            self._enqueue_with_ts(
                timestamp, source_ts, session_id, project_id, None, turn_id,
                "permission", tool_name, status, summary, message_id, extra,
            )

        elif msg_type == "result":
            metadata = msg.get("metadata", {})
            is_error = metadata.get("is_error", False)
            status = "errored" if is_error else "completed"
            summary = _truncate(f"Turn {status}")
            self._enqueue_with_ts(
                timestamp, source_ts, session_id, project_id, None, turn_id,
                "lifecycle", None, status, summary, message_id, None,
            )

    # ------------------------------------------------------------------
    # Hook: CommRouter (Legion comms)
    # ------------------------------------------------------------------

    async def on_comm(
        self,
        session_id: str,
        project_id: str | None,
        legion_id: str | None,
        comm_data: dict[str, Any],
    ) -> None:
        """Called by CommRouter after persisting each Comm."""
        if self._db is None:
            return
        try:
            timestamp = comm_data.get("timestamp") or time.time()
            comm_type = comm_data.get("comm_type", "")
            from_id = comm_data.get("from_minion_id") or "user"
            to_id = comm_data.get("to_minion_id") or "user"
            from_name = comm_data.get("from_minion_name") or from_id
            to_name = comm_data.get("to_minion_name") or to_id
            summary_text = comm_data.get("summary") or comm_data.get("content", "")[:80]
            summary = _truncate(f"[{comm_type}] {from_name} → {to_name}: {summary_text}")
            extra = {
                "comm_id": comm_data.get("comm_id"),
                "comm_type": comm_type,
                "from": from_id,
                "from_name": from_name,
                "to": to_id,
                "to_name": to_name,
                "comm_summary": summary_text,
            }
            self._enqueue_with_ts(
                timestamp, None, session_id, project_id, legion_id, None,
                "comm", None, "sent", summary, None, extra,
            )
        except Exception:
            logger.exception("AuditWriter.on_comm error (non-fatal)")

    # ------------------------------------------------------------------
    # Hook: Watchdog alerts
    # ------------------------------------------------------------------

    async def on_watchdog_alert(self, alert: dict[str, Any]) -> None:
        """Called by SessionWatchdogService when firing an alert."""
        if self._db is None:
            return
        try:
            session_id = alert.get("session_id", "")
            project_id = alert.get("project_id")
            watchdog = alert.get("watchdog", "unknown")
            details = alert.get("details", {})
            summary = _truncate(f"Watchdog alert: {watchdog}")
            self._enqueue(
                session_id=session_id,
                project_id=project_id,
                legion_id=None,
                turn_id=None,
                event_type="watchdog",
                tool_name=None,
                status="alert",
                summary=summary,
                message_id=None,
                extra={"watchdog": watchdog, "details": details},
            )
        except Exception:
            logger.exception("AuditWriter.on_watchdog_alert error (non-fatal)")

    # ------------------------------------------------------------------
    # Internal batching
    # ------------------------------------------------------------------

    def _enqueue(
        self,
        session_id: str,
        project_id: str | None,
        legion_id: str | None,
        turn_id: str | None,
        event_type: str,
        tool_name: str | None,
        status: str | None,
        summary: str | None,
        message_id: str | None,
        extra: Any,
    ) -> None:
        self._enqueue_with_ts(
            time.time(), None, session_id, project_id, legion_id, turn_id,
            event_type, tool_name, status, summary, message_id, extra,
        )

    def _enqueue_with_ts(
        self,
        timestamp: float,
        source_ts: float | None,
        session_id: str,
        project_id: str | None,
        legion_id: str | None,
        turn_id: str | None,
        event_type: str,
        tool_name: str | None,
        status: str | None,
        summary: str | None,
        message_id: str | None,
        extra: Any,
    ) -> None:
        row = (
            timestamp,
            source_ts,
            session_id,
            project_id,
            legion_id,
            turn_id,
            event_type,
            tool_name,
            status,
            summary,
            message_id,
            _compact_json(extra),
        )
        self._batch.append(row)
        if len(self._batch) >= _FLUSH_COUNT:
            asyncio.ensure_future(self._flush_now())

    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(_FLUSH_INTERVAL)
            if self._batch:
                await self._flush_now()

    async def _flush_now(self) -> None:
        if not self._batch or self._db is None:
            return
        rows, self._batch = self._batch, []
        sql = (
            "INSERT INTO audit_events "
            "(timestamp, source_ts, session_id, project_id, legion_id, turn_id, "
            "event_type, tool_name, status, summary, message_id, extra_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        try:
            await self._db.execute_write_many(sql, rows)
        except Exception:
            logger.exception("AuditWriter flush failed (rows dropped: %d)", len(rows))

    # ------------------------------------------------------------------
    # Expose last insert timestamp for long-poll notifications
    # ------------------------------------------------------------------

    @property
    def audit_event_callbacks(self) -> list:
        """External callbacks to call after each flush (for long-poll wake-up)."""
        if not hasattr(self, "_audit_event_callbacks"):
            self._audit_event_callbacks: list = []
        return self._audit_event_callbacks


# ------------------------------------------------------------------
# Summary helpers (no raw args copied — only derived human labels)
# ------------------------------------------------------------------

def _make_tool_summary(tool_name: str | None, tool_input: dict) -> str | None:
    """Build a safe ≤256-char human summary from tool name and selected input fields."""
    if not tool_name:
        return None
    tool = tool_name.lower()
    if tool in ("edit", "write", "multiedit"):
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        return _truncate(f"{tool_name}: {path}")
    if tool in ("bash", "shell", "computer"):
        cmd = tool_input.get("command") or tool_input.get("restart") or ""
        return _truncate(f"{tool_name}: {str(cmd)[:80]}")
    if tool in ("read",):
        path = tool_input.get("file_path") or ""
        return _truncate(f"{tool_name}: {path}")
    if tool in ("grep", "glob"):
        pat = tool_input.get("pattern") or tool_input.get("query") or ""
        return _truncate(f"{tool_name}: {pat}")
    return _truncate(f"{tool_name}")


def _make_tool_result_summary(
    tool_name: str | None, metadata: dict, is_error: bool
) -> str | None:
    status_label = "error" if is_error else "ok"
    if not tool_name:
        return _truncate(f"tool result: {status_label}")
    return _truncate(f"{tool_name}: {status_label}")
