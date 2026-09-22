"""SessionRecorder: raw-fidelity capture of SDK traffic for fixture building.

Issue #1998 (stage 1a of epic #1990): every genuine SDK object/stream event,
permission decision, interrupt, lifecycle action, and queue event a recording
session produces is captured dataclass-faithfully to raw_log.jsonl so stage
1b (#1999) can replay it as a test fixture via mock_sdk.py's raw-layer replay
mode. Gated end-to-end behind SessionConfig.recording_enabled plus the
--enable-session-recording Backend flag (see SessionCoordinator.start_session);
off by default and adds no overhead when absent — every capture point in
claude_sdk.py/session_coordinator.py/permission_service.py/event_queue.py
short-circuits on `recorder is None` before ever touching this module.

One instance owns one session's raw_log.jsonl, keyed by session_id in
SessionCoordinator._session_recorders. Reused across restart_session/
reset_session (never recreated) so a scenario spanning a restart keeps
appending to the same file; only torn down on terminate_session.
"""

import dataclasses
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionRecorder:
    """Owns one recording session's append-only raw_log.jsonl.

    Every public method is exception-safe end-to-end (payload construction
    included, not just the file write) — a recording bug must never take down
    the real session it's shadowing. claude_sdk.py's capture points are only
    gated on `recorder is None`; nothing downstream re-guards against a
    recorder call raising.
    """

    def __init__(self, session_id: str, session_dir: Path):
        self.session_id = session_id
        self.log_path = Path(session_dir) / "raw_log.jsonl"
        self._fh = None

    def _write(self, kind: str, build_payload) -> None:
        """Evaluate `build_payload` (a zero-arg callable) and append the resulting
        record. Both payload construction and the file write happen inside the
        same try/except — a caller like record_sdk_message() may need to build
        its payload via dataclasses.asdict(), which is untrusted work too.
        """
        try:
            record = {
                "kind": kind,
                "timestamp": datetime.now(UTC).isoformat(),
                **build_payload(),
            }
            if self._fh is None:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = self.log_path.open("a", encoding="utf-8")
            self._fh.write(json.dumps(record, default=str) + "\n")
            self._fh.flush()
        except Exception:
            logger.exception(
                f"Failed to write raw_log record (kind={kind}) for session {self.session_id}"
            )

    def close(self) -> None:
        """Release the held file handle. Called on session termination —
        SessionRecorder instances are reused across restart/reset, so this
        only ever runs once per recorder's lifetime."""
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                logger.exception(f"Failed to close raw_log handle for session {self.session_id}")
            finally:
                self._fh = None

    def record_sdk_message(self, sdk_message: Any) -> None:
        """Capture a raw SDK object or stream event, dataclass-faithfully.

        Reuses the codebase's existing StoredMessage.from_sdk_message() idiom
        (_type=type(sdk_msg).__name__, data=dataclasses.asdict(sdk_msg)). The
        one exception is ResultError — an Exception subclass, not a dataclass —
        which gets a distinct exception-shape capture so it round-trips via
        backend.raw_replay.reconstruct_sdk_message() the same way.
        """
        def build():
            if isinstance(sdk_message, Exception):
                return {
                    "_type": type(sdk_message).__name__,
                    "data": {
                        "message": str(sdk_message),
                        "data": getattr(sdk_message, "data", None),
                        "exit_code": getattr(sdk_message, "exit_code", None),
                    },
                }
            if not dataclasses.is_dataclass(sdk_message):
                raise TypeError(f"Not a dataclass or Exception: {type(sdk_message).__name__}")
            return {
                "_type": type(sdk_message).__name__,
                "data": dataclasses.asdict(sdk_message),
            }
        self._write("sdk_message", build)

    def record_permission_invocation(
        self, tool_name: str, input_params: dict[str, Any], suggestions: Any = None
    ) -> None:
        """Capture a permission callback invocation, before any decision is made."""
        self._write("permission_invocation", lambda: {
            "tool_name": tool_name,
            "input_params": input_params,
            "suggestions": suggestions,
        })

    def record_permission_response(
        self, tool_name: str, decision: str, reason: str | None = None
    ) -> None:
        """Capture the final decision for a permission callback invocation —
        auto-approved via tool-block rules, auto-approved/denied via suggestion
        matching, or user-decided (every outcome, not just the user-facing path).
        """
        self._write("permission_response", lambda: {
            "tool_name": tool_name,
            "decision": decision,
            "reason": reason,
        })

    def record_interrupt(self) -> None:
        self._write("interrupt", dict)

    def record_lifecycle(self, action: str) -> None:
        """Capture a session lifecycle action: start, restart, reset, pause, terminate."""
        self._write("lifecycle", lambda: {"action": action})

    def record_queue_event(self, event: dict[str, Any]) -> None:
        """Capture an EventQueue.append() payload (issue #1998's on_append hook)."""
        self._write("queue_event", lambda: {"event": event})
