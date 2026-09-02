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
        # Per-session "how far into REMOTE's own event numbering have we already
        # relayed" — must survive a relay task being stopped and a new one started
        # (e.g. on restart_session), or the fresh loop starting from since=0 would
        # re-fetch and re-append REMOTE's entire event backlog into the Hub's local
        # (never-cleared) EventQueue every time, duplicating history in the frontend.
        self._relay_cursors: dict[str, int] = {}
        self._audit_relay_task: asyncio.Task | None = None
        self._audit_relay_active = False
        self._ui_relay_task: asyncio.Task | None = None
        self._ui_relay_active = False

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
        await self.stop_audit_relay()
        await self.stop_ui_relay()
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Audit relay (issue #498 Batch 5, Pattern B — single shared upstream
    # connection, not one per browser poller)
    # ------------------------------------------------------------------

    async def start_audit_relay(self, audit_queue: EventQueue) -> None:
        """Start the single background task that keeps the Hub's local audit_queue
        filled from REMOTE's audit stream.

        Deliberately NOT a per-request relay: /api/poll/audit has no Hub-side
        session scoping, so an admin view opened in N browser tabs (or by N
        different users) would otherwise open N independent long-poll connections
        through to REMOTE — exactly the upstream-connection multiplication Pattern
        B exists to prevent (the same reason start_session's per-session relay
        loop exists, generalized to a single global stream instead of N
        per-session streams). All Hub-side consumers instead read from this one
        local buffer via EventQueue's existing multi-waiter fan-out.
        """
        if self._audit_relay_task is not None and not self._audit_relay_task.done():
            return
        self._audit_relay_active = True
        self._audit_relay_task = asyncio.create_task(
            self._audit_relay_poll_loop(audit_queue), name="remote_audit_relay_poll"
        )

    async def stop_audit_relay(self) -> None:
        self._audit_relay_active = False
        task = self._audit_relay_task
        self._audit_relay_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _audit_relay_poll_loop(self, audit_queue: EventQueue) -> None:
        """Long-poll REMOTE's mirrored /poll/audit and buffer each returned event
        into the Hub's local audit_queue. Uses REMOTE's own DB-timestamp cursor
        internally (private to this loop) to keep fetching from REMOTE; the events
        it appends get their own independent position in the Hub-local
        EventQueue's integer cursor space, which is what /api/poll/audit actually
        serves to browser clients in REMOTE mode (see routers/audit.py) — the two
        cursor spaces are intentionally decoupled, same as session poll relay.
        """
        cursor: float = 0.0
        failures = 0
        while self._audit_relay_active:
            await asyncio.sleep(0)
            try:
                resp = await self._client.get(
                    "/poll/audit", params={"cursor": cursor, "timeout": _POLL_TIMEOUT_SECONDS}
                )
                resp.raise_for_status()
                data = resp.json()
                events = data.get("events", [])
                next_cursor = data.get("next_cursor")
                if next_cursor is not None:
                    cursor = next_cursor
                failures = 0
                for event in events:
                    audit_queue.append(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                logger.warning(
                    f"Audit relay poll failed (attempt {failures}/{MAX_RELAY_FAILURES})",
                    exc_info=True,
                )
                if failures >= MAX_RELAY_FAILURES:
                    logger.error(f"Giving up audit relay poll after {failures} failures")
                    self._audit_relay_active = False
                    break
                await asyncio.sleep(min(2**failures, 30))

    # ------------------------------------------------------------------
    # UI relay (issue #499) — same single-shared-connection pattern as the audit
    # relay above. Without this, every broadcast that lands in the *global*
    # ui_queue (session PAUSED-for-permission state, rate-limit updates, watchdog
    # alerts, SDK-driven permission-mode changes, Legion comm/schedule
    # notifications — anything sent via ClaudeWebUI._on_state_change and friends)
    # only ever reaches REMOTE's own local ui_queue, which the Hub never polls —
    # a systematic gap found auditing every callback/broadcast mechanism against
    # the same "bypasses the per-session relay" pattern as the is_processing fix.
    # ------------------------------------------------------------------

    async def start_ui_relay(self, ui_queue: EventQueue) -> None:
        """Start the single background task that keeps the Hub's local ui_queue
        filled from REMOTE's global UI event stream (/api/backend/poll/ui)."""
        if self._ui_relay_task is not None and not self._ui_relay_task.done():
            return
        self._ui_relay_active = True
        self._ui_relay_task = asyncio.create_task(
            self._ui_relay_poll_loop(ui_queue), name="remote_ui_relay_poll"
        )

    async def stop_ui_relay(self) -> None:
        self._ui_relay_active = False
        task = self._ui_relay_task
        self._ui_relay_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _ui_relay_poll_loop(self, ui_queue: EventQueue) -> None:
        """Long-poll REMOTE's mirrored /poll/ui and replay each event into the
        Hub's local ui_queue. Uses poll_ui's own since/next_cursor integer
        convention (same shape as /poll/session/{id}, unlike audit's DB-timestamp
        cursor) — this loop's cursor is private to it, decoupled from what
        /api/poll/ui actually serves to browser clients from the Hub-local queue.
        """
        cursor = 0
        failures = 0
        while self._ui_relay_active:
            await asyncio.sleep(0)
            try:
                resp = await self._client.get(
                    "/poll/ui", params={"since": cursor, "timeout": _POLL_TIMEOUT_SECONDS}
                )
                resp.raise_for_status()
                data = resp.json()
                events = data.get("events", [])
                cursor = data.get("next_cursor", cursor)
                failures = 0
                for event in events:
                    ui_queue.append(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                logger.warning(
                    f"UI relay poll failed (attempt {failures}/{MAX_RELAY_FAILURES})",
                    exc_info=True,
                )
                if failures >= MAX_RELAY_FAILURES:
                    logger.error(f"Giving up UI relay poll after {failures} failures")
                    self._ui_relay_active = False
                    break
                await asyncio.sleep(min(2**failures, 30))

    # ------------------------------------------------------------------
    # SessionBackend Protocol
    # ------------------------------------------------------------------

    async def create_session(
        self, session_id: str, project_id: str, config: Any, **kwargs: Any
    ) -> str:
        """Mirror session creation onto REMOTE so its own session_manager has a
        record before `start_session` POSTs to REMOTE's `/sessions/{id}/start`
        (issue #498). `config` is the same `SessionCreateRequest`-shaped object
        `SessionCoordinator.create_session` received — dump it wholesale so REMOTE's
        session gets the caller's actual settings (model, permission_mode, tools,
        ...), not REMOTE-side defaults; session_id/project_id are re-applied
        explicitly in case the caller-supplied id differs from what `config` carries.
        """
        payload = config.model_dump(mode="json") if hasattr(config, "model_dump") else dict(kwargs)
        payload["session_id"] = session_id
        payload["project_id"] = project_id
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
        on_relay_event: Callable | None = None,
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
                self._relay_poll_loop(session_id, error_callback, on_relay_event),
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

    async def update_session_name(self, session_id: str, name: str) -> bool:
        resp = await self._put(f"/sessions/{session_id}/name", {"name": name})
        return resp is not None and resp.status_code < 400

    async def update_session_config(self, session_id: str, raw_payload: dict[str, Any]) -> bool:
        """Relay the raw PATCH /api/sessions/{id} body to REMOTE's own mirrored route
        (issue #499) so REMOTE's session config stays in sync with edits made after
        creation, not just what it got from create_session()'s creation-time dump."""
        resp = await self._patch(f"/sessions/{session_id}", raw_payload)
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
        # Unlike disconnect_session (used by restart_session, which needs the cursor
        # preserved so the next start resumes rather than re-fetching everything),
        # a genuine terminate ends this session for good — nothing will resume it.
        self._relay_cursors.pop(session_id, None)
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

    async def get_session_usage(self, session_id: str) -> dict[str, Any] | None:
        """Fetch REMOTE's own /sessions/{id}/usage response (issue #499) — turn/usage
        records only ever get written to REMOTE's local analytics DB (the write
        happens inside _create_message_callback's 'result' handling, which never
        runs on the Hub for a relayed session), so the Hub's own analytics_store has
        nothing to compute from. Callers should take only the raw usage fields from
        this response and recompute cost/rates against the Hub's own configured
        pricing — REMOTE has no UI to configure pricing at all, so its own
        estimated_cost_usd/rates_used are not meaningful here."""
        resp = await self._get(f"/sessions/{session_id}/usage")
        return resp.json() if resp is not None else None

    async def is_session_active(self, session_id: str) -> bool:
        return session_id in self._active_session_ids

    def active_session_ids(self) -> list[str]:
        return list(self._active_session_ids)

    async def list_mcp_configs(self) -> list[dict[str, Any]]:
        """Fetch REMOTE's full MCP config list — used by the Pattern C fan-out-merge
        in routers/mcp.py (list/export). This hits REMOTE's own unmodified
        list_configs() handler, so it comes back containing BOTH REMOTE's shared and
        non-shared entries; the caller is responsible for filtering to
        shared_connection == False before merging with the Hub's own local configs
        (taking this wholesale would leak REMOTE's own shared configs into the Hub's
        view)."""
        resp = await self._get("/mcp-configs", params={"limit": 10_000, "offset": 0})
        if resp is None:
            return []
        return resp.json().get("configs", [])

    async def create_mcp_config(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Create a non-shared MCP config on REMOTE (Pattern D import routing)."""
        try:
            resp = await self._client.post("/mcp-configs", json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            logger.exception("RemoteBackend create_mcp_config failed")
            return None

    async def update_mcp_config(
        self, config_id: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a non-shared MCP config on REMOTE (Pattern D import routing)."""
        try:
            resp = await self._client.put(f"/mcp-configs/{config_id}", json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            logger.exception("RemoteBackend update_mcp_config failed")
            return None

    # ------------------------------------------------------------------
    # Generic Pattern-A relay (issue #498 Batch 2+, see relay_client.py)
    # ------------------------------------------------------------------

    async def relay_request(
        self,
        method: str,
        path: str,
        params: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response | None:
        """Replay an arbitrary request against REMOTE's mirrored route verbatim.

        Used by relay_client.forward() for routers with no Hub-local state to
        keep in sync — the response is the answer, byte-for-byte. Returns None
        on connection failure (never raises — REMOTE being down must not crash
        the Hub); HTTP error statuses from REMOTE itself (404/400/409/...) are
        returned as-is so the caller can forward them verbatim too. `headers`
        merges with (and can't override) the client's own Authorization bearer
        header — used to carry Content-Type through (required for FastAPI's
        body-to-Pydantic-model parsing, and for multipart boundaries).
        """
        try:
            return await self._client.request(
                method, path, params=params, content=content, headers=headers
            )
        except httpx.HTTPError:
            logger.exception(f"RemoteBackend relay {method} {path} failed")
            return None

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

    async def _patch(self, path: str, json_body: dict[str, Any]) -> httpx.Response | None:
        try:
            return await self._client.patch(path, json=json_body)
        except httpx.HTTPError:
            logger.exception(f"RemoteBackend PATCH {path} failed")
            return None

    async def _put(self, path: str, json_body: dict[str, Any]) -> httpx.Response | None:
        try:
            return await self._client.put(path, json=json_body)
        except httpx.HTTPError:
            logger.exception(f"RemoteBackend PUT {path} failed")
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
        on_relay_event: Callable | None = None,
    ) -> None:
        """Long-poll REMOTE's mirrored `/poll/session/{id}` and replay each already-
        formed event envelope into the Hub's local EventQueue for this session — the
        same envelope shape REMOTE's own message_callback produced when it appended
        to REMOTE's own local queue, so the Hub's frontend poll transport sees an
        identical stream to a LOCAL session with no changes to poll.py/web_server.py.

        Resumes from `self._relay_cursors[session_id]` rather than starting at 0 —
        this loop is torn down and restarted fresh on every `restart_session()` (via
        `disconnect_session()`'s `_stop_relay`), and starting over from since=0 would
        re-fetch and re-append REMOTE's entire event backlog into the Hub's local
        EventQueue on every restart, duplicating message history in the frontend.

        `on_relay_event`, if given, is invoked once per relayed event (issue #499) —
        this loop deliberately does NOT run events through the Hub's full
        `_create_message_callback` pipeline (REMOTE already ran that once on its own
        side; doing so again here would double-write Hub-local storage/state that
        only REMOTE should own), but some Hub-local bookkeeping (e.g. is_processing)
        has nothing else that ever updates it for a REMOTE session, so a narrow hook
        is needed instead of the full pipeline.
        """
        cursor = self._relay_cursors.get(session_id, 0)
        failures = 0
        while session_id in self._active_session_ids:
            # Always yield once per iteration — guarantees cancellation is picked up
            # promptly and guards against a runaway tight loop if REMOTE ever answers
            # a "long"-poll instantly and repeatedly instead of genuinely blocking.
            await asyncio.sleep(0)
            try:
                resp = await self._client.get(
                    f"/poll/session/{session_id}",
                    # poll_session's query param is named "since", not "cursor" (see
                    # routers/poll.py) — sending "cursor" was silently ignored by
                    # FastAPI, so every iteration re-fetched from since=0 and
                    # re-appended REMOTE's entire event backlog into the Hub's local
                    # EventQueue on every poll (issue #498 bug, caught in review: the
                    # test's MockTransport handler never asserted on query params).
                    params={"since": cursor, "timeout": _POLL_TIMEOUT_SECONDS},
                )
                resp.raise_for_status()
                data = resp.json()
                events = data.get("events", [])
                cursor = data.get("next_cursor", cursor)
                self._relay_cursors[session_id] = cursor
                failures = 0
                queue = self._session_queues.get(session_id)
                for event in events:
                    if queue is not None:
                        queue.append(event)
                    if on_relay_event is not None:
                        try:
                            await on_relay_event(event)
                        except Exception:
                            logger.exception(
                                f"on_relay_event callback failed for session {session_id}"
                            )
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
