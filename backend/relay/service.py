"""RelayService: attachment resolution, live policy merge, handle assignment (issue #2103).

Policies are read from live manager state on every call (never from the start-time
SessionConfig snapshot), so runtime edits need no restart.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from backend.relay_manager import RelayNotFoundError

from .adapters import get_spec
from .audit import RelayAuditLog
from .handles import HandleConflictError, RelayHandleIndex
from .policy import EffectivePolicy, diff_policies, merge_policies, normalize_policies
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


class RelayNotAttachedError(Exception):
    """The relay exists but is not the session's effective attached relay (409)."""


class RelayService:
    def __init__(
        self,
        relay_manager: RelayManager,
        handle_index: RelayHandleIndex,
        session_manager: SessionManager,
        template_manager: TemplateManager,
        profile_manager: ProfileManager,
        audit: RelayAuditLog | None = None,
    ) -> None:
        self.relay_manager = relay_manager
        self.handles = handle_index
        self._sessions = session_manager
        self._templates = template_manager
        self._profiles = profile_manager
        self._handle_lock = asyncio.Lock()  # index change + persistence stay atomic
        self._audit = audit
        # async (relay_id, channel_id) -> plain label or None; set by the builder (issue #2355)
        self.channel_namer = None
        self._audit_tasks: set[asyncio.Task] = set()
        self._audit_order = asyncio.Lock()  # FIFO: records land in the order edits happened
        # sync callbacks ``(session_id | None) -> None``: a session's effective policy / relay
        # attachment may have changed (None = every dependent session); monitors re-evaluate (3b-i)
        self.change_listeners: list = []

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

    def _sole_relay_id(self) -> str | None:
        ids = list(self.relay_manager.relays)
        return ids[0] if len(ids) == 1 else None

    async def effective_policies(
        self, session_info: SessionInfo
    ) -> dict[str, dict[str, EffectivePolicy]]:
        """Per-key merge of policies across profile < template < session, within each relay:
        ``{relay_id: {target: EffectivePolicy}}``. Legacy flat tiers are normalised on read."""
        profile_cfgs, template_cfg, session_cfg = await self._tier_configs(session_info)
        sole = self._sole_relay_id()

        def norm(cfg: dict) -> dict[str, dict[str, str]]:
            return normalize_policies(cfg.get("relay_channel_policies"), sole)

        profiles = [norm(c) for c in profile_cfgs]
        template, session = norm(template_cfg), norm(session_cfg)
        relay_ids = {r for tier in (*profiles, template, session) for r in tier}
        result: dict[str, dict[str, EffectivePolicy]] = {}
        for relay_id in relay_ids:
            record = self.relay_manager.relays.get(relay_id)
            spec = get_spec(getattr(record, "adapter_type", None))
            if spec is None:  # dangling relay_id (relay deleted): ignored, not pruned
                continue
            values = spec.policy_model.values
            result[relay_id] = merge_policies(
                [p.get(relay_id) for p in profiles], template.get(relay_id),
                session.get(relay_id), values.__contains__,
            )
        return result

    async def effective_relay_policies(
        self, session_info: SessionInfo, relay_id: str | None
    ) -> dict[str, EffectivePolicy]:
        """The effective ``{target: EffectivePolicy}`` for one relay (empty when unset)."""
        return (await self.effective_policies(session_info)).get(relay_id or "", {})

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
        relay_id = attachment["relay_id"]
        record = self.relay_manager.relays.get(relay_id) if relay_id else None
        policies = await self.effective_relay_policies(info, relay_id) if record else {}
        spec = get_spec(record.adapter_type) if record else None
        return {
            "session_id": session_id,
            "attached": bool(relay_id and record),
            "relay_id": relay_id,
            "relay_name": record.name if record else None,
            "adapter_type": record.adapter_type if record else None,
            "policy_model": spec.policy_model.describe() if spec else None,
            "relay_handle": info.config.get("relay_handle"),
            "footer_enabled": attachment["relay_footer_enabled"],
            "footer_template": attachment["relay_footer_template"],
            "sources": attachment["sources"],
            "policies": {
                cid: {"policy": p.policy, "source": p.source} for cid, p in policies.items()
            },
        }

    # --------------------------------------------------------------- summary

    async def compute_summary(self, info: SessionInfo) -> dict[str, Any] | None:
        """Runtime session summary (in memory only: no Slack calls); None when no relay."""
        relay_id = (await self.effective_attachment(info))["relay_id"]
        record = self.relay_manager.relays.get(relay_id) if relay_id else None
        if record is None:
            return None
        policies = await self.effective_relay_policies(info, relay_id)
        return {
            "relay_id": relay_id,
            "adapter_type": record.adapter_type,
            "handle": info.config.get("relay_handle"),
            "policy_count": len(policies),
        }

    async def refresh_summary(self, session_id: str) -> None:
        """Recompute and broadcast (on change) the session's ``relay_summary``."""
        try:
            info = await self._sessions.get_session_info(session_id)
            if info is not None:
                await self._sessions.set_relay_summary(
                    session_id, await self.compute_summary(info)
                )
        except Exception:  # a summary must never fail the edit that triggered it
            logger.exception("Relay summary refresh failed for session %s", session_id)

    async def refresh_all_summaries(self) -> None:
        try:
            infos = await self._sessions.list_sessions()
        except Exception:
            logger.exception("Relay summary refresh failed")
            return
        for info in infos:
            await self.refresh_summary(info.session_id)

    async def refresh_for_template(self, template_id: str) -> None:
        await self._refresh_for_templates({template_id})

    async def refresh_for_profile(self, profile_id: str) -> None:
        """Sessions whose template references the profile."""
        try:
            ids = {
                t.template_id for t in await self._templates.list_templates()
                if profile_id in (t.profile_ids or {}).values()
            }
        except Exception:
            logger.exception("Relay summary refresh failed for profile %s", profile_id)
            return
        await self._refresh_for_templates(ids)

    async def _refresh_for_templates(self, template_ids: set[str]) -> None:
        """One session scan for any number of templates; never fails the triggering save."""
        if not template_ids:
            return
        self._notify_changed(None)  # attachment / policies of dependent sessions may have changed
        try:
            infos = await self._sessions.list_sessions()
        except Exception:
            logger.exception("Relay summary refresh failed for templates")
            return
        for info in infos:
            if info.template_id in template_ids:
                await self.refresh_summary(info.session_id)

    # -------------------------------------------------------------- policies

    async def _require_attached_relay(self, session_id: str, relay_id: str):
        """(spec) for *relay_id* once it is known to be the session's attached relay."""
        info = await self._require_session(session_id)
        record = self.relay_manager.relays.get(relay_id)
        if record is None:
            raise RelayNotFoundError(relay_id)
        if (await self.effective_attachment(info))["relay_id"] != relay_id:
            raise RelayNotAttachedError(f"Relay {relay_id} is not attached to this session")
        spec = get_spec(record.adapter_type)
        if spec is None:
            raise ValueError(f"Unsupported adapter_type '{record.adapter_type}'")
        return spec

    # ---------------------------------------------------------- policy audit

    def attach_policy_audit(self, session_manager: SessionManager) -> None:
        """Route every audited session-tier policy write into the relay audit log (#2355)."""
        session_manager.add_policy_change_callback(self._on_policy_change, self._sole_relay_id)

    def _notify_changed(self, session_id: str | None) -> None:
        for listener in list(self.change_listeners):
            try:
                listener(session_id)
            except Exception:
                logger.exception("Relay change listener failed")

    def _on_policy_change(
        self, session_id: str, changes: list[dict], source: str, extra: dict | None
    ) -> None:
        """SessionManager callback (runs under the session lock): schedule only, never raise."""
        self._schedule_audit(self._audit_session_changes(session_id, changes, source, extra))
        self._notify_changed(session_id)

    def audit_tier_policy_change(
        self, tier: str, tier_id: str, tier_name: str | None, old_cfg: dict | None,
        new_cfg: dict | None,
    ) -> None:
        """Template/profile edit: one record per changed target with ``session_id`` null.
        Dependent sessions get no per-session record (deliberate: inherited, merged live)."""
        sole = self._sole_relay_id()
        changes = diff_policies(
            normalize_policies((old_cfg or {}).get("relay_channel_policies"), sole),
            normalize_policies((new_cfg or {}).get("relay_channel_policies"), sole),
        )
        if changes:
            self._schedule_audit(self._audit_changes(
                changes, source=tier,
                fields={"tier": tier, "tier_id": tier_id, "tier_name": tier_name},
            ))
            self._notify_changed(None)

    def _schedule_audit(self, coro) -> None:
        if self._audit is None:
            coro.close()
            return
        async def ordered() -> None:
            async with self._audit_order:
                await coro

        try:
            task = asyncio.get_running_loop().create_task(ordered())
        except RuntimeError:  # no running loop: nothing can be written without blocking
            coro.close()
            logger.warning("Relay policy audit skipped: no running event loop")
            return
        self._audit_tasks.add(task)
        task.add_done_callback(self._audit_tasks.discard)

    async def drain_audit(self) -> None:
        """Wait for scheduled audit writes (tests, shutdown)."""
        while self._audit_tasks:
            await asyncio.gather(*list(self._audit_tasks), return_exceptions=True)

    async def _audit_session_changes(
        self, session_id: str, changes: list[dict], source: str, extra: dict | None
    ) -> None:
        info = await self._sessions.get_session_info(session_id)
        fields: dict[str, Any] = {"session_id": session_id}
        if info is not None:
            fields["handle"] = info.config.get("relay_handle")
            fields["session_name"] = info.name
        if source != "prompt":  # prompt records keep their pre-#2355 shape plus ``source``
            fields["tier"] = "session"
        await self._audit_changes(
            changes, source=source, fields=fields, extra=extra, with_old=source != "prompt"
        )

    async def _audit_changes(
        self, changes: list[dict], *, source: str, fields: dict[str, Any],
        extra: dict | None = None, with_old: bool = True,
    ) -> None:
        try:
            for change in changes:
                record = {
                    **fields, **(extra or {}), "event": "policy_change", "outcome": "ok",
                    "channel_id": change["target"], "policy": change["new"], "source": source,
                }
                if with_old:
                    record["old_policy"] = change["old"]
                if "channel_name" not in record:
                    record["channel_name"] = await self._channel_name(
                        change["relay_id"], change["target"]
                    )
                await self._audit.record(change["relay_id"], **record)
        except Exception:  # an audit failure never affects the edit
            logger.exception("Relay policy audit failed")

    async def _channel_name(self, relay_id: str, channel_id: str) -> str | None:
        if self.channel_namer is None:
            return None
        try:
            return await self.channel_namer(relay_id, channel_id)
        except Exception:
            return None

    async def set_policy(
        self, session_id: str, relay_id: str, target: str, policy: str, *, source: str,
        audit_extra: dict[str, Any] | None = None,
    ) -> None:
        """Session-tier policy edit, validated by the relay's adapter model (ValueError -> 422)."""
        spec = await self._require_attached_relay(session_id, relay_id)
        spec.policy_model.validate_target(target)
        spec.policy_model.validate_value(policy)
        await self._sessions.update_relay_policy(
            session_id, relay_id, target, policy, source=source,
            sole_relay_id=self._sole_relay_id(), audit_extra=audit_extra,
        )
        await self.refresh_summary(session_id)

    async def delete_policy(
        self, session_id: str, relay_id: str, target: str, *, source: str
    ) -> None:
        spec = await self._require_attached_relay(session_id, relay_id)
        spec.policy_model.validate_target(target)
        await self._sessions.update_relay_policy(
            session_id, relay_id, target, None, source=source,
            sole_relay_id=self._sole_relay_id(),
        )
        await self.refresh_summary(session_id)

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
        await self.refresh_summary(session_id)
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
        handle = None
        try:
            if info.config.get("relay_handle") or (await self.effective_attachment(info))["relay_id"]:
                handle = await self.ensure_handle(session_id)
        except Exception:  # never block a session start on handle bookkeeping
            logger.exception("Relay handle sync failed for session %s", session_id)
        await self.refresh_summary(session_id)
        self._notify_changed(session_id)  # attachment / config may have changed
        return handle

    async def initialize(self) -> None:
        """Load relay assets and build the handle index from persisted sessions."""
        await self.relay_manager.load()
        self.handles.build(self._sessions.iter_relay_handles())
        await self.refresh_all_summaries()

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

