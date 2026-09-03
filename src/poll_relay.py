"""Poll-relay: background long-poll tasks that fan Backend's poll streams out to
local per-session/UI EventQueues (issue #498).

Backend owns the real EventQueues (SessionCoordinator pushes events there
directly, same-process). Frontend's own /api/poll/ui and /api/poll/session/{id}
handlers (src/routers/poll.py) still read from LOCAL EventQueues exactly as
before the split — what changed is who *populates* them: instead of direct
in-process coordinator callbacks, a background task per stream continuously
long-polls the matching Backend endpoint and re-appends what it gets back.
This is the "poll-relay" pattern (as opposed to the generic reverse-proxy):
needed here specifically because multiple browser tabs share one local
EventQueue, which would otherwise mean multiple duplicate upstream long-poll
connections to Backend for the same stream.
"""

import asyncio
import logging

import httpx

from shared.event_queue import EventQueue

logger = logging.getLogger(__name__)

# Backoff on relay-loop errors (Backend unreachable, restarting, etc.) — never
# busy-loop against a down Backend.
_ERROR_BACKOFF_SECONDS = 2.0
_POLL_TIMEOUT_SECONDS = 30


class PollRelay:
    """Owns the background relay tasks fanning Backend's poll streams into local queues."""

    def __init__(self, backend_client, ui_queue: EventQueue, session_queues: dict[str, EventQueue]):
        self._backend_client = backend_client
        self._ui_queue = ui_queue
        self._session_queues = session_queues
        self._ui_task: asyncio.Task | None = None
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._stopped = False

    def start_ui_relay(self) -> None:
        """Start the single background task relaying Backend's /api/poll/ui stream."""
        if self._ui_task is not None and not self._ui_task.done():
            return
        self._ui_task = asyncio.create_task(self._relay_loop("/api/poll/ui", self._ui_queue), name="poll_relay_ui")

    def ensure_session_relay(self, session_id: str) -> None:
        """Start a background relay task for this session if one isn't already running.

        Lazily started on first local poll request for a session — cheap to keep
        running for the app's lifetime afterward (idle long-poll, not a busy loop).
        """
        existing = self._session_tasks.get(session_id)
        if existing is not None and not existing.done():
            return
        if session_id not in self._session_queues:
            self._session_queues[session_id] = EventQueue()
        path = f"/api/poll/session/{session_id}"
        self._session_tasks[session_id] = asyncio.create_task(
            self._relay_loop(path, self._session_queues[session_id]),
            name=f"poll_relay_session_{session_id}",
        )

    async def _relay_loop(self, path: str, queue: EventQueue) -> None:
        cursor = 0
        while not self._stopped:
            try:
                events, next_cursor = await self._poll_once(path, cursor)
                for event in events:
                    queue.append(event)
                cursor = next_cursor
            except asyncio.CancelledError:
                raise
            except httpx.HTTPError:
                logger.warning("Poll-relay for %s: Backend unreachable, retrying", path)
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
            except Exception:
                logger.exception("Poll-relay for %s: unexpected error, retrying", path)
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)

    async def _poll_once(self, path: str, cursor: int) -> tuple[list[dict], int]:
        body = await self._backend_client.get_json(
            path, params={"since": cursor, "timeout": _POLL_TIMEOUT_SECONDS}
        )
        return body.get("events", []), body.get("next_cursor", cursor)

    async def stop(self) -> None:
        """Cancel all relay tasks (called during app shutdown)."""
        self._stopped = True
        tasks = [t for t in [self._ui_task, *self._session_tasks.values()] if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
