"""Real SocketTransport on ``slack_sdk``'s aiohttp SocketModeClient (issue #2103 stage 3a, D1).

``slack_sdk`` is imported lazily inside ``connect()``: a missing library makes the service report
``transport_unavailable`` instead of breaking Backend start. The app-level token is read at
connect time only, handed to the client and never logged; errors are reduced to sanitised codes.

The SDK's own reconnect loop retries every few seconds forever, including on a revoked token, so
the client subclass takes ``issue_new_wss_url`` failures out of its hands: the client is closed
and the failure reported, and the supervisor (``service.py``) applies the backoff policy.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ..adapter import RelayError
from ..safety import safe_error_code
from .transport import (
    EnvelopeHandler,
    RawEnvelope,
    StateHandler,
    TransportAuthError,
    TransportUnavailableError,
)

logger = logging.getLogger(__name__)

AUTH_ERROR_CODES = frozenset({
    "invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive",
    "invalid_token", "missing_scope", "no_permission", "not_allowed_token_type",
})


class SlackSocketTransport:
    def __init__(self, token_provider: Callable[[], str]) -> None:
        self._token_provider = token_provider  # sync; runs in a thread (keyring can block)
        self._on_envelope: EnvelopeHandler | None = None
        self._on_state: StateHandler | None = None
        self._client: Any = None
        self._fatal: str | None = None

    def set_handlers(self, on_envelope: EnvelopeHandler, on_state: StateHandler) -> None:
        self._on_envelope = on_envelope
        self._on_state = on_state

    def _emit(self, state: str) -> None:
        if self._on_state is not None:
            self._on_state(state)

    async def connect(self) -> None:
        try:
            from slack_sdk.errors import SlackApiError
            from slack_sdk.socket_mode.aiohttp import SocketModeClient
            from slack_sdk.web.async_client import AsyncWebClient
        except ImportError:
            raise TransportUnavailableError("slack_sdk_missing") from None
        try:
            token = await asyncio.to_thread(self._token_provider)
        except RelayError:
            raise TransportAuthError("not_authed") from None
        transport = self

        class _GuardedClient(SocketModeClient):
            async def issue_new_wss_url(self) -> str:
                try:
                    return await super().issue_new_wss_url()
                except SlackApiError as e:
                    code = safe_error_code((getattr(e, "response", None) or {}).get("error"))
                except Exception:
                    code = "network_error"
                self.closed = True  # stop the SDK's endless retry loop; the supervisor decides
                transport._fail(code)
                raise TransportUnavailableError(code)

        sdk_logger = logging.getLogger("backend.relay.inbound.slack_sdk")
        sdk_logger.setLevel(logging.WARNING)
        client = _GuardedClient(
            app_token=token, web_client=AsyncWebClient(), logger=sdk_logger,
            auto_reconnect_enabled=True,
        )
        del token
        client.message_listeners.append(self._listener)
        self._client = client
        self._fatal = None
        try:
            await client.connect()
        except TransportUnavailableError:
            pass
        except Exception:
            self._fatal = self._fatal or "network_error"
        if self._fatal is not None or client.closed:
            code = self._fatal or "network_error"
            await self._close_client()
            if code in AUTH_ERROR_CODES:
                raise TransportAuthError(code)
            raise TransportUnavailableError(code)

    def _fail(self, code: str) -> None:
        self._fatal = code
        self._emit("auth_failed" if code in AUTH_ERROR_CODES else "dropped")

    async def _listener(self, client: Any, message: dict, raw: str) -> None:
        handler = self._on_envelope
        if handler is None:
            return
        kind = message.get("type")
        if kind == "hello":
            self._emit("connected")
            await handler(RawEnvelope("", "hello", message))
            return
        envelope_id = message.get("envelope_id")
        if kind == "events_api" and envelope_id:
            payload = message.get("payload")
            await handler(RawEnvelope(
                str(envelope_id), "events_api", payload if isinstance(payload, dict) else {},
                int(message.get("retry_attempt") or 0),
            ))
        # ack after the handler returned (it raising skips the ack: Slack redelivers, dedupe absorbs)
        if envelope_id:
            await client.send_socket_mode_response({"envelope_id": envelope_id})

    async def _close_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.debug("Socket client close failed", exc_info=True)

    async def disconnect(self) -> None:
        await self._close_client()
