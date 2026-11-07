"""RelayService: attachment resolution, live policy merge, handle assignment (issue #2103).

Policies are read from live manager state on every call (never from the start-time
SessionConfig snapshot), so runtime edits need no restart.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .handles import HandleConflictError, RelayHandleIndex
from .policy import (
    EffectivePolicy,
    merge_policies,
    validate_channel_id,
    validate_policy_value,
)
from .safety import validate_handle
from .validation import validate_relay_config

if TYPE_CHECKING:
    from backend.profile_manager import ProfileManager
    from backend.relay_manager import RelayManager
    from backend.session_manager import SessionInfo, SessionManager
    from backend.template_manager import TemplateManager

logger = logging.getLogger(__name__)

_ATTACHMENT_FIELDS = (
    "relay_id", "relay_footer_enabled", "relay_footer_template",
)


class SessionNotFoundError(KeyError):
    pass


class RelayService:
    def __init__(
        self,
        relay_manager: RelayManager,
        handle_index: RelayHandleIndex,
        session_manager: SessionManager,
        template_manager: TemplateManager,
        profile_manager: ProfileManager,
    ) -> None:
        self.relay_manager = relay_manager
        self.handles = handle_index
        self._sessions = session_manager
        self._templates = template_manager
        self._profiles = profile_manager
        self._handle_lock = asyncio.Lock()  # index change + persistence stay atomic

    # ---------------------------------------------------------------- tiers

    async def _tier_configs(
        self, session_info: SessionInfo
    ) -> tuple[list[dict], dict, dict]:
        """(profile configs in template order, template config, session config), live."""
        profile_cfgs: list[dict] = []
        template_cfg: dict = {}
        if session_info.template_id:
            template = await self._templates.get_template(session_info.template_id)
            if template:
                for profile_id in (template.profile_ids or {}).values():
                    profile = await self._profiles.get_profile(profile_id)
                    if profile:
                        profile_cfgs.append(profile.config or {})
                template_cfg = template.config or {}
        return profile_cfgs, template_cfg, session_info.config or {}

    async def effective_policies(self, session_info: SessionInfo) -> dict[str, EffectivePolicy]:
        """Per-key merge of channel policies across profile < template < session."""
        profile_cfgs, template_cfg, session_cfg = await self._tier_configs(session_info)
        return merge_policies(
            [c.get("relay_channel_policies") for c in profile_cfgs],
            template_cfg.get("relay_channel_policies"),
            session_cfg.get("relay_channel_policies"),
        )

    async def effective_attachment(self, session_info: SessionInfo) -> dict[str, Any]:
        """Resolved relay attachment fields with the tier each value came from."""
        profile_cfgs, template_cfg, session_cfg = await self._tier_configs(session_info)
        result: dict[str, Any] = {}
        sources: dict[str, str] = {}
        for field in _ATTACHMENT_FIELDS:
            result[field] = None
            for source, cfg in [
                *[("profile", c) for c in profile_cfgs], ("template", template_cfg),
                ("session", session_cfg),
            ]:
                if cfg.get(field) is not None:
                    result[field] = cfg[field]
                    sources[field] = source
        result["sources"] = sources
        return result

    async def _require_session(self, session_id: str) -> SessionInfo:
        info = await self._sessions.get_session_info(session_id)
        if info is None:
            raise SessionNotFoundError(session_id)
        return info

    async def get_session_relay(self, session_id: str) -> dict[str, Any]:
        info = await self._require_session(session_id)
        attachment = await self.effective_attachment(info)
        policies = await self.effective_policies(info)
        relay_id = attachment["relay_id"]
        record = self.relay_manager.relays.get(relay_id) if relay_id else None
        return {
            "session_id": session_id,
            "attached": bool(relay_id and record),
            "relay_id": relay_id,
            "relay_name": record.name if record else None,
            "relay_handle": info.config.get("relay_handle"),
            "footer_enabled": attachment["relay_footer_enabled"],
            "footer_template": attachment["relay_footer_template"],
            "sources": attachment["sources"],
            "policies": {
                cid: {"policy": p.policy, "source": p.source} for cid, p in policies.items()
            },
        }

    # -------------------------------------------------------------- policies

    async def set_policy(self, session_id: str, channel_id: str, policy: str) -> None:
        await self._require_session(session_id)
        validate_channel_id(channel_id)
        validate_policy_value(policy)
        await self._sessions.update_relay_policy(session_id, channel_id, policy)

    async def delete_policy(self, session_id: str, channel_id: str) -> None:
        await self._require_session(session_id)
        validate_channel_id(channel_id)
        await self._sessions.update_relay_policy(session_id, channel_id, None)

    # --------------------------------------------------------------- handles

    async def set_handle(self, session_id: str, handle: str) -> str:
        """User-edited handle: validates format/reserved, 409s on duplicates."""
        await self._require_session(session_id)
        validate_handle(handle)
        async with self._handle_lock:
            previous = self.handles.get_handle(session_id)
            await self.handles.set_handle(session_id, handle)
            if not await self._sessions.set_relay_handle(session_id, handle):
                self.handles.remove_session(session_id)
                if previous and not self.handles.is_taken(previous):
                    self.handles.rebind(session_id, previous)
                raise SessionNotFoundError(session_id)
        return handle

    async def ensure_handle(self, session_id: str) -> str | None:
        """Assign a handle on first attachment; never re-derives an existing one."""
        async with self._handle_lock:
            info = await self._sessions.get_session_info(session_id)
            if info is None:
                return None
            stored = info.config.get("relay_handle")
            if stored:
                if self.handles.get_session(stored) == session_id:
                    return stored
                if not self.handles.is_taken(stored, excluding_session=session_id):
                    await self.handles.set_handle(session_id, stored)
                    return stored
            handle = await self.handles.assign_default(session_id, info.name)
            if not await self._sessions.set_relay_handle(session_id, handle):
                self.handles.remove_session(session_id)  # session vanished mid-assignment
                return None
            return handle

    async def sync_session(self, session_id: str) -> str | None:
        """After a config write or at start: keep the index in step and assign a handle
        on first attachment (relay attached through any tier)."""
        info = await self._sessions.get_session_info(session_id)
        if info is None:
            return None
        try:
            if info.config.get("relay_handle") or (await self.effective_attachment(info))["relay_id"]:
                return await self.ensure_handle(session_id)
        except Exception:  # never block a session start on handle bookkeeping
            logger.exception("Relay handle sync failed for session %s", session_id)
        return None

    async def initialize(self) -> None:
        """Load relay assets and build the handle index from persisted sessions."""
        await self.relay_manager.load()
        self.handles.build(self._sessions.iter_relay_handles())

    async def validate_session_config(self, session_id: str, config: dict) -> dict:
        """Validate a whole-config replacement; returns the config to persist.

        Raises ValueError (format) or HandleConflictError (duplicate). A replacement that
        omits ``relay_handle`` / ``relay_channel_policies`` keeps the existing values.
        """
        validate_relay_config(config, "session")
        info = await self._require_session(session_id)
        handle = config.get("relay_handle")
        if handle is not None and self.handles.is_taken(handle, excluding_session=session_id):
            raise HandleConflictError(f"relay_handle '{handle}' is already in use")
        # Runtime-managed keys survive a whole-config save that doesn't mention them
        # (the settings UI does not know them yet); send an explicit value to change them.
        for key in ("relay_handle", "relay_channel_policies"):
            if key not in config and info.config.get(key):
                config = {**config, key: info.config[key]}
        return config

