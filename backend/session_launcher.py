"""
Session Launcher for Claude Code WebUI

Extracted from SessionCoordinator (issue #2189, epic #1990 stage 7
`coordinator-split`, PR 1): owns the session launch/teardown cluster
(start/terminate/restart/reset, MCP config resolution, secret placeholder
substitution). This is a mechanical extraction — SessionCoordinator keeps every
public method as a one-line delegate, and every per-session shared dict stays
constructed on the coordinator; SessionLauncher receives the same dict objects
by reference, never copies.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from backend.docker_utils import cleanup_session_tmp, get_session_tmp_dir
from backend.legion.minion_system_prompts import get_legion_guide_only
from shared.logging_config import get_logger

from .claude_sdk import ClaudeSDK
from .config_resolution import resolve_effective_config
from .data_storage import DataStorageManager
from .hooks.pretooluse_handler import InternalPermissionHandler
from .litellm_proxy_manager import make_model_alias
from .mcp_config_manager import McpServerType
from .oauth_refresh_manager import OAuthRefreshManager
from .session_manager import SessionState
from .session_recorder import SessionRecorder
from .task_utils import task_done_log_exception

if TYPE_CHECKING:
    from .models.messages import ToolCall

# Get specialized logger for launcher actions (same category as the coordinator's
# own coord_logger — this is still "coordinator actions" from an operator's
# perspective, just physically relocated).
coord_logger = get_logger('coordinator', category='COORDINATOR')
logger = logging.getLogger(__name__)

# Issue #1375: Regex for ${secret:<name>} references in MCP server header values.
_SECRET_REF_RE = re.compile(r"\$\{secret:([^}]+)\}")

# Issue #1982: max time start_session() waits for a shared-connection MCP server's
# first open attempt before proceeding degraded. Deliberately shorter than the
# shared-connection manager's own internal open timeout — a black-holed host
# can't stall session start, but a still-in-flight open isn't cancelled and may
# still succeed in time for the session's first tool call.
STARTUP_ATTACH_TIMEOUT_SECONDS = 8.0


@dataclass
class LauncherCallbacks:
    """Bound-method references into SessionCoordinator logic not yet extracted
    into their own modules (message-pipeline / tool-call-tracker clusters —
    PR 2/3 of issue #2189's coordinator-split stage).

    A narrow, documented interface rather than a back-reference to the whole
    coordinator: when those methods physically move in a later PR, only the
    wiring in SessionCoordinator.__init__ changes, not SessionLauncher's
    internals.
    """

    create_message_callback: Callable[[str], Callable]
    create_error_callback: Callable[[str], Callable]
    create_stderr_callback: Callable[[str], Callable]
    create_phase_callback: Callable[[str], Callable]
    create_auto_approval_callback: Callable[[str], Callable]
    on_rate_limits: Callable[[Any], Any]
    transition_to_error: Callable[..., Any]
    send_session_failure_message: Callable[..., Any]
    send_client_launched_message: Callable[[str], Any]
    send_mcp_degraded_message: Callable[[str, list], Any]
    result_error_structured_kwargs: Callable[[Any], dict]
    extract_claude_cli_error: Callable[..., str]
    mark_session_tools_interrupted: Callable[[str], "list[ToolCall]"]
    notify_session_reset: Callable[[str], Any]


class SessionLauncher:
    """Owns session launch/teardown: start/terminate/restart/reset, MCP config
    resolution, and secret placeholder substitution for SDK sessions.
    """

    def __init__(
        self,
        *,
        coordinator: Any,
        callbacks: LauncherCallbacks,
    ):
        # Issue #2189: every collaborator manager and shared per-session dict is a
        # LIVE property lookup against the coordinator (below), not a value snapshotted
        # here at construction time. A snapshot would go stale the moment any of them
        # is reassigned on the coordinator after construction — which happens for real:
        # BackendApp assigns `coordinator.litellm_proxy_manager` only after the
        # coordinator (and this launcher) already exist, several tests reassign
        # `coordinator.session_manager`/`.project_manager` with a mock mid-test, and at
        # least one test reassigns `coordinator._emitted_turn_ids` to a whole new dict
        # object. A back-reference + properties makes every one of those cases resolve
        # correctly without having to special-case each one individually.
        self._coordinator = coordinator

        # Narrow callback bundle into not-yet-extracted coordinator logic.
        self._callbacks = callbacks

        # SDK factory for dependency injection (enables MockClaudeSDK for testing).
        # Owned here now; SessionCoordinator exposes a property + set_sdk_factory()
        # delegate so existing direct-attribute test pokes keep working unchanged.
        self._sdk_factory = self._default_sdk_factory

    # --- Collaborator managers: live passthrough to the coordinator ----------
    session_manager = property(lambda self: self._coordinator.session_manager)
    template_manager = property(lambda self: self._coordinator.template_manager)
    profile_manager = property(lambda self: self._coordinator.profile_manager)
    credential_vault = property(lambda self: self._coordinator.credential_vault)
    mcp_config_manager = property(lambda self: self._coordinator.mcp_config_manager)
    hook_config_manager = property(lambda self: self._coordinator.hook_config_manager)
    oauth_manager = property(lambda self: self._coordinator.oauth_manager)
    oauth_refresh_manager = property(lambda self: self._coordinator.oauth_refresh_manager)
    shared_mcp_manager = property(lambda self: self._coordinator.shared_mcp_manager)
    legion_system = property(lambda self: self._coordinator.legion_system)
    resource_mcp_tools = property(lambda self: self._coordinator.resource_mcp_tools)
    links_mcp_tools = property(lambda self: self._coordinator.links_mcp_tools)
    queue_processor = property(lambda self: self._coordinator.queue_processor)
    litellm_proxy_manager = property(lambda self: self._coordinator.litellm_proxy_manager)
    project_manager = property(lambda self: self._coordinator.project_manager)
    _watchdog = property(lambda self: self._coordinator._watchdog)
    experimental = property(lambda self: self._coordinator.experimental)
    session_recording_enabled = property(lambda self: self._coordinator.session_recording_enabled)
    analytics_store = property(lambda self: self._coordinator.analytics_store)

    # Injected coordinator methods — never reimplemented here.
    get_or_create_storage_manager = property(lambda self: self._coordinator.get_or_create_storage_manager)
    _apply_audit_writer = property(lambda self: self._coordinator._apply_audit_writer)

    # --- Shared per-session state: live passthrough to the coordinator -------
    # Constructed on SessionCoordinator, read here from the SAME dict objects
    # (never copied) — also mutated by code staying on the coordinator
    # (message-pipeline closures, delete_session, resource_mcp_tools.py's direct
    # _active_sdks reach-in). A property (not a snapshotted reference) also
    # covers the rarer case of the dict itself being reassigned wholesale.
    _active_sdks = property(lambda self: self._coordinator._active_sdks)
    _storage_managers = property(lambda self: self._coordinator._storage_managers)
    _session_recorders = property(lambda self: self._coordinator._session_recorders)
    _usage_baseline_by_session = property(lambda self: self._coordinator._usage_baseline_by_session)
    _turn_seq_by_session = property(lambda self: self._coordinator._turn_seq_by_session)
    _last_turn_model_by_session = property(lambda self: self._coordinator._last_turn_model_by_session)
    _subagent_usage_by_session = property(lambda self: self._coordinator._subagent_usage_by_session)
    _message_callbacks = property(lambda self: self._coordinator._message_callbacks)
    _error_callbacks = property(lambda self: self._coordinator._error_callbacks)
    _retry_sequences = property(lambda self: self._coordinator._retry_sequences)
    _tool_call_events = property(lambda self: self._coordinator._tool_call_events)
    _message_emitted_events = property(lambda self: self._coordinator._message_emitted_events)
    _emitted_turn_ids = property(lambda self: self._coordinator._emitted_turn_ids)
    _task_leg_registries = property(lambda self: self._coordinator._task_leg_registries)

    @staticmethod
    def _default_sdk_factory(session_id, working_directory, **kwargs):
        """Default factory that strips session_name before calling ClaudeSDK."""
        kwargs.pop("session_name", None)
        return ClaudeSDK(session_id=session_id, working_directory=working_directory, **kwargs)

    def set_sdk_factory(self, factory):
        """Set custom SDK factory for testing (e.g., MockClaudeSDK)."""
        self._sdk_factory = factory

    def get_session_recorder(self, session_id: str) -> SessionRecorder | None:
        """Issue #1998: the active SessionRecorder for a session, or None if it isn't
        recording. Used by EventQueue's on_append hook and PermissionService's pause
        lifecycle recording — both live outside start_session()'s own creation path.
        """
        return self._session_recorders.get(session_id)

    async def _get_mcp_sdk_config(
        self, mcp_cfg, name_to_placeholder: "dict[str, str] | None" = None
    ) -> tuple[dict, str | None]:
        """Return (SDK config, unavailable_reason) for an MCP server.

        unavailable_reason is None on success. Injects an OAuth Bearer token when
        applicable for HTTP/SSE servers with oauth_enabled=True, reading the stored
        encrypted token so OAuth-authenticated servers always have fresh credentials.

        Issue #976: Proactively refreshes tokens that are expired or expiring within
        _OAUTH_REFRESH_BUFFER_SECONDS. On refresh failure the original token is still
        injected (if present) so mid-session MCP calls have the best available credentials.

        Issue #1375: name_to_placeholder maps secret name → CC_SECRET_* placeholder.
        After OAuth injection, any ${secret:<name>} references in header values are
        replaced with the corresponding placeholder for HTTP/SSE servers.

        Issue #1982: for shared_connection configs, the upstream open is bounded by
        STARTUP_ATTACH_TIMEOUT_SECONDS and never raises — a broken/slow server
        returns the proxy config anyway (still attachable; a later reconnect or
        token refresh may bring it up) along with a human-readable reason string.
        """
        # Issue #1484: route shared-connection configs through the in-process proxy.
        if getattr(mcp_cfg, "shared_connection", False):
            from backend.mcp.proxy_server_factory import build_proxy_server
            from backend.mcp.shared_connection_manager import _describe_open_failure

            reason: str | None = None
            try:
                # Shielded: a timeout here abandons only our own wait, not the
                # underlying get_or_open() attempt — it keeps running in the
                # background so a slow-but-successful open still lands (and
                # correctly finishes its own refcount bookkeeping) rather than
                # being cut off mid-open.
                await asyncio.wait_for(
                    asyncio.shield(self.shared_mcp_manager.get_or_open(mcp_cfg)),
                    timeout=STARTUP_ATTACH_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                reason = "timed out connecting"
            except Exception as exc:
                reason = _describe_open_failure(exc)
            return build_proxy_server(mcp_cfg, self.shared_mcp_manager), reason

        import time as _time

        config = mcp_cfg.to_sdk_config()
        # Issue #1425: detect ${secret:...} placeholder in Authorization header before OAuth injection.
        orig_headers = config.get("headers") or {}
        orig_auth = orig_headers.get("Authorization", "")
        auth_has_placeholder = bool(_SECRET_REF_RE.search(orig_auth))
        if mcp_cfg.oauth_enabled and mcp_cfg.type in (McpServerType.SSE, McpServerType.HTTP):
            if auth_has_placeholder:
                coord_logger.info(
                    "[MCP config] server=%s oauth_enabled=True but Authorization header "
                    "contains ${secret:...} — preserving placeholder, proxy will inject from vault.",
                    mcp_cfg.id,
                )
            else:
                try:
                    token = await self.oauth_manager.get_stored_token(mcp_cfg.id)
                    if token:
                        store = self.oauth_manager.get_token_store(mcp_cfg.id)
                        expiry = await store.get_token_expiry()
                        now = _time.time()

                        # Issue #976: Attempt refresh if token is expired or expiring soon
                        if expiry is not None and expiry < (now + OAuthRefreshManager.REFRESH_BUFFER_SECONDS):
                            seconds_until_expiry = expiry - now
                            if seconds_until_expiry < 0:
                                logger.warning(
                                    "OAuth token for MCP server %s expired %.0f s ago — attempting refresh.",
                                    mcp_cfg.id,
                                    -seconds_until_expiry,
                                )
                            else:
                                logger.info(
                                    "OAuth token for MCP server %s expires in %.0f s — refreshing proactively.",
                                    mcp_cfg.id,
                                    seconds_until_expiry,
                                )
                            new_token = await self.oauth_refresh_manager.refresh_token(mcp_cfg.id)
                            if new_token:
                                token = new_token
                            else:
                                logger.warning(
                                    "OAuth token refresh failed for MCP server %s. "
                                    "Injecting best available token — re-authenticate via the UI if calls fail.",
                                    mcp_cfg.id,
                                )

                        headers = dict(config.get("headers") or {})
                        headers["Authorization"] = f"Bearer {token.access_token}"
                        config["headers"] = headers
                        coord_logger.debug(
                            "[MCP config] server=%s oauth=True token_present=True expiry=%s",
                            mcp_cfg.id,
                            int(expiry) if expiry is not None else None,
                        )
                    else:
                        coord_logger.debug(
                            "[MCP config] server=%s oauth=True token=None (not authenticated) config=%s",
                            mcp_cfg.id,
                            config,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to inject OAuth token for MCP server %s: %s", mcp_cfg.id, exc
                    )
        else:
            coord_logger.debug(
                "[MCP config] server=%s oauth=False config=%s", mcp_cfg.id, config
            )
        # Issue #1375: Substitute ${secret:<name>} references in HTTP/SSE header values.
        if name_to_placeholder and mcp_cfg.type in (McpServerType.SSE, McpServerType.HTTP):
            headers = dict(config.get("headers") or {})
            if headers:
                headers = self._substitute_secret_refs(headers, name_to_placeholder, mcp_cfg.name)
                config["headers"] = headers
        return config, None

    async def _generate_secret_placeholders(
        self,
        session_id: str,
        session_info: Any,
        resolved_metas: list[dict],
    ) -> dict[str, str]:
        """Generate CC_SECRET_* placeholders for assigned secrets and persist them.

        Returns name_to_placeholder (name → placeholder) for MCP header substitution.
        Stores the inverse (placeholder → name) on session_info.secret_placeholders.
        """
        new_placeholders: dict[str, str] = {}
        name_to_placeholder: dict[str, str] = {}
        for secret in resolved_metas:
            name = secret.get("name", "unnamed")
            placeholder = f"CC_SECRET_{name}_{secrets.token_hex(4)}"
            new_placeholders[placeholder] = name
            name_to_placeholder[name] = placeholder
        session_info.secret_placeholders = new_placeholders
        await self.session_manager._persist_session_state(session_id)
        coord_logger.info(
            "Generated %d placeholder(s) for session %s: names=%s",
            len(new_placeholders),
            session_id,
            list(new_placeholders.values()),
        )
        return name_to_placeholder

    def _substitute_secret_refs(
        self,
        headers: dict[str, str],
        name_to_placeholder: dict[str, str],
        mcp_server_name: str,
    ) -> dict[str, str]:
        """Replace ${secret:<name>} in header values with session-scoped CC_SECRET_* placeholders.

        Raises ValueError (fail closed) when a referenced secret is not assigned/known.
        Header values with no references are passed through unchanged.
        """
        out: dict[str, str] = {}
        for hname, hval in headers.items():
            new_val = hval
            for match in _SECRET_REF_RE.finditer(hval):
                name = match.group(1).strip()
                placeholder = name_to_placeholder.get(name)
                if not placeholder:
                    raise ValueError(
                        f"MCP server '{mcp_server_name}' header '{hname}' references "
                        f"secret '{name}' which is not assigned to this session "
                        f"(or does not exist in the vault)."
                    )
                new_val = new_val.replace(match.group(0), placeholder)
            out[hname] = new_val
        return out

    async def start_session(
        self,
        session_id: str,
        permission_callback: Callable[[str, dict[str, Any]], bool | dict[str, Any]] | None = None,
        _lifecycle_action: str = "start",
    ) -> bool:
        """Start a session with SDK integration.

        Args:
            _lifecycle_action: Issue #1998 — the raw-log lifecycle label recorded when a
                SessionRecorder is active. restart_session()/reset_session() both funnel
                through this method to actually (re)start the SDK, so they pass "restart"/
                "reset" here rather than this method guessing which caller invoked it.
        """
        # Issue #2189 (coordinator-split PR 1): these three module-level helpers stay
        # defined in session_coordinator.py — _all_tier_fields_set is also used there by
        # _resolve_analytics_model_label (PR 2 territory), so splitting it across modules
        # would duplicate logic. Deferred import avoids the circular top-level import
        # (session_coordinator.py imports SessionLauncher at module scope).
        from .session_coordinator import _all_tier_fields_set, _build_model_map, _render_inject_file

        try:
            # Start session through session manager — fires the "start"/"restart"/
            # "reset" lifecycle-record callback itself now (issue #2184), replacing
            # the manual recorder.record_lifecycle(_lifecycle_action) call this method
            # used to make once the recorder was resolved below.
            if not await self.session_manager.start_session(session_id, action=_lifecycle_action):
                return False

            # Defensively reset processing state on session start
            await self.session_manager.end_turn(session_id)

            # Check if SDK exists and is running
            sdk = self._active_sdks.get(session_id)
            if sdk and sdk.is_running():
                # logger.info(f"Session {session_id} is already running, skipping start - NO CLIENT_LAUNCHED MESSAGE")
                return True

            # We need to create or recreate the SDK in either case:
            # - No SDK exists: Create new SDK
            # - SDK exists but not running: Recreate SDK to restart it
            sdk_was_created = True

            # if not sdk:
            #     logger.info(f"No SDK found for session {session_id} - CREATING NEW SDK")
            # else:
            #     logger.info(f"SDK exists but not running for session {session_id} - RECREATING SDK")

            # Get session info to create/recreate SDK with same parameters
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                logger.error(f"No session info found for {session_id}")
                return False

            # Issue #349: is_minion field removed - all sessions are minions
            # Ensure role is set for sessions without one
            if not session_info.role:
                session_info.role = "assistant"
                await self.session_manager._persist_session_state(session_id)
                logger.info(f"Set default role for session {session_id}: {session_info.role}")

            # Issue #827: Generate per-session secrets fetch token if not present.
            # Used by proxy sidecar to call GET /api/sessions/{id}/secrets/resolve.
            if not session_info.secret_fetch_token:
                session_info.secret_fetch_token = secrets.token_urlsafe(32)
                await self.session_manager._persist_session_state(session_id)

            # Create storage manager — reuse a cached instance if one exists rather
            # than unconditionally constructing a fresh one (issue #2084 stage 3-C: a
            # fresh instance here would orphan the `_write_lock` a concurrent
            # background/on-demand migration might already be holding for this exact
            # session — see get_or_create_storage_manager()'s docstring — silently
            # defeating the lock's entire coordination purpose). Safe to reuse:
            # DataStorageManager holds no other per-lifecycle state, and
            # _apply_audit_writer() below is idempotent (issue #1172).
            storage_manager = await self.get_or_create_storage_manager(session_id)
            session_dir = storage_manager.session_dir
            self._apply_audit_writer(storage_manager, session_id)

            # Issue #1059: Resolve effective config EARLY — before any CONFIG_FIELD read.
            # For template-linked sessions, session_info CONFIG_FIELDS are at dataclass defaults;
            # all real values come from resolve_effective_config().
            effective_config = await resolve_effective_config(
                session_info, self.template_manager, self.profile_manager
            )

            # Issue #1998: create/reuse the SessionRecorder for this session. Gated on the
            # persisted per-session opt-in AND the Backend-wide --enable-session-recording
            # flag — a session configured with recording_enabled=True before a later
            # flag-less Backend restart just goes inert (no recorder, no error), the same
            # posture as docker_enabled going inert if Docker becomes unavailable. Also
            # never constructed for a Docker-isolated session (defense-in-depth alongside
            # the config-save rejection in routers/sessions.py — never silently partial).
            # Reused across restart/reset (never recreated) so a scenario spanning a
            # restart keeps appending to the same raw_log.jsonl; only dropped in
            # terminate_session().
            recorder: SessionRecorder | None = self._session_recorders.get(session_id)
            if (
                recorder is None
                and self.session_recording_enabled
                and effective_config.recording_enabled
                and not effective_config.docker_enabled
            ):
                recorder = SessionRecorder(session_id, session_dir)
                self._session_recorders[session_id] = recorder
                # Issue #2184: this recorder didn't exist yet when
                # session_manager.start_session() fired its lifecycle-record hook
                # above (self._on_session_manager_lifecycle_record), so that hook had
                # nothing to record into — capture the missed event now that the
                # recorder exists. restart_session()/reset_session() reuse an
                # already-existing recorder (the hook already recorded those
                # correctly), so this only ever fires once per session, on its very
                # first start.
                try:
                    recorder.record_lifecycle(_lifecycle_action)
                except Exception:
                    logger.exception(
                        f"Recorder failed to capture lifecycle event for session {session_id} "
                        "— continuing session start regardless"
                    )

            # Issue #917: Resolve template variables in path-typed CONFIG_FIELDS.
            # Apply to effective_config (single source of truth for CONFIG_FIELDS).
            from backend.template_variables import build_variables, resolve_path, resolve_path_list
            _tv = build_variables(session_id, session_dir, session_info.working_directory)
            _ec_path_updates: dict[str, Any] = {}
            if effective_config.auto_memory_directory:
                _ec_path_updates["auto_memory_directory"] = resolve_path(
                    effective_config.auto_memory_directory, _tv
                )
            if effective_config.additional_directories:
                _ec_path_updates["additional_directories"] = resolve_path_list(
                    effective_config.additional_directories, _tv
                )
            if effective_config.docker_extra_mounts:
                _ec_path_updates["docker_extra_mounts"] = resolve_path_list(
                    effective_config.docker_extra_mounts, _tv
                )
            if _ec_path_updates:
                effective_config = effective_config.model_copy(update=_ec_path_updates)

            # Check if we have a valid Claude Code session ID to resume.
            # Issue #2159: pass the actual Claude Code session id directly — ClaudeSDK
            # no longer reaches back into session_manager.get_session_info() itself to
            # resolve it (that reach-in is gone from claude_sdk.py's start()).
            resume_sdk_session = session_info.claude_code_session_id or None

            # Issue #1375: Resolve secret placeholders early so MCP header substitution
            # can use them before the mcp_servers dict is built.
            name_to_placeholder: dict[str, str] = {}
            resolved_metas: list[dict] = []
            if effective_config.assigned_secrets:
                resolved_metas = await self.credential_vault.resolve_secrets_for_assignment(
                    effective_config.assigned_secrets
                )
                name_to_placeholder = await self._generate_secret_placeholders(
                    session_id, session_info, resolved_metas
                )

            # Issue #313: Attach MCP tools based on can_spawn_minions flag (universal Legion)
            # All sessions with can_spawn_minions=True get Legion MCP tools
            mcp_servers = {}
            mcp_tools_list = []
            # Issue #1982: (name, reason) for shared-connection servers whose start-time
            # open attempt failed. The server stays attached either way — only reported.
            unavailable_mcp_servers: list[tuple[str, str]] = []
            if session_info.can_spawn_minions and self.legion_system and self.legion_system.mcp_tools:
                # Create session-specific MCP server (injects session_id into tool calls)
                mcp_server = self.legion_system.mcp_tools.create_mcp_server_for_session(session_id)
                if mcp_server:
                    # SDK expects dict mapping server name to config, not a list
                    mcp_servers["legion"] = mcp_server
                    # Allow all legion MCP tools (reduces command-line length)
                    mcp_tools_list.append("mcp__legion")
                    coord_logger.info(f"Attaching Legion MCP tools to session {session_id} (can_spawn_minions=True)")

            # Issue #404: Attach resource MCP tools to all sessions
            if self.resource_mcp_tools:
                resource_mcp_server = self.resource_mcp_tools.create_mcp_server_for_session(session_id)
                if resource_mcp_server:
                    mcp_servers["resources"] = resource_mcp_server
                    mcp_tools_list.append("mcp__resources")
                    coord_logger.info(f"Attaching Resource MCP tools to session {session_id}")

            # Issue #1530: Attach links MCP tools to all sessions
            if self.links_mcp_tools:
                links_mcp_server = self.links_mcp_tools.create_mcp_server_for_session(session_id)
                if links_mcp_server:
                    mcp_servers["links"] = links_mcp_server
                    mcp_tools_list.append("mcp__links")
                    coord_logger.info(f"Attaching Links MCP tools to session {session_id}")

            # Issue #676: Attach user-selected global MCP server configs
            if effective_config.mcp_server_ids:
                selected_configs = self.mcp_config_manager.get_configs_by_ids(
                    effective_config.mcp_server_ids
                )
                # Issue #1375: Fail closed if ${secret:...} refs exist but proxy is disabled.
                # Issue #1484: shared_connection configs resolve secrets in-process via
                # SharedMcpConnectionManager — they never reach the session's MCP client,
                # so they are exempt from this proxy requirement.
                has_secret_refs = any(
                    _SECRET_REF_RE.search(v or "")
                    for cfg in selected_configs
                    if cfg.type in (McpServerType.SSE, McpServerType.HTTP)
                    and not getattr(cfg, "shared_connection", False)
                    for v in (cfg.headers or {}).values()
                )
                if has_secret_refs and not effective_config.docker_proxy_enabled:
                    raise ValueError(
                        "MCP header secret references (${secret:...}) require docker_proxy_enabled=True. "
                        "Enable the proxy sidecar in session/profile/template config, or remove the references."
                    )
                for mcp_cfg in selected_configs:
                    sdk_config, unavailable_reason = await self._get_mcp_sdk_config(
                        mcp_cfg, name_to_placeholder
                    )
                    mcp_servers[mcp_cfg.slug] = sdk_config
                    mcp_tools_list.append(f"mcp__{mcp_cfg.slug}")
                    if unavailable_reason is not None:
                        unavailable_mcp_servers.append((mcp_cfg.name, unavailable_reason))
                        coord_logger.warning(
                            f"MCP server '{mcp_cfg.name}' unavailable at start for "
                            f"session {session_id}: {unavailable_reason}"
                        )
                    else:
                        coord_logger.info(
                            f"Attaching global MCP server '{mcp_cfg.name}' to session {session_id}"
                        )

            coord_logger.debug(
                "[MCP launch] session=%s servers=%s", session_id, list(mcp_servers.keys())
            )

            # Issue #1629: Attach user-selected global hook configs. Purely additive
            # against any disk-based .claude/settings.json hooks — bare_mode (which
            # disables hooks entirely) and setting_sources handling live in ClaudeSDK's
            # _get_sdk_options(); this code path never reads or modifies setting_sources.
            hooks_settings: dict | None = None
            if effective_config.hook_ids:
                selected_hook_configs = self.hook_config_manager.get_configs_by_ids(
                    effective_config.hook_ids
                )
                hooks_settings = self.hook_config_manager.to_sdk_hooks_payload(
                    selected_hook_configs
                ) or None
                if hooks_settings:
                    coord_logger.info(
                        f"Attaching {len(selected_hook_configs)} hook config(s) to session {session_id}"
                    )

            # Merge MCP tools with effective allowed_tools
            all_tools = effective_config.allowed_tools if effective_config.allowed_tools else []
            all_tools = list(set(all_tools + mcp_tools_list))  # Deduplicate

            # Issue #349: All sessions are minions - always prepend legion guide
            legion_guide = get_legion_guide_only()

            # Issue #691: Append session history reference so agents can check past context
            # Issue #710: Skip history reference when knowledge management is disabled
            if effective_config.history_distillation_enabled:
                history_dir = session_dir / "history"
                history_note = (
                    f"\n\n## Session History\n"
                    f"Distilled history from previous conversations is available at "
                    f"`{history_dir}/` (read-only). Before answering questions about past "
                    f"context, identity, or decisions, check this folder for relevant "
                    f"archived conversations.\n"
                    f"Use Read, Glob, or Grep to access history files. "
                    f"Do NOT use Bash (e.g. `ls`, `cat`) for history access."
                )
            else:
                history_note = ""

            # Issue #749: Skill creating context
            if effective_config.skill_creating_enabled:
                skill_creating_note = (
                    "\n\n## Skill Creation\n"
                    "You can create custom skills to automate repetitive workflows. "
                    "Use `/skill-maker` to get guided through writing a SKILL.md file. "
                    "Skills MUST be created in your working directory at "
                    "`<working-directory>/.claude/skills/{name}/SKILL.md` — "
                    "NOT in `~/.claude/skills/` (that is for system-managed global skills). "
                    "Skills are hot-reloaded and available immediately after writing. "
                    "If a skill doesn't seem to work correctly, call `restart_session` to fully reload.\n"
                )
            else:
                skill_creating_note = ""

            if effective_config.system_prompt:
                minion_system_prompt = f"{legion_guide}{history_note}{skill_creating_note}\n\n---\n\n{effective_config.system_prompt}"
            else:
                minion_system_prompt = f"{legion_guide}{history_note}{skill_creating_note}"
            coord_logger.info(f"Built minion system prompt for start (guide + context): {len(minion_system_prompt)} chars")

            # Escape special characters in system_prompt for subprocess command-line safety
            # CRITICAL: Newlines break subprocess argument parsing on Windows
            if minion_system_prompt:
                # Replace literal newlines with escaped newlines for command-line
                escaped_prompt = minion_system_prompt.replace('\n', '\\n').replace('\r', '\\r')
                # Also escape other problematic characters
                escaped_prompt = escaped_prompt.replace('"', '\\"').replace('$', '\\$')
                coord_logger.info(f"Escaped system prompt: {len(escaped_prompt)} chars (original: {len(minion_system_prompt)})")
                minion_system_prompt = escaped_prompt

            # Issue #496: Auto-resolve cli_path when Docker mode is enabled
            effective_cli_path = effective_config.cli_path
            # Issue #1672: process_wrapper is a host-path launcher; unreachable from inside
            # the container, so force it off whenever Docker isolation is active.
            effective_process_wrapper = (
                None if effective_config.docker_enabled else effective_config.process_wrapper
            )
            docker_env_vars = {}
            if effective_config.docker_enabled and not effective_config.cli_path:
                from backend.docker_utils import get_session_tmp_dir, resolve_docker_cli_path
                # Persistent data dir for Claude session transcripts (enables --resume).
                # Nested inside session_dir so Claude CLI internals and WebUI session data
                # are created, backed up, and cleaned up together.
                docker_data_dir = str(session_dir / "docker_claude_data")
                # Issue #759: Mount session memory dir into Docker container (RW, at host path)
                extra_mounts = list(effective_config.docker_extra_mounts or [])
                # Issue #1179: Proxy-sidecar-only mounts (session_token, session_id).
                # Agent mounts go in extra_mounts; proxy mounts go here.
                proxy_extra_mounts: list[str] = []
                if effective_config.auto_memory_mode == "session":
                    memory_dir = session_dir / "memory"
                    memory_dir.mkdir(exist_ok=True)
                    extra_mounts.append(f"{memory_dir}:{memory_dir}")
                elif effective_config.auto_memory_mode == "claude" and effective_config.auto_memory_directory:
                    native_mem = Path(effective_config.auto_memory_directory)
                    native_mem.mkdir(parents=True, exist_ok=True)
                    extra_mounts.append(f"{native_mem}:{native_mem}")
                # Issue #773: Mount session data dirs into Docker (read-only)
                # These host-side dirs contain files the agent needs to Read:
                #   resources/   - MCP-registered resources, comm file attachments
                #   attachments/ - User-uploaded files via InputArea
                #   history/     - Distilled history .md files (issue #691)
                # Mounted at the same absolute path so paths work identically.
                for subdir in ("resources", "attachments", "history"):
                    host_dir = session_dir / subdir
                    host_dir.mkdir(exist_ok=True)
                    extra_mounts.append(f"{host_dir}:{host_dir}:ro")

                # Issue #759: Mount synced skills into Docker container (read-only)
                # Mount the real skills dir (not the symlink dir) to avoid broken
                # symlinks inside the container.
                from backend.skill_manager import NEW_GLOBAL_SKILLS_DIR
                docker_home = effective_config.docker_home_directory or "/home/claude"
                if NEW_GLOBAL_SKILLS_DIR.exists():
                    extra_mounts.append(
                        f"{NEW_GLOBAL_SKILLS_DIR}:{docker_home}/.claude/skills:ro"
                    )
                # Issue #820: Mount session-specific /tmp dir so container /tmp is host-accessible
                # IMPORTANT: mkdir must happen BEFORE adding to extra_mounts — Docker bind-mount
                # fails at container start if the host source path does not already exist.
                tmp_dir = get_session_tmp_dir(session_dir)
                tmp_dir.mkdir(exist_ok=True)
                extra_mounts.append(f"{tmp_dir}:/tmp")

                # Issue #1134: Typed-secret placeholder injection.
                # The proxy fetches secrets via REST (GET /api/sessions/{id}/secrets/resolve)
                # using the per-session Bearer token. Placeholders are stored on SessionInfo
                # and passed to the agent container via env vars / mounted files.
                proxy_allowlist_file = None
                delivery_envs: dict[str, str] = {}  # env var name → placeholder string
                _ssh_key_prepared = False  # Issue #1052: two-dir SSH isolation flag
                _ssh_key_dir: Path | None = None
                _ssh_shared_dir: Path | None = None

                if effective_config.docker_proxy_enabled:
                    # Build dynamic allowlist: static defaults + config domains
                    effective_domains: set[str] = set()
                    static_allowlist_path = Path("backend/docker/proxy/allowlist.json")
                    if static_allowlist_path.exists():
                        try:
                            static = json.loads(static_allowlist_path.read_text())
                            effective_domains.update(static.get("domains", []))
                        except Exception:
                            coord_logger.warning("Failed to read static proxy allowlist")
                    if effective_config.docker_proxy_allowlist_domains:
                        effective_domains.update(effective_config.docker_proxy_allowlist_domains)
                    if effective_domains:
                        allowlist_path = tmp_dir / "allowlist.json"
                        allowlist_path.write_text(json.dumps({"domains": sorted(effective_domains)}))
                        proxy_allowlist_file = str(allowlist_path)
                        coord_logger.info(
                            f"Built proxy allowlist for session {session_id}: "
                            f"{len(effective_domains)} domains"
                        )

                    # Issue #1134 / #1375: Secret delivery — placeholders were already
                    # generated early (before mcp_servers build). Reuse them here to
                    # populate inject_env / inject_file delivery channels.
                    if resolved_metas:
                        for secret in resolved_metas:
                            name = secret.get("name", "unnamed")
                            placeholder = name_to_placeholder.get(name)
                            if not placeholder:
                                continue

                            # inject_env: deliver placeholder via environment variable
                            inject_env = secret.get("inject_env")
                            if inject_env:
                                delivery_envs[inject_env] = placeholder

                            # inject_file: render placeholder into a mounted file
                            inject_file = secret.get("inject_file")
                            if inject_file and not (secret.get("type") == "ssh_key" and effective_config.docker_proxy_enabled):
                                file_path = inject_file.get("path")
                                if file_path:
                                    fmt = inject_file.get("format", "raw")
                                    key_path = inject_file.get("key_path")
                                    perms_str = inject_file.get("permissions", "0600")
                                    content = _render_inject_file(placeholder, fmt, key_path)
                                    safe_fname = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
                                    host_file = tmp_dir / f"delivery_{safe_fname}"
                                    host_file.write_text(content)
                                    os.chmod(host_file, int(perms_str, 8))
                                    extra_mounts.append(f"{host_file}:{file_path}:ro")

                        # Issue #1052: SSH key isolation via two-dir design.
                        # key_dir  → proxy-only mount at /run/ssh-private:ro (key bytes
                        #            never reach the agent; proxy wipes the file after ssh-add).
                        # shared_dir → both containers at /run/ssh (socket created by proxy
                        #              ssh-agent; agent uses socket, not key file).
                        from backend.docker_utils import prepare_session_ssh  # noqa: PLC0415
                        _ssh_key_dir = tmp_dir / "ssh-key"
                        _ssh_shared_dir = tmp_dir / "ssh-shared"
                        try:
                            if prepare_session_ssh(resolved_metas, _ssh_key_dir, _ssh_shared_dir):
                                _ssh_key_prepared = True
                        except ValueError as ssh_err:
                            coord_logger.error(
                                f"SSH key setup failed for session {session_id}: {ssh_err}"
                            )
                            raise

                    # Issue #827 / #1134: Write per-session token + session_id files for proxy.
                    # Proxy calls GET /api/sessions/{id}/secrets/resolve with this Bearer token.
                    # Issue #1179: These mounts target /etc/proxy/ — proxy sidecar only, not agent.
                    session_token = getattr(session_info, "secret_fetch_token", None)
                    if session_token:
                        token_path = tmp_dir / "session_token"
                        token_path.write_text(session_token)
                        os.chmod(token_path, 0o644)
                        proxy_extra_mounts.append(f"{token_path}:/etc/proxy/session_token:ro")

                        session_id_path = tmp_dir / "session_id"
                        session_id_path.write_text(session_id)
                        os.chmod(session_id_path, 0o644)
                        proxy_extra_mounts.append(f"{session_id_path}:/etc/proxy/session_id:ro")

                # Issue #1089: Deduplicate mounts by container destination path (first-seen wins).
                # Format: "host_path:container_path[:options]" — extract the second colon-delimited field.
                _seen_container_paths: set[str] = set()
                _deduped_mounts: list[str] = []
                for _mount in extra_mounts:
                    _parts = _mount.split(":", 2)
                    _container_path = _parts[1] if len(_parts) >= 2 else _mount
                    if _container_path not in _seen_container_paths:
                        _seen_container_paths.add(_container_path)
                        _deduped_mounts.append(_mount)
                extra_mounts = _deduped_mounts

                # Issue #1179: Deduplicate proxy_extra_mounts independently (same strategy).
                _seen_proxy_paths: set[str] = set()
                _deduped_proxy_mounts: list[str] = []
                for _mount in proxy_extra_mounts:
                    _parts = _mount.split(":", 2)
                    _container_path = _parts[1] if len(_parts) >= 2 else _mount
                    if _container_path not in _seen_proxy_paths:
                        _seen_proxy_paths.add(_container_path)
                        _deduped_proxy_mounts.append(_mount)
                proxy_extra_mounts = _deduped_proxy_mounts

                effective_cli_path, docker_env_vars = resolve_docker_cli_path(
                    docker_image=effective_config.docker_image,
                    docker_extra_mounts=extra_mounts or None,
                    workspace=session_info.working_directory,
                    session_data_dir=docker_data_dir,
                    docker_home_directory=effective_config.docker_home_directory,
                    # Issue #1050: Resolve effective proxy image
                    proxy_image=(
                        (effective_config.docker_proxy_image or self._resolve_default_proxy_image())
                        if effective_config.docker_proxy_enabled
                        else None
                    ),
                    # Issue #1134: pass delivery env vars inline (no file); keep allowlist path
                    delivery_envs=delivery_envs or None,
                    # Issue #1396: non-secret direct env passthrough
                    extra_env=effective_config.extra_env or None,
                    proxy_allowlist_file=proxy_allowlist_file,
                    # Issue #1179: Proxy-sidecar-only mounts
                    docker_proxy_extra_mounts=proxy_extra_mounts or None,
                    # Issue #1356: label container for script schedule lookup
                    session_id=session_id,
                    # Thread server port into proxy sidecar so it calls back on the right port.
                    # main.py sets WEBUI_BASE_URL at startup; default is :8000 which breaks
                    # non-default ports (e.g. test instances on 8001).
                    proxy_webui_url=os.environ.get("WEBUI_BASE_URL") or None,
                )
                # Issue #1052: Tell claude-docker where the two SSH tmpdirs live.
                # key_dir  → proxy-only mount at /run/ssh-private:ro
                # shared_dir → both containers at /run/ssh (rw on proxy, ro on agent)
                if _ssh_key_prepared:
                    docker_env_vars["CLAUDE_DOCKER_SSH_KEY_DIR"] = str(_ssh_key_dir)
                    docker_env_vars["CLAUDE_DOCKER_SSH_SHARED_DIR"] = str(_ssh_shared_dir)
                coord_logger.info(
                    f"Docker mode enabled for session {session_id}: "
                    f"cli_path={effective_cli_path}, env={docker_env_vars}"
                )

            # Create/recreate SDK instance with session parameters (uses factory for testability — issue #559)
            # (effective_config already resolved early in this method — issue #1059)

            # Issue #1401: For session mode, force auto_memory_directory to the session-scoped subdir
            # so the SDK's built-in memory is isolated per session. Ensure dir exists for both
            # docker (docker mount section above already ran mkdir) and non-docker paths.
            if effective_config.auto_memory_mode == "session":
                _session_memory_dir = session_dir / "memory"
                _session_memory_dir.mkdir(parents=True, exist_ok=True)
                effective_config = effective_config.model_copy(
                    update={"auto_memory_directory": str(_session_memory_dir)}
                )

            litellm_env: dict[str, str] = {}
            catalog_model_update: dict = {}
            _proxy_mgr = self.litellm_proxy_manager

            tier_active = _all_tier_fields_set(effective_config)
            single_active = bool(
                effective_config.provider_catalog_id and effective_config.provider_model_id
            )

            if (tier_active or single_active) and _proxy_mgr is not None:
                if not _proxy_mgr.is_running:
                    # Issue #2183: the proxy manager object existing doesn't mean the
                    # proxy actually booted — without this guard, a catalog session
                    # proceeds to register a virtual key and build a base_url pointing
                    # at a dead port, failing downstream with an opaque connection
                    # error instead of a clear message.
                    first_line = (_proxy_mgr.last_error or "unknown error").splitlines()[0]
                    error_message = f"LiteLLM proxy is unavailable: {first_line}"
                    await self._callbacks.transition_to_error(session_id, error_message)
                    # No raw_error passed: last_error is already single-line (see
                    # LiteLLMProxyManager.start()), so error_message already carries
                    # the full signal — passing it again as raw_error would send
                    # _format_failure_content() down its stderr-pattern-matching path
                    # and overwrite this clear, proxy-specific message.
                    await self._callbacks.send_session_failure_message(session_id, error_message)
                    return False

                virtual_key = _proxy_mgr.register_session_key(session_id)
                base_url = f"http://127.0.0.1:{_proxy_mgr.port}/"

                if effective_config.docker_enabled:
                    # Docker path: proxy addon rewrites body model field; never override SDK model
                    if tier_active:
                        model_map = _build_model_map(effective_config)
                        default_alias = model_map["default"]
                        # Short alias for CLI baseline so the SDK starts with the right tier
                        catalog_model_update["model"] = effective_config.provider_default_tier
                    else:
                        single_alias = make_model_alias(
                            effective_config.provider_catalog_id,
                            effective_config.provider_model_id,
                        )
                        model_map = {}
                        default_alias = single_alias
                        # NOTE: catalog_model_update["model"] intentionally NOT set —
                        # proxy rewrites the body; SDK model left unset (R1 behavior change)

                    _proxy_mgr.register_session_routing(
                        session_id, virtual_key, base_url,
                        model_map=model_map, default_model=default_alias,
                    )
                    # Inject CLAUDE_CODE_ATTRIBUTION_HEADER=0 into the agent container so
                    # the billing header doesn't cache-bust 3rd-party / local LLM APIs.
                    # Must go through CLAUDE_DOCKER_EXTRA_ENV (not litellm_env) because
                    # _merged_extra_env only reaches the wrapper process, not the container.
                    _extra = json.loads(docker_env_vars.get("CLAUDE_DOCKER_EXTRA_ENV", "{}"))
                    _extra["CLAUDE_CODE_ATTRIBUTION_HEADER"] = "0"
                    docker_env_vars["CLAUDE_DOCKER_EXTRA_ENV"] = json.dumps(_extra)
                else:
                    # Non-Docker: tier breakout not available; single-model alias override only
                    if tier_active:
                        coord_logger.warning(
                            f"Session {session_id} has per-tier routing configured but "
                            "docker_enabled=False; falling back to default-tier single-model."
                        )
                        tier = effective_config.provider_default_tier
                        catalog_id = getattr(effective_config, f"provider_{tier}_catalog_id")
                        model_id = getattr(effective_config, f"provider_{tier}_model_id")
                    else:
                        catalog_id = effective_config.provider_catalog_id
                        model_id = effective_config.provider_model_id
                    model_alias = make_model_alias(catalog_id, model_id)
                    catalog_model_update["model"] = model_alias
                    litellm_env = {
                        "ANTHROPIC_BASE_URL": base_url,
                        "ANTHROPIC_API_KEY": virtual_key,
                        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
                    }

            # Override fields computed earlier in this method:
            #   system_prompt   — assembled above (includes legion guide, history ref, etc.)
            #   allowed_tools   — merged MCP + session tools list built above
            #   cli_path        — Docker-resolved path computed above
            #   process_wrapper — forced to None under Docker isolation (computed above)
            sdk_config = effective_config.model_copy(update={
                "system_prompt": minion_system_prompt,
                "allowed_tools": all_tools,
                "cli_path": effective_cli_path,
                "process_wrapper": effective_process_wrapper,
                **catalog_model_update,
            })

            # Issue #906: Custom auto-memory directory (claude mode + directory set) — create dir
            if effective_config.auto_memory_mode == "claude" and effective_config.auto_memory_directory:
                custom_memory_dir = Path(effective_config.auto_memory_directory)
                custom_memory_dir.mkdir(parents=True, exist_ok=True)
                coord_logger.debug(f"Custom auto-memory directory for session {session_id}: {custom_memory_dir}")

            # Issue #707: Build PreToolUse handler for internal tool access control
            from backend.config_manager import load_config as load_app_config
            permission_handler = self._build_permission_handler(
                session_dir, effective_config.history_distillation_enabled, effective_config.auto_memory_mode,
                skill_creating_enabled=effective_config.skill_creating_enabled,
                working_directory=Path(session_info.working_directory),
                is_legion=("legion" in mcp_servers),
                allow_background_agent=load_app_config().features.allow_background_agent,
                block_cross_session_messaging=load_app_config().features.block_cross_session_messaging,
            )

            # extra_env merge order: user-set < docker wrapper vars < litellm routing.
            _merged_extra_env: dict[str, str] = {}
            if effective_config.extra_env:
                _merged_extra_env.update(effective_config.extra_env)
            if docker_env_vars:
                _merged_extra_env.update(docker_env_vars)
            if litellm_env:
                _merged_extra_env.update(litellm_env)

            sdk = self._sdk_factory(
                session_id=session_id,
                working_directory=session_info.working_directory,
                session_name=session_info.name,  # For mock SDK fixture resolution (issue #561)
                config=sdk_config,
                storage_manager=storage_manager,
                session_manager=self.session_manager,
                message_callback=self._callbacks.create_message_callback(session_id),
                error_callback=self._callbacks.create_error_callback(session_id),
                permission_callback=permission_callback,
                rate_limit_callback=self._callbacks.on_rate_limits,
                resume_session_id=resume_sdk_session,
                mcp_servers=mcp_servers if mcp_servers else None,
                hooks_settings=hooks_settings,
                experimental=self.experimental,
                stderr_callback=self._callbacks.create_stderr_callback(session_id),
                extra_env=_merged_extra_env,
                permission_handler=permission_handler,
                recorder=recorder,
                phase_callback=self._callbacks.create_phase_callback(session_id),
            )
            # Issue #707: Set auto-approval callback so can_use_tool can notify us
            sdk.auto_approval_callback = self._callbacks.create_auto_approval_callback(session_id)
            self._active_sdks[session_id] = sdk

            # Issue #1838: a new SDK subprocess starts its own cumulative usage/cost
            # counters from zero, so the per-turn delta baseline must reset here too —
            # this fires on every genuine new-epoch creation (initial start, resume,
            # restart, reset) and never on the "already running" early return above.
            self._usage_baseline_by_session.pop(session_id, None)

            # Initialize callback lists if not exists (preserve existing callbacks)
            if session_id not in self._message_callbacks:
                self._message_callbacks[session_id] = []
            if session_id not in self._error_callbacks:
                self._error_callbacks[session_id] = []

            if not await sdk.start():
                logger.error(f"Failed to start SDK for session {session_id}")

                # Get the error message from the SDK
                raw_error_message = getattr(sdk.info, 'error_message', 'Unknown error occurred while starting Claude Code')

                # Issue #1902: sdk.start() already mirrors a caught ResultError's structured
                # fields onto sdk.info (_apply_result_error_fields) before returning False —
                # reconstruct that ResultError shape here so this fallback stays consistent
                # with what error_callback("startup_failed", ...) already computed, instead
                # of downgrading it back to plain substring-matched text.
                mirrored_result_error = SimpleNamespace(
                    subtype=getattr(sdk.info, 'error_subtype', None),
                    terminal_reason=getattr(sdk.info, 'error_terminal_reason', None),
                    api_error_status=getattr(sdk.info, 'error_api_error_status', None),
                    errors=getattr(sdk.info, 'error_list', None) or [],
                    result=getattr(sdk.info, 'error_result', None),
                )
                is_result_error = bool(
                    mirrored_result_error.subtype
                    or mirrored_result_error.terminal_reason
                    or mirrored_result_error.errors
                    or mirrored_result_error.result
                )
                result_error = mirrored_result_error if is_result_error else None
                structured_error_kwargs = (
                    self._callbacks.result_error_structured_kwargs(result_error) if result_error else {}
                )

                # Extract user-friendly error message
                error_message = self._callbacks.extract_claude_cli_error(raw_error_message, result_error)

                # Update session state to ERROR and reset processing state
                await self._callbacks.transition_to_error(session_id, error_message, **structured_error_kwargs)

                # Send system message explaining the failure (with raw details)
                await self._callbacks.send_session_failure_message(
                    session_id, error_message, raw_error_message, **structured_error_kwargs
                )

                # Clean up the failed SDK
                if session_id in self._active_sdks:
                    del self._active_sdks[session_id]
                    # logger.info(f"Cleaned up failed SDK for session {session_id}")

                return False

            # Send system message for SDK client launch/resume
            # logger.info(f"Session {session_id}: sdk_was_created = {sdk_was_created}")
            if sdk_was_created:  # Only if we actually created/resumed the SDK
                # logger.info(f"CALLING _send_client_launched_message for session {session_id}")
                await self._callbacks.send_client_launched_message(session_id)
            # else:
            #     logger.info(f"NOT calling _send_client_launched_message for session {session_id} because sdk_was_created = False")

            # Issue #976: Ensure app-level background refresh tasks for OAuth-enabled servers
            if effective_config.mcp_server_ids:
                selected_configs = self.mcp_config_manager.get_configs_by_ids(effective_config.mcp_server_ids)
                for mcp_cfg in selected_configs:
                    if mcp_cfg.oauth_enabled:
                        self.oauth_refresh_manager.ensure_refresh(mcp_cfg.id)

            # Issue #1982: tell the agent/user, once, when a shared MCP server was
            # unavailable at start — the server itself stayed attached regardless.
            if unavailable_mcp_servers:
                await self._callbacks.send_mcp_degraded_message(session_id, unavailable_mcp_servers)

            coord_logger.info(f"Session {session_id} SDK task started - state will update to ACTIVE when SDK is ready")
            return True

        except Exception as e:
            logger.exception(f"Failed to start integrated session {session_id}")

            # Extract user-friendly error message, preserve raw for details
            raw_error_str = str(e)
            error_message = self._callbacks.extract_claude_cli_error(raw_error_str)

            # Update session state to ERROR and reset processing state
            await self._callbacks.transition_to_error(session_id, error_message)
            coord_logger.info(f"Updated session {session_id} state to ERROR after exception")

            # Send system message explaining the failure (with raw details)
            await self._callbacks.send_session_failure_message(session_id, error_message, raw_error_str)

            # Clean up any partially created SDK
            if session_id in self._active_sdks:
                try:
                    sdk = self._active_sdks[session_id]
                    if sdk:
                        await sdk.terminate()
                except Exception:
                    pass  # Best effort cleanup
                del self._active_sdks[session_id]
                coord_logger.info(f"Cleaned up SDK for session {session_id} after exception")

            return False

    async def _next_turn_seq(self, session_id: str) -> int:
        """Return the next turn_seq for `session_id`, lazily seeding from the DB's
        turn_count on first use after a restart (`_turn_seq_by_session` is in-memory
        only). Shared by the normal per-turn recording path and the #1840
        termination-time subagent-usage catch-up flush, which both need a unique
        turn_seq from the same counter.
        """
        if session_id not in self._turn_seq_by_session:
            db_count = await self.analytics_store.get_turn_count(session_id)
            self._turn_seq_by_session[session_id] = db_count
        self._turn_seq_by_session[session_id] += 1
        return self._turn_seq_by_session[session_id]

    async def flush_unflushed_subagent_usage(
        self, session_id: str, allow_retry: bool = True
    ) -> None:
        """Persist any leftover subagent (Task/Agent tool) usage as a catch-up
        row, e.g. from a run_in_background subagent still emitting usage after
        its originating turn's ResultMessage already fired and popped the
        accumulator. Shared by terminate_session() and delete_session()
        (issue #1840, folded into #1941): both are a last chance to persist
        this usage — no further 'result' message will ever arrive to trigger
        the normal merge, and since #1941, deletion no longer wipes analytics
        rows afterward either, so dropping it here would be permanent data loss.

        Owns popping `_last_turn_model_by_session` unconditionally (issue #1831
        requires it cleared on delete regardless of whether there's leftover
        subagent usage to flush), using its value to attribute the catch-up
        row when there is one.

        `allow_retry` controls whether a failed flush restores the accumulator
        for a later retry attempt — correct for terminate_session() (the
        session_id can be restarted and terminated again, retriggering this
        flush), but not for delete_session() (the session_id is gone for good
        afterward; restoring would just leak the entry in memory forever with
        no code path left to ever retry it).
        """
        _model = self._last_turn_model_by_session.pop(session_id, None)
        _sub_accum = self._subagent_usage_by_session.pop(session_id, None)
        analytics_store = self.analytics_store
        if not _sub_accum or not analytics_store:
            return
        try:
            turn_seq = await self._next_turn_seq(session_id)
            _flushed = await analytics_store.record_turn(
                session_id, turn_seq, _model, _sub_accum, None, is_subagent=True
            )
            if not _flushed and allow_retry:
                # Don't lose it — restore so a future flush/merge can retry.
                self._subagent_usage_by_session[session_id] = _sub_accum
        except Exception:
            logger.exception(
                f"Failed to flush subagent usage for session {session_id}"
            )
            if allow_retry:
                self._subagent_usage_by_session[session_id] = _sub_accum

    async def terminate_session(self, session_id: str) -> bool:
        """Terminate a session and cleanup resources"""
        try:
            # Issue #976: Release per-server OAuth refresh ref counts for this session
            _term_info = await self.session_manager.get_session_info(session_id)
            if _term_info and _term_info.config.get("mcp_server_ids"):
                _term_configs = self.mcp_config_manager.get_configs_by_ids(_term_info.config.get("mcp_server_ids"))
                for _cfg in _term_configs:
                    if _cfg.oauth_enabled:
                        self.oauth_refresh_manager.release_refresh(_cfg.id)
                    # Issue #1484: release shared-connection refcount on termination
                    if getattr(_cfg, "shared_connection", False):
                        await self.shared_mcp_manager.release(_cfg.id)

            # Issue #500: Stop queue processor before termination
            self.queue_processor.stop(session_id)

            # Reset processing state before termination
            await self.session_manager.end_turn(session_id)

            # Issue #520: Mark active ToolCalls as interrupted and store ToolCallUpdate entries
            self._callbacks.mark_session_tools_interrupted(session_id)

            # Terminate SDK first
            sdk = self._active_sdks.get(session_id)
            if sdk:
                await sdk.terminate()
                del self._active_sdks[session_id]

            _litellm_proxy_manager = self.litellm_proxy_manager
            if _litellm_proxy_manager is not None:
                _litellm_proxy_manager.unregister_session_key(session_id)
                _litellm_proxy_manager.unregister_session_routing(session_id)

            # Issue #1840/#1941: flush any unflushed subagent (Task/Agent tool)
            # usage — e.g. a run_in_background subagent still emitting usage
            # after its originating turn's ResultMessage already fired and
            # popped the accumulator. Termination is the last chance to persist
            # it: no further 'result' message will ever arrive for this session
            # to trigger the normal merge. delete_session() needs the same
            # flush now that it preserves analytics history instead of wiping
            # it (see delete_session()'s own call to this helper).
            await self.flush_unflushed_subagent_usage(session_id)

            # Terminate session through manager — fires the "terminate" lifecycle-
            # record callback itself now (issue #2184), replacing the manual
            # recorder.record_lifecycle("terminate") call this method used to make
            # BEFORE this call (the ordering bug AC7 flagged: recording into an
            # already-closed/removed recorder would have silently recorded nothing
            # once recording moved into a hook fired from inside this call).
            success = await self.session_manager.terminate_session(session_id)

            # Issue #1998: recorder is only ever torn down here (not on restart/reset).
            # Popped/closed AFTER the manager call above so the lifecycle-record hook
            # it fires still finds this recorder in self._session_recorders.
            recorder = self._session_recorders.pop(session_id, None)
            if recorder is not None:
                recorder.close()

            # Issue #820: Clean up session /tmp directory on session end
            # Issue #1933: most sessions never write to /tmp, so check existence
            # synchronously (cheap stat) and only dispatch the blocking rmtree to a
            # thread when there's actually something to remove — avoids contending
            # the shared default thread-pool executor (also used by analytics
            # writes) with a thread-pool round trip for a no-op on every session
            # during a large concurrent Stop All.
            session_tmp_dir = get_session_tmp_dir(self.session_manager.sessions_dir / session_id)
            if session_tmp_dir.exists():
                await asyncio.to_thread(
                    cleanup_session_tmp, session_id, self.session_manager.sessions_dir
                )

            # Cleanup storage and callbacks
            if session_id in self._storage_managers:
                del self._storage_managers[session_id]
            if session_id in self._message_callbacks:
                del self._message_callbacks[session_id]
            if session_id in self._error_callbacks:
                del self._error_callbacks[session_id]
            # Issue #858: Cleanup per-session tool-call event
            self._tool_call_events.pop(session_id, None)
            # Issue #1694: Cleanup per-session message-emitted barrier state
            self._message_emitted_events.pop(session_id, None)
            self._emitted_turn_ids.pop(session_id, None)
            # Issue #894: Cleanup retry sequence tracking
            self._retry_sequences.pop(session_id, None)

            # Issue #2159: no explicit _notify_state_change() here — this used to be
            # a 3rd, redundant state_change emission on top of the 2 that
            # session_manager.terminate_session() already fires on its own
            # (TERMINATING, then TERMINATED) via its callback chain.

            coord_logger.info(f"Session {session_id} stopped")
            return success

        except Exception:
            logger.exception(f"Failed to terminate integrated session {session_id}")
            return False

    async def disconnect_sdk(self, session_id: str) -> bool:
        """Disconnect the active SDK for a session without full termination."""
        sdk = self._active_sdks.get(session_id)
        if sdk:
            success = await sdk.disconnect()
            if success:
                del self._active_sdks[session_id]
            return success
        return True  # Already disconnected

    async def restart_session(self, session_id: str, permission_callback: Callable | None = None) -> bool:
        """
        Restart a session by disconnecting SDK and resuming with same session ID.

        This is useful for unsticking the agent without losing conversation history.

        Issue #2159: deliberately ACTIVE-only, not is_deliverable() — restarting the
        live turn is a different predicate than deliverability (a PAUSED session has
        no live turn to restart; it's blocked on a permission decision, not running).
        Enforced here, the one place every caller (REST endpoint, legion_mcp_tools's
        restart_session MCP tool, ApplicationService.restart_session) funnels through,
        so the gate holds regardless of entry point rather than being trusted to
        whichever caller remembers to check it themselves.
        """
        try:
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info or session_info.state != SessionState.ACTIVE:
                logger.warning(
                    f"Cannot restart session {session_id}: not ACTIVE "
                    f"(state: {session_info.state if session_info else 'not found'})"
                )
                return False

            coord_logger.info(f"Restarting session {session_id}")

            # Issue #2109 (AC1): mark open tools interrupted on restart, same as
            # terminate_session/interrupt_session already do — a tool left open
            # across a restart previously had no stored terminal record.
            self._callbacks.mark_session_tools_interrupted(session_id)

            # Get current SDK if it exists
            sdk = self._active_sdks.get(session_id)
            if sdk:
                # Disconnect existing SDK gracefully
                coord_logger.debug(f"Disconnecting existing SDK for session {session_id}")
                disconnect_result = await sdk.disconnect()
                if not disconnect_result:
                    logger.warning(f"SDK disconnect returned False for session {session_id}")

                # Update session state to TERMINATED after disconnect
                await self.session_manager.recover_session(session_id)

                # Wait for cleanup — Docker --rm needs time to remove crashed containers
                # before a new container can be started (issue #781)
                await asyncio.sleep(1.0)

                # Remove old SDK reference
                del self._active_sdks[session_id]
            else:
                # No active SDK - restart will act like a fresh start
                coord_logger.debug(f"No existing SDK for session {session_id}, will create new one")
                # Ensure session state is TERMINATED before starting
                await self.session_manager.recover_session(session_id)

            # Issue #1130: Clear watchdog state so next episode starts fresh
            if self._watchdog is not None:
                self._watchdog.reset_session(session_id)

            # Issue #1513: Reset unread state — a restart is a new context.
            try:
                await self.session_manager.update_session(
                    session_id,
                    last_completion_at=None,
                    last_viewed_at=None,
                )
            except Exception:
                logger.exception(f"Failed to clear unread timestamps for session {session_id}")

            # Start session again (will automatically resume using claude_code_session_id)
            success = await self.start_session(session_id, permission_callback, _lifecycle_action="restart")

            if success:
                coord_logger.info(f"Session {session_id} restarted successfully")
            else:
                logger.error(f"Failed to restart session {session_id}")

            return success

        except Exception:
            logger.exception(f"Failed to restart session {session_id}")
            return False

    async def reset_session(self, session_id: str, permission_callback: Callable | None = None, _from_queue_processor: bool = False) -> bool:
        """
        Reset a session by clearing all messages and starting fresh.

        Keeps session settings (permission mode, tools, etc.) but clears conversation history.
        Queue: pending items preserved, in-flight items marked failed.

        Args:
            _from_queue_processor: When True, skip stopping the queue processor to avoid
                cancelling the calling task (the processor manages its own lifecycle).
        """
        try:
            coord_logger.info(f"Resetting session {session_id}")

            # Issue #976: Release per-server OAuth refresh ref counts for this session
            _reset_info = await self.session_manager.get_session_info(session_id)
            if _reset_info and _reset_info.config.get("mcp_server_ids"):
                _reset_configs = self.mcp_config_manager.get_configs_by_ids(_reset_info.config.get("mcp_server_ids"))
                for _cfg in _reset_configs:
                    if _cfg.oauth_enabled:
                        self.oauth_refresh_manager.release_refresh(_cfg.id)
                    # Issue #1484: release shared-connection refcount on reset
                    if getattr(_cfg, "shared_connection", False):
                        await self.shared_mcp_manager.release(_cfg.id)

            # Issue #500: Stop queue processor and mark any in-flight item as failed
            # Skip when called from the processor itself to avoid self-cancellation
            if not _from_queue_processor:
                self.queue_processor.stop(session_id)

            # Issue #1130: Clear watchdog state so next episode starts fresh
            if self._watchdog is not None:
                self._watchdog.reset_session(session_id)

            # Get current SDK and disconnect
            sdk = self._active_sdks.get(session_id)
            if sdk:
                await sdk.disconnect()
                await self.session_manager.recover_session(session_id)
                await asyncio.sleep(0.5)
                del self._active_sdks[session_id]

            # Issue #1019: If session is in ERROR state (no active SDK to disconnect),
            # transition to TERMINATED so start_session() will accept it.
            _pre_reset_info = await self.session_manager.get_session_info(session_id)
            if _pre_reset_info and _pre_reset_info.state == SessionState.ERROR:
                await self.session_manager.recover_session(session_id)
                coord_logger.info(f"Transitioned errored session {session_id} to TERMINATED for reset")

            # Clear Claude Code session ID from state (prevents resume)
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info:
                await self.session_manager.update_claude_code_session_id(session_id, None)
                coord_logger.info(f"Cleared Claude Code session ID for {session_id}")

            # Archive session data before clearing (Issue #579)
            await self._archive_session_for_reset(session_id)

            # Clear message history
            storage = self._storage_managers.get(session_id)
            if not storage:
                # Create storage manager on-demand for inactive sessions
                coord_logger.debug(f"Creating storage manager for inactive session {session_id}")
                session_dir = await self.session_manager.get_session_directory(session_id)
                storage = DataStorageManager(session_dir)
                await storage.initialize()
                self._storage_managers[session_id] = storage

            # Clear messages and resources (storage now guaranteed to exist)
            if storage:
                await storage.clear_messages()
                coord_logger.info(f"Cleared message history for session {session_id}")
                await storage.clear_resources()
                coord_logger.info(f"Cleared resources for session {session_id}")
                # Issue #1244: clear queue + attachments (archive already captured them)
                await storage.clear_queue()
                coord_logger.info(f"Cleared queue for session {session_id}")
                await storage.clear_attachments()
                coord_logger.info(f"Cleared attachments for session {session_id}")
                # Issue #1746: Drop cached task leg registry along with the
                # cleared message history it was built from.
                self._task_leg_registries.pop(session_id, None)

            # Issue #1244: rotate proxy logs then clear non-proxy docker_claude_data and tmp
            await self._rotate_proxy_logs(session_id)
            await self._clear_docker_claude_data(session_id, keep_subdirs={"proxy"})
            await self._clear_session_tmp(session_id)

            # Issue #1513: Reset unread state — a reset is a new context.
            try:
                await self.session_manager.update_session(
                    session_id,
                    last_completion_at=None,
                    last_viewed_at=None,
                )
            except Exception:
                logger.exception(f"Failed to clear unread timestamps for session {session_id}")

            # Issue #858: Clear event so stale set() signals don't skip the next wait.
            if session_id in self._tool_call_events:
                self._tool_call_events[session_id].clear()
            # Issue #1694: Clear barrier state so stale set() signals / emitted turn_ids
            # from the prior conversation don't leak into the reset session.
            if session_id in self._message_emitted_events:
                self._message_emitted_events[session_id].clear()
            self._emitted_turn_ids.pop(session_id, None)
            # Issue #894: Cleanup retry sequence tracking on reset
            self._retry_sequences.pop(session_id, None)

            # Issue #500: Notify frontend to clear messages for this session
            await self._callbacks.notify_session_reset(session_id)

            # Start fresh session (will create new Claude Code session)
            success = await self.start_session(session_id, permission_callback, _lifecycle_action="reset")

            if success:
                coord_logger.info(f"Session {session_id} reset successfully")
            else:
                logger.error(f"Failed to reset session {session_id}")

            return success

        except Exception:
            logger.exception(f"Failed to reset session {session_id}")
            return False

    async def _archive_session_for_reset(self, session_id: str) -> bool:
        """Archive session data before a reset so it can be reviewed later.

        Uses snapshot_artifacts() with is_reset=True so that queue.jsonl,
        attachments/, and proxy logs are captured but history/memory are left
        in place (decision #5).  Writes disposal_metadata.json with reason="reset".

        Returns True on success, False on failure (logged, never raised).
        """
        try:
            from backend.legion.archive_manager import SnapshotContext
            from backend.models.archive_models import DisposalMetadata

            session_info = await self.session_manager.get_session_info(session_id)
            session_dir = self.session_manager.sessions_dir / session_id
            # Use microsecond-precision timestamp for cross-path consistency
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
            archive_dir = (
                self.session_manager.data_dir / "archives" / "minions" / session_id / timestamp
            )
            archive_dir.mkdir(parents=True, exist_ok=True)

            # Determine project/legion ID for metadata + snapshot context
            project_id = ""
            if session_info:
                for proj in await self.project_manager.list_projects():
                    if session_id in proj.session_ids:
                        project_id = proj.project_id
                        break

            # Build snapshot context
            cfg = session_info.config if session_info else {}
            auto_mem_raw = cfg.get("auto_memory_directory")
            ctx = SnapshotContext(
                session_id=session_id,
                legion_id=project_id or None,
                auto_memory_directory=Path(auto_mem_raw) if auto_mem_raw else None,
                docker_enabled=bool(cfg.get("docker_enabled", False)),
                proxy_enabled=bool(cfg.get("docker_proxy_enabled", False)),
                is_reset=True,
                will_be_deleted=False,
            )

            # Delegate to unified artifact snapshot
            archive_manager = self.legion_system.archive_manager
            await archive_manager.snapshot_artifacts(session_dir, archive_dir, ctx)

            # Write disposal_metadata.json using the shared dataclass
            metadata = DisposalMetadata(
                disposed_at=datetime.now(UTC).timestamp(),
                reason="reset",
                parent_overseer_id=None,
                parent_overseer_name=None,
                legion_id=project_id,
                final_state="reset",
                minion_id=session_id,
                minion_name=session_info.name if session_info else "",
                minion_role=session_info.role if session_info else None,
                overseer_level=0,
                child_minion_ids=[],
                descendants_count=0,
            )
            metadata_path = archive_dir / "disposal_metadata.json"
            metadata_path.write_text(json.dumps(metadata.to_dict(), indent=2))

            # Fire-and-forget distillation — writes to live history/ (decision #5).
            # Uses the archived copy of messages.jsonl so the live file can be truncated.
            # Issue #1059: Use resolve_effective_config to support template-linked sessions.
            _eff_for_archive = await resolve_effective_config(
                session_info, self.template_manager, self.profile_manager
            )
            if _eff_for_archive.history_distillation_enabled:
                archived_messages = archive_dir / "messages.jsonl"
                if archived_messages.exists():
                    from backend.history_distiller import distill_session_history

                    history_output = session_dir / "history" / f"{timestamp}.md"
                    archive_ts = datetime.now(UTC).isoformat()
                    t = asyncio.create_task(
                        distill_session_history(archived_messages, history_output, session_id, archive_ts)
                    )
                    t.add_done_callback(task_done_log_exception)
                    coord_logger.debug(f"Launched history distillation for session {session_id}")

            coord_logger.info(f"Archived session {session_id} to {archive_dir}")
            return True

        except Exception as e:
            coord_logger.warning(f"Archive before reset failed for {session_id}: {e}")
            return False

    async def _rotate_proxy_logs(self, session_id: str) -> None:
        """Truncate proxy log files to empty after archiving (issue #1244, decision #11).

        Truncate rather than unlink so the file descriptor stays valid if the
        proxy container is mid-write at reset time.
        """
        proxy_dir = self.session_manager.sessions_dir / session_id / "docker_claude_data" / "proxy"
        if not proxy_dir.is_dir():
            return
        for name in ("access.log", "dns.log", "socks5.log", "dropped.log"):
            log_file = proxy_dir / name
            if log_file.exists():
                try:
                    log_file.write_bytes(b"")
                    coord_logger.debug(f"Truncated proxy log {name} for {session_id}")
                except OSError:
                    logger.exception(f"Failed to truncate proxy log {name} for {session_id}")

    async def _clear_docker_claude_data(
        self, session_id: str, keep_subdirs: set[str] | None = None
    ) -> None:
        """Remove all subdirs under docker_claude_data/ except those in keep_subdirs.

        Subdirs are recreated by claude-docker on next session start.
        """
        keep = keep_subdirs or set()
        docker_data_dir = (
            self.session_manager.sessions_dir / session_id / "docker_claude_data"
        )
        if not docker_data_dir.is_dir():
            return
        for child in docker_data_dir.iterdir():
            if child.is_dir() and child.name not in keep:
                try:
                    shutil.rmtree(child)
                    coord_logger.debug(f"Removed docker_claude_data/{child.name} for {session_id}")
                except OSError:
                    logger.exception(
                        f"Failed to remove docker_claude_data/{child.name} for {session_id}"
                    )

    async def _clear_session_tmp(self, session_id: str) -> None:
        """Remove the session's tmp/ directory (issue #1244, decision #3)."""
        tmp_dir = self.session_manager.sessions_dir / session_id / "tmp"
        if tmp_dir.is_dir():
            try:
                shutil.rmtree(tmp_dir)
                coord_logger.debug(f"Removed tmp/ for {session_id}")
            except OSError:
                logger.exception(f"Failed to remove tmp/ for {session_id}")

    def _build_permission_handler(
        self,
        session_dir: Path,
        knowledge_mgmt_enabled: bool,
        auto_memory_mode: str = "claude",
        skill_creating_enabled: bool = False,
        working_directory: Path | None = None,
        is_legion: bool = False,
        allow_background_agent: bool = False,
        block_cross_session_messaging: bool = True,
    ) -> InternalPermissionHandler:
        """Build internal permission handler with consistent path configuration (issue #707)."""
        memory_dir = session_dir / "memory" if auto_memory_mode == "session" else None
        return InternalPermissionHandler(
            session_data_dir=session_dir,
            plans_dir=Path.home() / ".cc_webui" / "plans",
            knowledge_mgmt_enabled=knowledge_mgmt_enabled,
            memory_dir=memory_dir,
            skill_creating_enabled=skill_creating_enabled,
            working_directory=working_directory,
            is_legion=is_legion,
            allow_background_agent=allow_background_agent,
            block_cross_session_messaging=block_cross_session_messaging,
        )

    def _resolve_default_proxy_image(self) -> str:
        """Return the app-config default proxy image name (issue #1050)."""
        from backend.config_manager import load_config as load_app_config
        app_config = load_app_config()
        return app_config.proxy.proxy_image
