"""SubscriptionStore interface (issue #2103).

Implemented by ``relay/inbound/store.py::JsonSubscriptionStore`` (stage 3a: instruction-thread
records; stage 3b adds monitor kinds on the same storage).
"""

from __future__ import annotations

from typing import Any, Protocol


class SubscriptionStore(Protocol):
    async def list(self, relay_id: str, session_id: str | None = None) -> list[dict[str, Any]]: ...

    async def get(self, relay_id: str, subscription_id: str) -> dict[str, Any] | None: ...

    async def add(self, relay_id: str, subscription: dict[str, Any]) -> dict[str, Any]: ...

    async def remove(self, relay_id: str, subscription_id: str) -> bool: ...

    async def update(
        self, relay_id: str, subscription_id: str, **fields: Any
    ) -> dict[str, Any] | None: ...

    async def update_last_seen(self, relay_id: str, subscription_id: str, ts: str) -> None: ...

    async def list_by_session(self, session_id: str) -> list[dict[str, Any]]: ...

    async def remove_by_session(self, session_id: str) -> int: ...

    async def set_status_by_session(
        self, session_id: str, status: str, *, reason: str | None = None,
        only_reason: str | None = None,
    ) -> int: ...

    def find_thread(self, channel: str, thread_ts: str) -> dict[str, Any] | None: ...
