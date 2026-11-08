"""Owner self-DM resolution (issue #2103 outbound-1c-ii).

Messages the relay sends into the owner's self-DM carry the agent marker, keyed on the RESOLVED
conversation ID (however the agent addressed it). Order: in-memory cache -> the ID persisted on
the relay record -> ``open_dm(owner)`` (then persisted). The cache is keyed by owner, so a
swapped token (another owner) never reuses a stale ID.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .adapter import RelayError

if TYPE_CHECKING:
    from backend.relay_manager import RelayManager

    from .gate import RelayContext

logger = logging.getLogger(__name__)


class SelfDmResolver:
    def __init__(self, relay_manager: RelayManager) -> None:
        self._manager = relay_manager
        self._cache: dict[tuple[str, str, str | None], str] = {}

    async def resolve(self, ctx: RelayContext) -> str | None:
        """The owner's self-DM channel ID, or None when it cannot be determined."""
        owner = ctx.owner_user_id
        if not owner:
            return None
        record = self._manager.relays.get(ctx.relay_id)
        # keyed by workspace too: the same user ID can exist in another workspace (Enterprise Grid)
        key = (ctx.relay_id, owner, getattr(record, "workspace_id", None))
        if key in self._cache:
            return self._cache[key]
        persisted = getattr(record, "self_dm_channel_id", None)
        if persisted and getattr(record, "owner_user_id", None) == owner:
            self._cache[key] = persisted
            return persisted
        try:
            dm = await ctx.adapter.open_dm(owner)
        except RelayError as e:
            logger.warning("Could not resolve the owner's self-DM (%s)", e.code)
            return None
        if not dm:
            return None
        self._cache[key] = dm
        record_self_dm = getattr(self._manager, "record_self_dm", None)
        if record_self_dm is not None:
            record_self_dm(ctx.relay_id, owner, dm)
        return dm
