"""SocketTransport seam (issue #2103 stage 3a, D1): the inbound pipeline never imports slack_sdk."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class TransportAuthError(Exception):
    """The app-level token was rejected (revoked, invalid, wrong scope). Never carries a token."""

    def __init__(self, code: str = "invalid_auth"):
        self.code = code
        super().__init__(code)


class TransportUnavailableError(Exception):
    """The transport cannot run (library missing) or could not connect (network). ``code`` is a
    sanitised token."""

    def __init__(self, code: str = "unavailable"):
        self.code = code
        super().__init__(code)


@dataclass
class RawEnvelope:
    envelope_id: str
    type: str  # "events_api" | "hello" | "other"
    payload: dict[str, Any] = field(default_factory=dict)
    retry_attempt: int = 0


EnvelopeHandler = Callable[[RawEnvelope], Awaitable[None]]
# states reported by a transport: connected | reconnecting | dropped | auth_failed
StateHandler = Callable[[str], None]


class SocketTransport(Protocol):
    def set_handlers(self, on_envelope: EnvelopeHandler, on_state: StateHandler) -> None: ...

    async def connect(self) -> None:
        """Raises TransportAuthError / TransportUnavailableError. The envelope handler's return is the
        ack point: the transport acks after it returns (and does not ack when it raises)."""
        ...

    async def disconnect(self) -> None: ...
