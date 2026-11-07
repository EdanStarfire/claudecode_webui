"""SubscriptionStore interface (issue #2103). Implementation arrives with inbound monitoring."""

from __future__ import annotations

from typing import Any, Protocol


class SubscriptionStore(Protocol):
    async def list(self, relay_id: str, session_id: str | None = None) -> list[dict[str, Any]]: ...

    async def add(self, relay_id: str, subscription: dict[str, Any]) -> dict[str, Any]: ...

    async def remove(self, relay_id: str, subscription_id: str) -> bool: ...

    async def update_last_seen(self, relay_id: str, subscription_id: str, ts: str) -> None: ...
