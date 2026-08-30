"""
RemoteBackend: relays session operations to a configured REMOTE WebUI instance
running headless (issue #498/#499).

REMOTE is defined as literally the same, unmodified application running in headless
mode (Batch 0's `--headless-backend`) — every method here is a Pattern-A `httpx`
relay call against REMOTE's mirrored `/api/backend/*` routes, using the same wire
shapes today's `/api/*` routes already use. `start_session` additionally spawns a
background long-poll relay loop per session so the Hub's own frontend poll transport
sees an identical event stream to a LOCAL session, with zero changes to the
~250-line message-callback logic in web_server.py/session_coordinator.py.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx

from .event_queue import EventQueue

logger = logging.getLogger(__name__)

# Issue #498 contract §8: retry-with-backoff, mark session ERROR after this many
# consecutive poll failures, Hub process never crashes.
MAX_RELAY_FAILURES = 5
_POLL_TIMEOUT_SECONDS = 30.0
_RELAY_HTTP_TIMEOUT = _POLL_TIMEOUT_SECONDS + 10.0


class RemoteBackend:
    """SessionBackend implementation that relays every call to a configured REMOTE."""

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        session_queues: dict[str, EventQueue],
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=f"{self._base_url}/api/backend",
            headers={"Authorization": f"Bearer {auth_token}"},
            timeout=_RELAY_HTTP_TIMEOUT,
        )
        # Session poll queues (EventQueue instances), same dict web_server.py hands
        # to LocalBackend-driven sessions — relayed events land here identically.
        self._session_queues = session_queues
        self._relay_tasks: dict[str, asyncio.Task] = {}
        self._active_session_ids: set[str] = set()

    async def aclose(self) -> None:
        tasks = list(self._relay_tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._relay_tasks.clear()
        await self._client.aclose()

    # ------------------------------------------------------------------
    # SessionBackend Protocol
    # ------------------------------------------------------------------

    async def create_session(
        self, session_id: str, project_id: str, config: Any, **kwargs: Any
    ) -> str:
        payload = {"session_id": session_id, "project_id": project_id, **kwargs}
        resp = await self._client.post("/sessions", json=payload)
        resp.raise_for_status()
        return session_id

    async def start_session(
        self,
        session_id: str,
        *,
        sdk_kwargs: dict[str, Any] | None = None,
        permission_callback: Callable | None = None,
        auto_approval_callback: Callable | None = None,
        error_callback: Callable | None = None,
    ) -> tuple[bool, str | None]:
        try:
            resp = await self._client.post(f"/sessions/{session_id}/start")
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return False, f"REMOTE returned {e.response.status_code}: {e.response.text}"
        except httpx.HTTPError as e:
            return False, f"REMOTE unreachable: {e}"

        self._active_session_ids.add(session_id)
        existing = self._relay_tasks.get(session_id)
        if existing is None or existing.done():
            task = asyncio.create_task(
                self._relay_poll_loop(session_id, error_callback),
                name=f"remote_relay_poll_{session_id}",
            )
            self._relay_tasks[session_id] = task
        return True, None

    async def send_message(
        self, session_id: str, message: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        resp = await self._post(
            f"/sessions/{session_id}/messages", {"message": message, "metadata": metadata}
        )
        return resp is not None and resp.status_code < 400

    async def interrupt_session(self, session_id: str) -> bool:
        resp = await self._post(f"/sessions/{session_id}/interrupt", {})
        return resp is not None and resp.status_code < 400

    async def set_permission_mode(self, session_id: str, mode: str) -> bool:
        resp = await self._post(f"/sessions/{session_id}/permission-mode", {"mode": mode})
        return resp is not None and resp.status_code < 400

    async def set_model(self, session_id: str, model: str) -> bool:
        resp = await self._post(f"/sessions/{session_id}/model", {"model": model})
        return resp is not None and resp.status_code < 400

    async def resolve_permission(
        self, session_id: str, request_id: str, response: dict[str, Any]
    ) -> bool:
        resp = await self._post(f"/sessions/{session_id}/permission/{request_id}", response)
        return resp is not None and resp.status_code < 400

    async def get_mcp_status(self, session_id: str) -> dict[str, Any]:
        resp = await self._get(f"/sessions/{session_id}/mcp-status")
        return resp.json() if resp is not None else {"servers": []}

    async def get_context_usage(self, session_id: str) -> dict[str, Any]:
        resp = await self._get(f"/sessions/{session_id}/context-usage")
        return resp.json() if resp is not None else {}

    async def toggle_mcp_server(self, session_id: str, name: str, enabled: bool) -> None:
        resp = await self._client.post(
            f"/sessions/{session_id}/mcp-toggle", json={"name": name, "enabled": enabled}
        )
        resp.raise_for_status()

    async def reconnect_mcp_server(self, session_id: str, name: str) -> None:
        resp = await self._client.post(f"/sessions/{session_id}/mcp-reconnect", json={"name": name})
        resp.raise_for_status()

    async def add_directory(self, session_id: str, directory: str) -> dict[str, Any]:
        resp = await self._client.post(
            f"/sessions/{session_id}/add-directory", json={"directory": directory}
        )
        resp.raise_for_status()
        return resp.json()

    async def disconnect_session(self, session_id: str) -> bool:
        self._stop_relay(session_id)
        resp = await self._post(f"/sessions/{session_id}/disconnect", {})
        return resp is not None and resp.status_code < 400

    async def terminate_session(self, session_id: str) -> bool:
        self._stop_relay(session_id)
        resp = await self._post(f"/sessions/{session_id}/terminate", {})
        return resp is not None and resp.status_code < 400

    async def get_session_runtime_info(self, session_id: str) -> dict[str, Any] | None:
        resp = await self._get(f"/sessions/{session_id}")
        return resp.json() if resp is not None else None

    async def get_messages(
        self, session_id: str, limit: int | None = 50, offset: int = 0
    ) -> dict[str, Any]:
        """Pattern A relay for message history — REMOTE is the only place a REMOTE
        session's messages.jsonl actually lives, so this is a wholesale forward, not
        a Hub-local read (see routers/sessions.py's get_messages route)."""
        resp = await self._get(
            f"/sessions/{session_id}/messages", params={"limit": limit, "offset": offset}
        )
        return resp.json() if resp is not None else {"messages": [], "total": 0}

    async def is_session_active(self, session_id: str) -> bool:
        return session_id in self._active_session_ids

    def active_session_ids(self) -> list[str]:
        return list(self._active_session_ids)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response | None:
        try:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
            return resp
        except httpx.HTTPError:
            logger.exception(f"RemoteBackend GET {path} failed")
            return None

    async def _post(self, path: str, json_body: dict[str, Any]) -> httpx.Response | None:
        try:
            return await self._client.post(path, json=json_body)
        except httpx.HTTPError:
            logger.exception(f"RemoteBackend POST {path} failed")
            return None

    def _stop_relay(self, session_id: str) -> None:
        self._active_session_ids.discard(session_id)
        task = self._relay_tasks.pop(session_id, None)
        if task:
            task.cancel()

    async def _relay_poll_loop(
        self,
        session_id: str,
        error_callback: Callable | None,
    ) -> None:
        """Long-poll REMOTE's mirrored `/poll/session/{id}` and replay each already-
        formed event envelope into the Hub's local EventQueue for this session — the
        same envelope shape REMOTE's own message_callback produced when it appended
        to REMOTE's own local queue, so the Hub's frontend poll transport sees an
        identical stream to a LOCAL session with no changes to poll.py/web_server.py.
        """
        cursor = 0
        failures = 0
        while session_id in self._active_session_ids:
            # Always yield once per iteration — guarantees cancellation is picked up
            # promptly and guards against a runaway tight loop if REMOTE ever answers
            # a "long"-poll instantly and repeatedly instead of genuinely blocking.
            await asyncio.sleep(0)
            try:
                resp = await self._client.get(
                    f"/poll/session/{session_id}",
                    params={"cursor": cursor, "timeout": _POLL_TIMEOUT_SECONDS},
                )
                resp.raise_for_status()
                data = resp.json()
                events = data.get("events", [])
                cursor = data.get("next_cursor", cursor)
                failures = 0
                queue = self._session_queues.get(session_id)
                if queue is not None:
                    for event in events:
                        queue.append(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                logger.warning(
                    f"Relay poll failed for session {session_id} "
                    f"(attempt {failures}/{MAX_RELAY_FAILURES})",
                    exc_info=True,
                )
                if failures >= MAX_RELAY_FAILURES:
                    logger.error(
                        f"Giving up relay poll for session {session_id} after {failures} failures"
                    )
                    self._active_session_ids.discard(session_id)
                    if error_callback is not None:
                        # Matches SessionCoordinator._create_error_callback's inner
                        # signature: async def callback(error_type: str, error: Exception)
                        # — session_id is already bound in that closure, not passed here.
                        exc = RuntimeError(
                            f"Lost connection to REMOTE after {failures} failed poll attempts"
                        )
                        if asyncio.iscoroutinefunction(error_callback):
                            await error_callback("relay_connection_lost", exc)
                        else:
                            error_callback("relay_connection_lost", exc)
                    break
                await asyncio.sleep(min(2**failures, 30))
