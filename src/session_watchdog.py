"""
Session Watchdog Service (issues #1130 + #1131)

Detective-only service that monitors sessions for:
  - Idle timeout (#1130): ACTIVE session with no message activity > threshold
  - High error rate (#1131): ≥ threshold fraction of last N tool calls failed/denied

Never mutates session state — only pushes alerts to the UI poll queue.
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .logging_config import get_logger

if TYPE_CHECKING:
    from .config_manager import AppConfig
    from .event_queue import EventQueue
    from .session_manager import SessionInfo, SessionManager
    from .template_manager import TemplateManager

watchdog_logger = get_logger('watchdog', category='WATCHDOG')
logger = logging.getLogger(__name__)


@dataclass
class WatchdogAlertState:
    """Per-session, per-watchdog-type alert episode tracking."""
    idle_alerted: bool = False
    error_rate_alerted: bool = False


@dataclass
class _ToolOutcomeEntry:
    tool_use_id: str
    is_error: bool  # True = FAILED or DENIED


class SessionWatchdogService:
    """
    Singleton background service that evaluates session health on a configurable
    poll interval.

    Activity tracking:
      - last_activity_at is maintained by SessionManager.record_activity() called
        from SessionCoordinator on every SDK message and user-sent message.

    Tool-outcome tracking:
      - Callers invoke record_tool_outcome() after FAILED/DENIED/COMPLETED transitions.
      - INTERRUPTED outcomes are skipped (not counted in numerator or denominator).
      - Deduplication by tool_use_id prevents double-counting replayed results.
    """

    def __init__(
        self,
        session_manager: "SessionManager",
        template_manager: "TemplateManager",
        app_config: "AppConfig",
        ui_queue: "EventQueue",
    ) -> None:
        self._session_manager = session_manager
        self._template_manager = template_manager
        self._app_config = app_config
        self._ui_queue = ui_queue

        self._running = False
        self._task: asyncio.Task | None = None

        # Per-session alert episode state
        self._alert_states: dict[str, WatchdogAlertState] = {}

        # Per-session tool outcome deque (maxlen=10, True=error, False=ok)
        self._tool_outcomes: dict[str, deque[bool]] = {}

        # Per-session set of tool_use_ids already recorded (dedup guard)
        self._recorded_tool_ids: dict[str, set[str]] = {}

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop())
        watchdog_logger.info("SessionWatchdogService started")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        watchdog_logger.info("SessionWatchdogService stopped")

    # =========================================================================
    # Public API — called from SessionCoordinator
    # =========================================================================

    def record_tool_outcome(self, session_id: str, tool_use_id: str, status: str) -> None:
        """Record terminal tool call outcome for error-rate tracking.

        Args:
            session_id: Session the tool belongs to.
            tool_use_id: Unique tool call ID for deduplication.
            status: ToolState value string: 'completed', 'failed', 'denied', 'interrupted'.
                    'interrupted' is skipped entirely (not counted in numerator or denominator).
        """
        if status == "interrupted":
            return

        # Dedup: only first terminal state per tool_use_id counts
        seen = self._recorded_tool_ids.setdefault(session_id, set())
        if tool_use_id in seen:
            return
        seen.add(tool_use_id)

        is_error = status in ("failed", "denied")
        outcomes = self._tool_outcomes.setdefault(session_id, deque(maxlen=10))
        outcomes.append(is_error)

    def reset_session(self, session_id: str) -> None:
        """Clear per-session watchdog state (deque + alert state + seen IDs).

        Called on session restart or reset so the next episode starts fresh.
        Idempotent — safe to call even if session was never tracked.
        """
        self._tool_outcomes.pop(session_id, None)
        self._alert_states.pop(session_id, None)
        self._recorded_tool_ids.pop(session_id, None)

    # =========================================================================
    # Internal
    # =========================================================================

    async def _tick_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Watchdog tick error")
            try:
                interval = self._app_config.watchdog.poll_interval_seconds
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        sessions = await self._session_manager.list_sessions()
        for session in sessions:
            try:
                await self._evaluate_session(session)
            except Exception:
                logger.exception(f"Watchdog evaluation error for session {session.session_id}")

    async def _evaluate_session(self, session: "SessionInfo") -> None:
        from .session_manager import SessionState

        cfg = self._resolve_config(session)
        if not cfg.get("enabled", False):
            return

        state = self._alert_states.setdefault(session.session_id, WatchdogAlertState())
        now = datetime.now(UTC)

        # --- Re-arm logic (clear alerted flag when condition resolves) ---
        if state.idle_alerted:
            last = session.last_activity_at
            idle_cfg = cfg.get("idle", {})
            timeout = idle_cfg.get("timeout_seconds", 300)
            if last and (now - last).total_seconds() < timeout:
                state.idle_alerted = False

        if state.error_rate_alerted:
            outcomes = self._tool_outcomes.get(session.session_id)
            error_cfg = cfg.get("error_rate", {})
            threshold = error_cfg.get("threshold", 0.6)
            if outcomes is None or len(outcomes) == 0:
                current_ratio = 0.0
            else:
                current_ratio = sum(outcomes) / len(outcomes)
            if current_ratio < threshold:
                state.error_rate_alerted = False

        # --- Idle check ---
        idle_cfg = cfg.get("idle", {})
        if idle_cfg.get("enabled", False) and not state.idle_alerted:
            # Only ACTIVE sessions trigger idle alerts; PAUSED is exempt
            if session.state == SessionState.ACTIVE:
                last = session.last_activity_at
                timeout = idle_cfg.get("timeout_seconds", 300)
                if last is not None:
                    idle_seconds = (now - last).total_seconds()
                    if idle_seconds > timeout:
                        state.idle_alerted = True
                        await self._fire_alert(
                            session,
                            watchdog="idle",
                            details={"idle_seconds": round(idle_seconds, 1)},
                        )

        # --- Error-rate check ---
        error_cfg = cfg.get("error_rate", {})
        if error_cfg.get("enabled", False) and not state.error_rate_alerted:
            outcomes = self._tool_outcomes.get(session.session_id)
            if outcomes is not None:
                min_calls = error_cfg.get("min_calls", 10)
                threshold = error_cfg.get("threshold", 0.6)
                n = len(outcomes)
                if n >= min_calls:
                    error_count = sum(outcomes)
                    ratio = error_count / n
                    if ratio >= threshold:
                        state.error_rate_alerted = True
                        await self._fire_alert(
                            session,
                            watchdog="error_rate",
                            details={
                                "tool_call_count": n,
                                "tool_error_count": error_count,
                                "error_rate": round(ratio, 3),
                            },
                        )

    def _resolve_config(self, session: "SessionInfo") -> dict[str, Any]:
        """Merge template watchdog block with global AppConfig defaults.

        Resolution order (highest priority first):
        1. Template watchdog block (if session was created from a template)
        2. Global AppConfig.watchdog
        """
        global_cfg = self._app_config.watchdog

        base: dict[str, Any] = {
            "enabled": global_cfg.enabled,
            "idle": {
                "enabled": global_cfg.idle.enabled,
                "timeout_seconds": global_cfg.idle.timeout_seconds,
            },
            "error_rate": {
                "enabled": global_cfg.error_rate.enabled,
                "min_calls": global_cfg.error_rate.min_calls,
                "threshold": global_cfg.error_rate.threshold,
            },
        }

        if session.template_id:
            template = self._template_manager.templates.get(session.template_id)
            if template and template.watchdog:
                tw: dict = template.watchdog
                # Master switch
                if "enabled" in tw:
                    base["enabled"] = tw["enabled"]
                # Idle sub-config
                if "idle" in tw and isinstance(tw["idle"], dict):
                    base["idle"].update(tw["idle"])
                # Error-rate sub-config
                if "error_rate" in tw and isinstance(tw["error_rate"], dict):
                    base["error_rate"].update(tw["error_rate"])

        # If master switch is False, both sub-watchdogs are disabled
        if not base["enabled"]:
            base["idle"]["enabled"] = False
            base["error_rate"]["enabled"] = False

        return base

    async def _fire_alert(
        self,
        session: "SessionInfo",
        watchdog: str,
        details: dict[str, Any],
    ) -> None:
        event: dict[str, Any] = {
            "type": "session_watchdog_alert",
            "session_id": session.session_id,
            "session_name": session.name or session.session_id,
            "project_id": session.project_id,
            "watchdog": watchdog,
            "details": details,
            "fired_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._ui_queue.append(event)
            watchdog_logger.info(
                f"Watchdog alert fired: session={session.session_id} type={watchdog} details={details}"
            )
        except Exception:
            logger.exception(f"Failed to push watchdog alert for session {session.session_id}")
