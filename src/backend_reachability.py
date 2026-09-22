"""Backend reachability tracking — shared "is Backend currently unreachable?"
signal for Frontend-side code that talks to Backend (issue #1844).

Without this, every route handler's own `httpx.RequestError` (Backend down,
still starting, crashed, genuinely remote and network-partitioned) propagates
into `shared/exception_handlers.py`'s generic `except Exception` and gets a
full ERROR-level traceback logged once per request — indistinguishable from a
genuine bug, and multiplied by however many concurrent requests hit the relay
while Backend is down. `BackendReachabilityTracker` collapses that into one
concise warning per outage (plus an occasional reminder during a long one)
regardless of which code path or which of the three windows (startup,
crash, manual stop) noticed first.

Deliberately generic/reusable groundwork for #1847 (coordinated
restart/rollback needs this same signal) — nothing restart-specific lives
here. Lives under `src/` only; must not import from `backend/` (see
`src/tests/test_import_boundary.py`).
"""

import logging
import time

from fastapi import HTTPException

from shared.logging_config import get_debug_flag

logger = logging.getLogger(__name__)

# How long an ongoing outage stays silent before a reminder warning is logged
# again — long enough to avoid flooding error.log on every failed request,
# short enough that a genuine multi-minute outage doesn't go completely quiet.
_COOLDOWN_SECONDS = 30.0


class BackendReachabilityTracker:
    """Tracks whether Backend is currently believed unreachable.

    Plain instance state, no locking — same single-event-loop assumption as
    `web_server.py`'s existing `_last_restart_time`.
    """

    def __init__(self, cooldown_seconds: float = _COOLDOWN_SECONDS):
        self._cooldown_seconds = cooldown_seconds
        self._unreachable = False
        self._last_logged: float = 0.0

    @property
    def is_unreachable(self) -> bool:
        return self._unreachable

    def record_failure(self, exc: Exception) -> None:
        """Call on a connection-level failure (httpx.RequestError) talking to Backend.

        Logs once on the transition into the unreachable state, then again only
        after the cooldown elapses while the outage continues.

        Uses logger.error() (not .warning()) deliberately: this module's plain
        `logging.getLogger(__name__)` propagates only to the root logger, whose
        only handlers (shared/logging_config.py's error_handler and
        console_error_handler) are filtered to ERROR+ — a .warning() call here
        would be silently dropped, reaching neither error.log nor the console
        (confirmed via manual verification, issue #1844). This is still a single
        concise line, never a traceback (no logger.exception), so it stays
        distinguishable from a genuine bug despite sharing the same level.
        """
        now = time.monotonic()
        should_log = not self._unreachable or now - self._last_logged >= self._cooldown_seconds
        self._unreachable = True
        if should_log:
            self._last_logged = now
            logger.error(
                "Backend unreachable (%s: %s); suppressing repeat notices for %ds",
                type(exc).__name__, exc, int(self._cooldown_seconds),
            )

    def record_success(self) -> None:
        """Call on any successful connection to Backend (status code irrelevant).

        No-op unless a prior failure was recorded. See record_failure() for why
        this logs at ERROR rather than WARNING.
        """
        if self._unreachable:
            self._unreachable = False
            self._last_logged = 0.0
            logger.error("Backend reachable again")


class BackendUnreachableError(HTTPException):
    """HTTPException subclass carrying a machine-readable `error_code`.

    A plain HTTPException's `detail` can't be surfaced alongside a sibling
    `error_code` key without nesting under `detail` — `src/web_server.py`
    registers an exception handler keyed on this subclass to flatten both
    into the top-level response body.
    """

    def __init__(self, status_code: int, detail: str, error_code: str):
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code


def is_backend_degraded(webui) -> bool:
    """Whether Backend's auto-restart supervisor has given up after its
    crash-loop budget — centralizes the `backend_supervisor is not None and
    backend_supervisor.degraded` check (None in remote-Backend mode, where
    "degraded" can never apply) so every call site derives it identically
    instead of duplicating the None-guard inline (issue #1989 review finding:
    two call sites — src/routers/config.py, src/web_server.py's dynamic OAuth
    callback relay — were missed entirely when this was first wired through
    inline at each of relay.py's/poll.py's call sites).
    """
    return webui.backend_supervisor is not None and webui.backend_supervisor.degraded


def to_http_exception(exc: Exception, *, degraded: bool = False) -> BackendUnreachableError:
    """Build the HTTPException a route handler should raise for a Backend
    connection failure, so it can pre-empt `handle_exceptions` (which would
    otherwise log a second, full-traceback copy of the same failure already
    recorded by `BackendReachabilityTracker.record_failure`).

    `degraded=True` (Backend's auto-restart supervisor gave up after its
    crash-loop budget) is distinguished from a plain transient outage
    (`degraded=False` — the poll-relay's own retry loop is expected to
    recover it) so the browser can tell "will likely self-heal" apart from
    "needs a restart" (issue #1989).
    """
    if degraded:
        status_code, error_code, message = 503, "backend_degraded", "Backend is degraded and unavailable"
    else:
        status_code, error_code, message = 502, "backend_unreachable", "Backend is unreachable"
    detail = f"{message}: {exc}" if get_debug_flag('debug_error_handler') else message
    return BackendUnreachableError(status_code=status_code, detail=detail, error_code=error_code)
