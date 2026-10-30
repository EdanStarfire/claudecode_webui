"""
Session Coordinator for Claude Code WebUI

Integrates session management, data storage, and SDK interaction
into a unified system for managing Claude Code sessions.
"""

import asyncio
import json
import logging
import re
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config_resolution import (
    _MCP_INHERITABLE_FIELDS,
    CONFIG_FIELDS,
    _mcp_field_equal,
    resolve_template_config,
)
from backend.mcp.links_mcp_tools import LinksMCPTools
from backend.mcp.resource_mcp_tools import ResourceMCPTools
from shared.logging_config import get_logger

from .archive_coordinator import ArchiveCoordinator
from .claude_sdk import ClaudeSDK, ResultError
from .config_manager import default_config_file
from .coordinator_builder import build_core_managers, build_legion_system
from .data_storage import DataStorageManager
from .delivery_gate import DeliveryGate
from .hooks.pretooluse_handler import InternalPermissionHandler
from .litellm_proxy_manager import make_model_alias
from .message_history_reader import WINDOW_MODES
from .message_pipeline import MessagePipeline, MessagePipelineCallbacks
from .models.legion_models import Comm, CommType, DeliveryPolicy, SenderType
from .models.messages import (
    LegacyMessageFormatError,
    PermissionInfo,
    ToolCall,
)
from .models.permission_mode import PermissionMode
from .project_manager import ProjectInfo
from .queue_processor import QueueProcessor
from .routers._models import _validate_new_additional_directory
from .session_config import DEFAULTS, SessionConfig
from .session_launcher import (
    STARTUP_ATTACH_TIMEOUT_SECONDS,  # noqa: F401 — re-exported; test_session_coordinator.py imports it from here
    LauncherCallbacks,
    SessionLauncher,
)
from .session_lifecycle import is_interruptible
from .session_manager import STOPPED_STATES, SessionInfo, SessionState
from .session_query import SessionQuery
from .session_recorder import SessionRecorder
from .session_resources import SessionResources
from .task_registry import TaskLegRegistry
from .tenant_context import TenantContext
from .timestamp_injection import maybe_inject_timestamp
from .timestamp_utils import get_unix_timestamp
from .tool_call_tracker import ToolCallTracker

# Get specialized logger for coordinator actions
coord_logger = get_logger('coordinator', category='COORDINATOR')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


def _render_inject_file(placeholder: str, fmt: str, key_path: str | None) -> str:
    """Render placeholder into an inject_file's content.

    Issue #1134: builds a formatted file containing only the placeholder string
    so the agent never sees the real credential value.
    """
    if fmt == "raw" or not key_path:
        return placeholder

    keys = key_path.split(".")

    if fmt == "json":
        obj: dict = {}
        cur = obj
        for key in keys[:-1]:
            cur[key] = {}
            cur = cur[key]
        cur[keys[-1]] = placeholder
        return json.dumps(obj, indent=2)

    if fmt == "yaml":
        # Minimal YAML without external dependencies.
        lines = []
        indent = 0
        for key in keys[:-1]:
            lines.append("  " * indent + f"{key}:")
            indent += 1
        lines.append("  " * indent + f"{keys[-1]}: {placeholder}")
        return "\n".join(lines) + "\n"

    if fmt == "toml":
        section = ".".join(keys[:-1])
        last = keys[-1]
        if section:
            return f'[{section}]\n{last} = "{placeholder}"\n'
        return f'{last} = "{placeholder}"\n'

    return placeholder


_VALID_TIERS: frozenset[str] = frozenset({"haiku", "sonnet", "opus"})


def _all_tier_fields_set(cfg) -> bool:
    """Return True iff all 6 per-tier id fields and provider_default_tier are non-None."""
    return all((
        cfg.provider_haiku_catalog_id, cfg.provider_haiku_model_id,
        cfg.provider_sonnet_catalog_id, cfg.provider_sonnet_model_id,
        cfg.provider_opus_catalog_id, cfg.provider_opus_model_id,
        cfg.provider_default_tier,
    ))


def _build_model_map(cfg) -> dict[str, str]:
    """Build {tier: alias, default: alias} from per-tier config fields."""
    haiku_alias  = make_model_alias(cfg.provider_haiku_catalog_id,  cfg.provider_haiku_model_id)
    sonnet_alias = make_model_alias(cfg.provider_sonnet_catalog_id, cfg.provider_sonnet_model_id)
    opus_alias   = make_model_alias(cfg.provider_opus_catalog_id,   cfg.provider_opus_model_id)
    tier_map = {"haiku": haiku_alias, "sonnet": sonnet_alias, "opus": opus_alias}
    tier_map["default"] = tier_map[cfg.provider_default_tier]
    return tier_map


def _resolve_analytics_model_label(cfg, sdk_reported_model: str | None = None) -> str | None:
    """Return the model label to write to analytics.

    Priority:
      1. Single provider-catalog routing → catalog alias.
      2. Per-tier provider-catalog routing → default-tier alias.
      3. SDK-reported model for the current turn, if available.
      4. Configured model field (generic tier alias) as a last resort.
    """
    if cfg.provider_catalog_id and cfg.provider_model_id:
        return make_model_alias(cfg.provider_catalog_id, cfg.provider_model_id)
    if _all_tier_fields_set(cfg):
        tier = cfg.provider_default_tier
        if tier not in _VALID_TIERS:
            return cfg.model
        catalog_id = getattr(cfg, f"provider_{tier}_catalog_id")
        model_id = getattr(cfg, f"provider_{tier}_model_id")
        return make_model_alias(catalog_id, model_id)
    return sdk_reported_model if sdk_reported_model is not None else cfg.model


def _nested_metadata(message_data: Any, parsed_message: Any) -> dict:
    """Issue #2190: a real SDK-origin message_callback invocation receives
    MessageRecord.to_dict() (metadata already nested under message_data['metadata'])
    since issue #2084 stage 3-B — _create_message_callback's own
    message_processor.process_message(message_data, source="sdk") call then
    re-dispatches that already-canonical dict through the same flat-shape-oriented
    message_parser.py handlers a second time, which cannot recover a field like
    is_error/api_error_status/model from a dict that no longer carries it at the
    top level (confirmed via a real raw-fixture replay: parsed_message.metadata
    silently came back empty of these for a genuine nested 'result' record).
    Prefers message_data's own nested metadata (the reliable source for real
    SDK-origin messages) and falls back to parsed_message.metadata for callers
    that still pass a flat dict directly (e.g. synthetic system messages, some
    test call sites)."""
    nested = message_data.get('metadata') if isinstance(message_data, dict) else None
    if isinstance(nested, dict) and nested:
        return nested
    return parsed_message.metadata or {}


def _extract_send_comm_sender_attachments(result_content: Any) -> list[dict] | None:
    """Issue #1593/#1730: Parse sender resource IDs for mcp__legion__send_comm attachments.

    Extracts the JSON footer embedded by `_handle_send_comm` in the tool result text
    (format: `<!-- sender_attachments: [...] -->`).
    """
    if not isinstance(result_content, str):
        return None

    match = re.search(r"<!-- sender_attachments: (.*?) -->", result_content, re.DOTALL)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, list) and parsed else None


class SessionCoordinator:
    """
    Coordinates Claude Code sessions with integrated storage and SDK management.

    This is the main orchestrator that ties together:
    - Session lifecycle management
    - Persistent data storage
    - Claude Code SDK integration
    - Message processing and parsing
    - Real-time communication pipeline
    """

    def __init__(
        self,
        data_dir: Path = None,
        experimental: bool = False,
        litellm_proxy_manager=None,
        host: str = "127.0.0.1",
        port: int = 8000,
        session_recording_enabled: bool = False,
        watchdog: Any | None = None,
        session_manager: Any | None = None,
        template_manager: Any | None = None,
        tenant_context: TenantContext | None = None,
    ):
        # Issue #2268 AC1: tenant_context is the single source of truth for this
        # process's data_dir/config_file/host/home_dir/backend_id — constructed here
        # if the caller (tests, mostly) didn't already build and pass one.
        self.tenant_context = tenant_context or TenantContext(
            data_dir=data_dir or Path("data"),
            config_file=default_config_file(),
            host=host,
        )
        self.data_dir = self.tenant_context.data_dir
        self.experimental = experimental
        # Issue #2159: watchdog is a declared constructor dependency (same
        # None-default-then-reassign pattern as litellm_proxy_manager below).
        # Issue #2184: web_server.py now constructs SessionManager/TemplateManager
        # (and the SessionWatchdogService built from them) BEFORE this coordinator,
        # passing all three in here directly — set_watchdog()'s post-construction
        # reassignment is gone, every consumer finds `self._watchdog` already correct
        # from __init__ (only the `is not None` guard remains, same as
        # litellm_proxy_manager's own guard).
        self._watchdog = watchdog
        # Issue #1998: dev-only raw SDK traffic capture, structurally unavailable unless the
        # Backend was started with --enable-session-recording. See start_session()'s recorder
        # gate and routers/sessions.py's config-save rejection when this is False.
        self.session_recording_enabled = session_recording_enabled
        # Issue #1789: the main app's own bind host/port — used by OAuthCallbackListenerManager
        # to bind on the same host, and by McpConfigManager's custom-callback conflict checks.
        self.host = host
        self.port = port

        # Issue #2267: the ~18 collaborators below depend only on data_dir and/or
        # each other (never on `self`), so their construction lives in
        # coordinator_builder.py. Issue #2184: session_manager/template_manager are
        # construct-if-not-given (web_server.py may pass an externally-constructed
        # one in) — build_core_managers() applies that same convention.
        core = build_core_managers(
            data_dir=self.data_dir,
            session_manager=session_manager,
            template_manager=template_manager,
            host=self.host,
            tenant_context=self.tenant_context,
        )
        self.session_manager = core.session_manager
        self.template_manager = core.template_manager
        self.project_manager = core.project_manager
        self.message_parser = core.message_parser
        self.message_processor = core.message_processor
        self.credential_vault = core.credential_vault
        self.profile_manager = core.profile_manager
        self.mcp_config_manager = core.mcp_config_manager
        self.hook_config_manager = core.hook_config_manager
        self.provider_catalog_store = core.provider_catalog_store
        self.oauth_manager = core.oauth_manager
        self.oauth_refresh_manager = core.oauth_refresh_manager
        self.mcp_oneshot_connector = core.mcp_oneshot_connector
        self.oauth_callback_listener_manager = core.oauth_callback_listener_manager
        self.vault_refresh_manager = core.vault_refresh_manager
        self.secrets_service = core.secrets_service
        self.oauth_secret_bundle_manager = core.oauth_secret_bundle_manager
        self.queue_manager = core.queue_manager
        self.pending_store = core.pending_store

        # Active SDK instances
        self._active_sdks: dict[str, ClaudeSDK] = {}
        self._storage_managers: dict[str, DataStorageManager] = {}
        # Issue #1998: raw-fidelity session recorders, keyed by session_id. Created lazily in
        # start_session() when the effective config opts in and the flag above is on; reused
        # across restart_session/reset_session (never recreated); dropped in terminate_session.
        self._session_recorders: dict[str, SessionRecorder] = {}

        # Issue #1484: shared-connection MCP manager for opt-in passthrough configs.
        # Receives credential_vault so ${secret:...} refs in shared configs are resolved
        # to plaintext inline (no Docker proxy sidecar in the shared-connection egress path).
        # Built unwired by build_core_managers() above — the set_cfg_lookup() closure
        # captures self.mcp_config_manager, so it can only be wired after `self` exists.
        self.shared_mcp_manager = core.shared_mcp_manager

        async def _cfg_lookup(server_id: str):
            return await self.mcp_config_manager.get_config(server_id)

        self.shared_mcp_manager.set_cfg_lookup(_cfg_lookup)

        # Event callbacks
        self._message_callbacks: dict[str, list[Callable]] = {}
        self._error_callbacks: dict[str, list[Callable]] = {}
        self._state_change_callbacks: list[Callable] = []
        # Issue #2159: metadata-only updates — separate from _state_change_callbacks.
        self._metadata_change_callbacks: list[Callable] = []
        # Issue #2184: is_processing-only updates — separate from both of the above.
        self._processing_change_callbacks: list[Callable] = []
        self._session_reset_callbacks: list[Callable] = []
        self._session_deleted_callbacks: list[Callable] = []
        self._tool_call_broadcast_callbacks: list[Callable] = []


        # Track applied permission updates for state management
        self._permission_updates: dict[str, list[dict]] = {}  # session_id -> list of applied updates

        self.litellm_proxy_manager = litellm_proxy_manager

        # Audit writer (set after construction via set_audit_writer; optional)
        self._audit_writer = None

        # Issue #2127: crash-repair records produced by _repair_crash_orphaned_tool_calls()
        # before set_audit_writer() has ever been called, queued for replay once it is.
        self._crash_repair_audit_backlog: list[tuple[str, dict]] = []

        # Issue #2159: PermissionService back-reference (set after construction via
        # set_permission_service — PermissionService's own constructor takes this
        # coordinator, so it can't be passed in here without a cycle at construction
        # time; optional, same pattern as _audit_writer/litellm_proxy_manager above).
        self.permission_service = None

        # Issue #2189 (coordinator-split PR 1): _sdk_factory now lives on
        # SessionLauncher (constructed at the end of this __init__, after every
        # collaborator manager it needs exists) — see the `_sdk_factory` property
        # below __init__ for the delegate that keeps this attribute readable/
        # writable on the coordinator unchanged.

        # Issue #899: Rate limit state accumulator (rate_limit_type -> {used_percentage, resets_at})
        self._rate_limits_state: dict[str, dict] = {}
        self._rate_limit_broadcast_callback: Callable | None = None

        # Issue #894: Track active api_retry sequence per session (session_id -> retry_message_id)
        self._retry_sequences: dict[str, str] = {}

        # Issue #324: Active tool calls per session for unified ToolCall lifecycle
        # Maps session_id -> {tool_use_id: ToolCall}
        self._active_tool_calls: dict[str, dict[str, ToolCall]] = {}

        # Issue #1746: Per-session background-agent (Task) leg registry.
        # Lazily hydrated from stored messages on first access, then kept live
        # via the four Task lifecycle frames as they stream in.
        self._task_leg_registries: dict[str, TaskLegRegistry] = {}
        # Issue #2145: latest background-task snapshot per session (process-bound)
        self._background_tasks: dict[str, list[dict[str, Any]]] = {}

        # Issue #858: Per-session event notified on every create_tool_call(), allowing
        # permission callbacks to wait efficiently instead of polling.
        self._tool_call_events: dict[str, asyncio.Event] = {}

        # Issue #1694/#1958: Per-session event + set of emitted turn_ids, notified whenever
        # an assistant message envelope is appended to the session queue. Lets the
        # permission callback wait for "this tool's owning message was queued" before
        # appending the awaiting_permission update, so the frontend always sees the
        # assistant bubble ahead of its permission prompt. A dedicated primitive (not a
        # reuse of _tool_call_events) to avoid coupling two unrelated signals.
        self._message_emitted_events: dict[str, asyncio.Event] = {}
        self._emitted_turn_ids: dict[str, set[str]] = {}

        # Permission callback factory (injected by web_server)
        # Allows legion components to create permission callbacks on-demand
        self._permission_callback_factory: Callable[[str], Callable] | None = None

        # Message callback registrar (injected by web_server)
        # Allows legion components to register WebSocket message callbacks
        self._message_callback_registrar: Callable[[str], None] | None = None

        # Initialize Legion multi-agent system. Needs `self` (session_port=self),
        # so it's built via a helper called here rather than from
        # build_core_managers() above — see coordinator_builder.build_legion_system().
        self.legion_system = build_legion_system(
            coordinator=self,
            data_dir=self.data_dir,
            template_manager=self.template_manager,
            tenant_context=self.tenant_context,
        )

        # Issue #500: Message queue system. queue_manager/pending_store were
        # built unwired by build_core_managers() above; queue_processor needs
        # `self` so it stays here.
        self.queue_processor = QueueProcessor(self)

        # Issue #2189 (coordinator-split PR 10): consolidated delivery gate
        self.delivery_gate = DeliveryGate(self)

        # Issue #1125: Per-session token usage analytics (injected by web_server via set_analytics_store)
        self.analytics_store = None
        # Per-session turn sequence counters (rehydrated lazily from DB on first use)
        self._turn_seq_by_session: dict[str, int] = {}
        # Issue #1838: last-seen raw cumulative usage/cost snapshot per session,
        # used to compute true per-turn deltas from the SDK's cumulative-since-
        # subprocess-start counters. Reset at every new-SDK-creation epoch boundary.
        self._usage_baseline_by_session: dict[str, dict[str, float]] = {}
        # Issue #1831: SDK-reported model from the most recent assistant message of
        # the current in-flight turn. Cleared once consumed by that turn's result
        # handler, so a turn with no assistant message (e.g. an immediate
        # synthetic/error result) correctly yields no fallback instead of a stale
        # value from a previous turn.
        self._last_turn_model_by_session: dict[str, str] = {}
        # Issue #1840: running sum of subagent (Task/Agent tool) usage for the
        # in-flight turn, keyed by session. Each subagent AssistantMessage's usage
        # is a per-call delta (unlike the main thread's cumulative snapshots), so
        # it's summed directly rather than run through _delta_from_baseline().
        # Popped and merged into the turn's usage_delta on 'result', or flushed as
        # a catch-up row on session termination if a run_in_background subagent
        # outlives its originating turn.
        self._subagent_usage_by_session: dict[str, dict[str, float]] = {}
        # Callback for broadcasting usage_updated events (injected by web_server)
        self._usage_broadcast_callback: Callable[[str, dict], None] | None = None
        # Issue #2190: most recent synthetic (model == "<synthetic>") assistant text
        # and its timestamp, keyed by session — a usage-limit notice precedes its
        # ResultMessage within the same turn. Popped unconditionally by the 'result'
        # branch of _create_message_callback for every result (not just a 429
        # match), since a stashed notice is only ever paired with the one result
        # immediately following it — same per-session-dict pattern as
        # _last_turn_model_by_session.
        self._last_synthetic_text_by_session: dict[str, tuple[str, float]] = {}

        # Issue #404: Resource MCP tools for displaying resources in task panel
        # Callback for broadcasting resource_registered events will be set by web_server
        self.resource_mcp_tools = ResourceMCPTools(
            session_coordinator=self,
            broadcast_callback=self._broadcast_resource_registered
        )

        # Issue #1530: Links MCP tools for registering persistent named links
        # Callback for broadcasting link_registered events will be set by web_server
        self.links_mcp_tools = LinksMCPTools(
            session_coordinator=self,
            broadcast_callback=self._broadcast_link_registered
        )

        # Issue #2189 (coordinator-split PR 1): the session launch/teardown cluster
        # (start/terminate/restart/reset, MCP config resolution, secret placeholder
        # substitution) lives on SessionLauncher now. Constructed last — after every
        # collaborator manager above exists — and receives the per-session dicts
        # above BY REFERENCE (not copies): they stay constructed on this coordinator
        # since code not yet extracted (message-pipeline closures, delete_session,
        # resource_mcp_tools.py's direct _active_sdks reach-in) mutates them too.
        # Issue #2189: wrapped in lambdas rather than passed as bound methods
        # directly — a bound method captures the method object that is current
        # AT THIS LINE, so a test that does `patch.object(coordinator,
        # "mark_session_tools_interrupted")` AFTER construction would silently
        # fail to intercept calls the launcher makes through a pre-captured bound
        # method (found while verifying 70+ existing tests stay unaffected: two
        # tests patch `mark_session_tools_interrupted` post-construction). A
        # lambda defers the `self.X` attribute lookup to call time, so it always
        # sees whatever is currently set on `self` — including a test's patch.
        # Issue #2189: plain bound-method references for 13 of the 14 fields — each
        # of these is only ever invoked directly at a documented call site in
        # session_launcher.py, and none of them is reassigned/patched on the
        # coordinator post-construction anywhere in the test suite (verified).
        # `on_rate_limits` is the one field handed to ClaudeSDK BY REFERENCE and
        # invoked later via `_safe_callback`'s `asyncio.iscoroutinefunction(callback)`
        # check — wrapping an async method in a plain lambda would make that check
        # return False (a lambda is never a coroutine function, even though its body
        # calls one), silently skipping the `await` and discarding the real
        # `_on_rate_limits` coroutine unexecuted. A nested `async def` wrapper
        # preserves correct `iscoroutinefunction` detection (True) while still
        # deferring the `self._on_rate_limits` lookup to call time.
        async def _on_rate_limits_dispatch(rate_limit_info):
            return await self._message_pipeline.on_rate_limits(rate_limit_info)

        # Issue #2189 (coordinator-split PR 3): the ToolCall lifecycle tracking
        # cluster (creation, permission request/response, running/result/input
        # updates, interruption, snapshot application) lives on ToolCallTracker
        # now. Constructed BEFORE both message_pipeline_callbacks and
        # launcher_callbacks below, since both bundles retarget a field to it.
        # Unlike SessionLauncher/MessagePipeline, this is the terminal content
        # extraction — nothing it calls is still stranded on the coordinator —
        # so no Callbacks dataclass is needed here.
        self._tool_call_tracker = ToolCallTracker(coordinator=self)

        # Issue #2189 (coordinator-split PR 2): the message-pipeline cluster
        # (SDK message/error/stderr/phase callback factories, auto-resume
        # scheduling, lifecycle system-message senders) lives on MessagePipeline
        # now. Constructed BEFORE launcher_callbacks below, since 10 of its 14
        # fields retarget from `self._create_X` to `self._message_pipeline.X`.
        message_pipeline_callbacks = MessagePipelineCallbacks(
            # Issue #2189: lambda for the same late-binding/test-patch-staleness
            # reason PR 1 used one for this exact method on LauncherCallbacks —
            # defers the `self.X` attribute lookup to call time so a test that
            # patches `coordinator.mark_session_tools_interrupted` post-construction
            # is still seen. Retargeted (PR 3) to the tracker now that
            # mark_session_tools_interrupted lives there.
            mark_session_tools_interrupted=lambda session_id: self._tool_call_tracker.mark_session_tools_interrupted(session_id),
        )
        self._message_pipeline = MessagePipeline(
            coordinator=self,
            callbacks=message_pipeline_callbacks,
        )

        launcher_callbacks = LauncherCallbacks(
            create_message_callback=self._message_pipeline.create_message_callback,
            create_error_callback=self._message_pipeline.create_error_callback,
            create_stderr_callback=self._message_pipeline.create_stderr_callback,
            create_phase_callback=self._message_pipeline.create_phase_callback,
            # Retargeted (PR 3): _create_auto_approval_callback now lives on
            # ToolCallTracker. Already a plain bound-method capture (not
            # reassigned/patched on the coordinator post-construction anywhere
            # in the test suite), so no lambda wrapper is needed here.
            create_auto_approval_callback=self._tool_call_tracker._create_auto_approval_callback,
            on_rate_limits=_on_rate_limits_dispatch,
            transition_to_error=self._message_pipeline.transition_to_error,
            send_session_failure_message=self._message_pipeline.send_session_failure_message,
            send_client_launched_message=self._message_pipeline.send_client_launched_message,
            send_mcp_degraded_message=self._message_pipeline.send_mcp_degraded_message,
            result_error_structured_kwargs=self._message_pipeline.result_error_structured_kwargs,
            extract_claude_cli_error=self._message_pipeline.extract_claude_cli_error,
            # Issue #2189: the one field with a demonstrated staleness bug — two
            # existing tests do `patch.object(coordinator, "mark_session_tools_interrupted")`
            # AFTER construction, which a pre-captured bound method would never see.
            # A lambda defers the `self.X` attribute lookup to call time instead.
            # Retargeted (PR 3) to the tracker now that mark_session_tools_interrupted
            # lives there — the test patch sites were updated accordingly
            # (patch.object(coord._tool_call_tracker, "mark_session_tools_interrupted")).
            mark_session_tools_interrupted=lambda session_id: self._tool_call_tracker.mark_session_tools_interrupted(session_id),
            notify_session_reset=self._notify_session_reset,
        )
        self._session_launcher = SessionLauncher(
            coordinator=self,
            callbacks=launcher_callbacks,
        )

        # Issue #2189 (coordinator-split PR 4): the archive-query block,
        # archive_and_clear_session, and the compaction/distillation pair
        # live on ArchiveCoordinator now. No Callbacks bundle needed — every
        # coordinator dependency it calls is reached directly via
        # self._coordinator.X (and, since PR 5, a direct import for the two
        # resource-formatting helpers — see session_resources.py).
        self._archive_coordinator = ArchiveCoordinator(coordinator=self)

        # Issue #2189 (coordinator-split PR 5): the module-level resource-
        # formatting helpers, resource-gallery CRUD/broadcast, proxy-log
        # reading, and uploaded-file tracking live on SessionResources now.
        # No Callbacks bundle needed — nothing it calls is still stranded on
        # the coordinator.
        self._session_resources = SessionResources(coordinator=self)

        # Issue #2189 (coordinator-split PR 6): session-info/listing response
        # assembly (SDK status, message counts, effective config) and the
        # task-leg-registry pair live on SessionQuery now. No Callbacks bundle
        # needed — nothing it calls is still stranded on the coordinator.
        self._session_query = SessionQuery(coordinator=self)

    # Issue #2189 (coordinator-split PR 1): _default_sdk_factory/_sdk_factory now
    # live on SessionLauncher. Re-exported here (same function object, and a
    # read/write property delegating to the launcher) so existing tests that poke
    # `coordinator._sdk_factory` directly, or assert
    # `coordinator._sdk_factory is SessionCoordinator._default_sdk_factory`,
    # keep working unchanged. Re-wrapped in staticmethod() — without it, instance
    # access (`coordinator._default_sdk_factory(...)`) would silently become a
    # bound method (self inserted as the first positional arg), shifting every
    # argument by one position.
    _default_sdk_factory = staticmethod(SessionLauncher._default_sdk_factory)

    @property
    def _sdk_factory(self):
        return self._session_launcher._sdk_factory

    @_sdk_factory.setter
    def _sdk_factory(self, factory):
        self._session_launcher._sdk_factory = factory

    def set_sdk_factory(self, factory):
        """Set custom SDK factory for testing (e.g., MockClaudeSDK)."""
        self._session_launcher.set_sdk_factory(factory)

    def get_session_recorder(self, session_id: str) -> SessionRecorder | None:
        """Issue #1998: the active SessionRecorder for a session, or None if it isn't
        recording. Used by EventQueue's on_append hook and PermissionService's pause
        lifecycle recording — both live outside start_session()'s own creation path.
        """
        return self._session_launcher.get_session_recorder(session_id)

    def set_analytics_store(self, store) -> None:
        """Inject the AnalyticsStore instance (issue #1125)."""
        self.analytics_store = store

    def set_permission_service(self, permission_service) -> None:
        """Inject the PermissionService instance (issue #2159).

        PermissionService's constructor already takes `coordinator` as a
        back-reference; this is the other half of that pair, needed so
        interrupt_session() can deny/resolve a PAUSED session's own pending
        permission(s) without every caller (REST router, comm_router's HALT/PIVOT
        path) having to reach into PermissionService itself.
        """
        self.permission_service = permission_service

    async def set_audit_writer(self, writer) -> None:
        """Wire an AuditWriter into all existing and future storage managers.

        Issue #2127: also drains `_crash_repair_audit_backlog`, replaying any
        boot-time crash-repair records queued by `_repair_crash_orphaned_tool_calls()`
        before this method was ever called — see that method's docstring for why the
        backlog exists.
        """
        self._audit_writer = writer
        for session_id, manager in list(self._storage_managers.items()):
            self._apply_audit_writer(manager, session_id)

        if self._crash_repair_audit_backlog:
            backlog, self._crash_repair_audit_backlog = self._crash_repair_audit_backlog, []
            for session_id, record in backlog:
                project_id = self._resolve_project_id(session_id)
                await writer.on_message_append(session_id, project_id, record)

    def _resolve_project_id(self, session_id: str) -> str | None:
        """Look up the project_id of an active session, or None if not found."""
        session = self.session_manager._active_sessions.get(session_id)
        return session.project_id if session else None

    def _apply_history_index_feed(self, storage_manager, session_id: str) -> None:
        """Issue #2030 (stage 2): feed every durable append to the session's history index."""
        self._session_query.attach_history_index_feed(storage_manager, session_id)

    def _apply_audit_writer(self, storage_manager, session_id: str) -> None:
        """Configure audit callbacks on a newly created DataStorageManager."""
        self._apply_history_index_feed(storage_manager, session_id)
        if self._audit_writer is None:
            return
        storage_manager._session_id = session_id
        storage_manager._project_id = self._resolve_project_id(session_id)
        if self._audit_writer.on_message_append not in storage_manager.on_append:
            storage_manager.on_append.append(self._audit_writer.on_message_append)

    # Issue #2189 (coordinator-split PR 1): MCP config resolution / secret
    # placeholder substitution moved to SessionLauncher — one-line delegates.
    async def _get_mcp_sdk_config(self, mcp_cfg, name_to_placeholder=None):
        return await self._session_launcher._get_mcp_sdk_config(mcp_cfg, name_to_placeholder)

    async def _generate_secret_placeholders(self, session_id, session_info, resolved_metas):
        return await self._session_launcher._generate_secret_placeholders(
            session_id, session_info, resolved_metas
        )

    def _substitute_secret_refs(self, headers, name_to_placeholder, mcp_server_name):
        return self._session_launcher._substitute_secret_refs(
            headers, name_to_placeholder, mcp_server_name
        )

    async def initialize(self):
        """Initialize the session coordinator"""
        try:
            await self.session_manager.initialize()
            await self._repair_crash_orphaned_tool_calls()
            await self.project_manager.initialize()

            # Load templates from disk
            await self.template_manager.load_templates()
            # Create default templates if none exist
            await self.template_manager.create_default_templates()
            coord_logger.info("Loaded minion templates")

            # Load configuration profiles from disk (issue #1062).
            # Pass template_manager so the isolation->features field migration
            # (issue #1707) can scan already-loaded templates for references.
            await self.profile_manager.load_profiles(template_manager=self.template_manager)
            coord_logger.info("Loaded configuration profiles")
            await self._migrate_stale_mcp_snapshots()  # issue #1660

            # Load global MCP server configs (issue #676)
            await self.mcp_config_manager.load_configs()
            coord_logger.info("Loaded global MCP server configs")

            # Load global hook configs (issue #1629)
            await self.hook_config_manager.load_configs()
            coord_logger.info("Loaded global hook configs")

            # Load provider catalog from data/providers.json (issue #1465)
            await self.provider_catalog_store.load()
            coord_logger.info("Loaded provider catalog")

            # Rebuild capability registry from persisted session data (if LegionSystem is initialized)
            if hasattr(self, 'legion_system') and self.legion_system is not None:
                await self.legion_system.legion_coordinator.rebuild_capability_registry()

            # Register callback to receive session manager state changes
            self.session_manager.add_state_change_callback(self._on_session_manager_state_change)
            # Issue #2159: same wiring for metadata-only updates (name, model,
            # permission_mode, etc.) — a separate callback/event stream from state_change.
            self.session_manager.add_metadata_change_callback(self._on_session_manager_metadata_change)
            # Issue #2184: same wiring for is_processing-only updates — a separate
            # callback/event stream from both of the above.
            self.session_manager.add_processing_change_callback(self._on_session_manager_processing_change)
            # Issue #2184: the recorder lookup + record_lifecycle(action) + exception
            # handling previously duplicated across permission_service.py's 2 manual
            # pause/resume call sites (now deleted) and this file's own start/terminate
            # call sites (also deleted — see start_session()/terminate_session()).
            self.session_manager.add_lifecycle_record_callback(self._on_session_manager_lifecycle_record)

            # Initialize storage managers for all existing sessions
            await self._initialize_existing_session_storage()

            # Issue #500: Load message queues for all existing sessions
            await self._initialize_queues()

            # Validate and cleanup orphaned project/session references (issue #63)
            await self._validate_and_cleanup_projects()

            # Load and start scheduler service (issue #495)
            if hasattr(self, 'legion_system') and self.legion_system is not None:
                await self.legion_system.scheduler_service.load_all_schedules()
                await self.legion_system.scheduler_service.start()
                await self.legion_system.history_rotator.start()

            coord_logger.info("Session coordinator initialized successfully")
        except Exception:
            logger.exception("Failed to initialize session coordinator")
            raise

    async def _repair_crash_orphaned_tool_calls(self) -> None:
        """Issue #2189 (coordinator-split PR 7): relocated to ToolCallTracker —
        one-line delegate."""
        await self._tool_call_tracker._repair_crash_orphaned_tool_calls()

    async def _initialize_existing_session_storage(self):
        """Initialize storage managers for all existing sessions"""
        try:
            # Get all existing sessions
            sessions = await self.session_manager.list_sessions()
            # logger.info(f"Initializing storage managers for {len(sessions)} existing sessions")

            for session in sessions:
                session_id = session.session_id
                if session_id not in self._storage_managers:
                    # Create storage manager for this session
                    session_dir = await self.session_manager.get_session_directory(session_id)
                    storage_manager = DataStorageManager(session_dir)
                    await storage_manager.initialize()
                    self._storage_managers[session_id] = storage_manager
                    self._apply_audit_writer(storage_manager, session_id)
                    # logger.info(f"Initialized storage manager for session {session_id}")

        except Exception:
            logger.exception("Failed to initialize storage managers for existing sessions")
            # Don't raise - this shouldn't block coordinator initialization

    async def _initialize_queues(self):
        """Load message queues for all existing sessions on startup."""
        await self.queue_processor._initialize_queues()

    # =========================================================================
    # Message Queue Operations (Issue #500)
    # =========================================================================

    async def enqueue_message(
        self,
        session_id: str,
        content: str,
        reset_session: bool | None = None,
        metadata: dict | None = None,
        inject_timestamp: bool = True,
        sender_type: SenderType = SenderType.USER,
    ) -> dict:
        """Enqueue a message for a session (issue #2193 PR6). sender_type defaults to
        USER (the manual queue-message REST endpoint's producer identity) — scheduler
        and resume_all() pass SCHEDULE/SYSTEM explicitly.

        auto_start=True (matching routers/legion.py's and legion_mcp_tools.py's own
        PR5 Comm construction): queue_processor.py's prepare_for_send() always
        auto-started a dormant (CREATED/TERMINATED) target unconditionally — without
        this, _ensure_session_ready()'s AC8 gate would fail every enqueue onto a
        stopped session with "auto_start was not requested," a live regression from
        today's behavior (confirmed via a scratch integration test during review).

        Raises ValueError on a deliver() failure that never actually enqueued the
        comm (e.g. a full pending store, mirroring queue_manager.enqueue()'s own
        "Queue full" raise) — matches the old queue_processor.enqueue_message()
        behavior of letting that ValueError propagate to routers/queue.py's
        handle_exceptions(..., value_error_status=400), instead of silently
        falling back to a position-less dict (found in review: that fallback
        would otherwise 500 on routers/queue.py's unconditional item["position"]
        read).
        """
        session_info = await self.session_manager.get_session_info(session_id)
        if not session_info:
            raise ValueError(f"Session {session_id} not found")
        queue_config = getattr(session_info, 'queue_config', None) or {}
        if reset_session is None:
            reset_session = queue_config.get("default_reset_session", True)
        max_queue_size = queue_config.get("max_queue_size", 100)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            sender_type=sender_type,  # explicit — overrides __post_init__'s from_user-based inference
            to_minion_id=session_id,
            content=content,
            comm_type=CommType.SYSTEM,
            delivery=DeliveryPolicy.WHEN_IDLE,
            reset_first=reset_session,
            auto_start=True,
            metadata=metadata or {},
        )
        result = await self.delivery_gate.deliver(
            comm, inject_timestamp=inject_timestamp, max_pending_size=max_queue_size,
        )
        item = self.pending_store.get_item(session_id, comm.comm_id)
        if item is None:
            raise ValueError(result.error or "Failed to enqueue message")
        return item.to_legacy_queue_dict()

    async def cancel_queue_item(self, session_id: str, queue_id: str) -> dict | None:
        """Cancel a pending queue item. Returns the item dict or None."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return None
        item = await self.pending_store.cancel(session_id, session_dir, queue_id)
        return item.to_legacy_queue_dict() if item else None

    async def requeue_item(self, session_id: str, queue_id: str) -> dict | None:
        """Re-queue a sent/failed item at the front. Returns the new item dict."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return None
        item = await self.pending_store.requeue(session_id, session_dir, queue_id)
        return item.to_legacy_queue_dict() if item else None

    async def clear_queue(self, session_id: str) -> int:
        """Clear all pending queue items. Returns count of cancelled items."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return 0
        return await self.pending_store.clear_pending(session_id, session_dir)

    async def clear_queue_history(self, session_id: str) -> int:
        """Remove terminal (sent/failed/cancelled) queue items. Returns count removed."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return 0
        return await self.pending_store.clear_history(session_id, session_dir)

    async def get_queue(self, session_id: str, limit: int = 100, offset: int = 0) -> dict:
        """Get queue items for a session, paginated, most recent first."""
        return self.pending_store.get_page(session_id, limit=limit, offset=offset, legacy_shape=True)

    async def pause_queue(self, session_id: str, paused: bool) -> bool:
        """Pause or resume the queue for a session."""
        success = await self.session_manager.update_session(session_id, queue_paused=paused)
        if success and not paused:
            when_idle_pending = any(
                i.comm.delivery == DeliveryPolicy.WHEN_IDLE
                for i in self.pending_store.get_pending(session_id)
            )
            if when_idle_pending:
                self.delivery_gate.ensure_when_idle_running(session_id)
        return success

    async def update_queue_config(self, session_id: str, config: dict) -> bool:
        """Update queue configuration for a session."""
        return await self.session_manager.update_queue_config(session_id, config)

    async def create_session(
        self,
        session_id: str,
        project_id: str,
        config: SessionConfig,
        name: str | None = None,
        permission_callback: Callable[[str, dict[str, Any]], bool | dict[str, Any]] | None = None,
        role: str | None = None,
        capabilities: list[str] = None,
        parent_overseer_id: str | None = None,
        overseer_level: int = 0,
        can_spawn_minions: bool = True,
    ) -> str:
        """Create a new Claude Code session with integrated components (within a project)"""
        try:
            # Get project to determine working directory and multi-agent status
            project = await self.project_manager.get_project(project_id)
            if not project:
                raise ValueError(f"Project {project_id} not found")

            # Use custom working directory if provided, otherwise use project directory
            effective_working_directory = config.working_directory or project.working_directory

            # Calculate order based on existing sessions in project
            order = len(project.session_ids)

            # Issue #349: All sessions are minions (is_minion field removed)

            # Build a config copy with resolved working directory for session manager
            sm_config = config.model_copy(update={"working_directory": effective_working_directory})

            # Create session through session manager
            # Store raw system_prompt (guide will be prepended later in start_session)
            await self.session_manager.create_session(
                session_id=session_id,
                config=sm_config,
                name=name,
                order=order,
                project_id=project_id,
                role=role or "assistant",
                capabilities=capabilities,
                parent_overseer_id=parent_overseer_id,
                overseer_level=overseer_level,
                can_spawn_minions=can_spawn_minions,
            )

            # If template-linked, diff creation-time config against template defaults
            # and store non-matching fields as initial session_overrides (#1079).
            if config.template_id:
                await self._init_session_overrides(session_id, config)
                sinfo = await self.session_manager.get_session_info(session_id)
                if sinfo:
                    await self._prune_inherited_mcp_snapshot(sinfo)  # issue #1660

            # Add session to project
            await self.project_manager.add_session_to_project(project_id, session_id)

            # Initialize storage manager for this session
            session_dir = await self.session_manager.get_session_directory(session_id)
            storage_manager = DataStorageManager(session_dir)
            await storage_manager.initialize()
            self._storage_managers[session_id] = storage_manager
            self._apply_audit_writer(storage_manager, session_id)

            # Initialize callback lists
            self._message_callbacks[session_id] = []
            self._error_callbacks[session_id] = []

            coord_logger.info(f"Session {session_id} created in project {project_id}")
            await self._notify_state_change(session_id, SessionState.CREATED)

            return session_id

        except Exception:
            logger.exception("Failed to create integrated session")
            raise

    async def get_session_storage(self, session_id: str):
        """Get storage manager for a session"""
        return self._storage_managers.get(session_id)

    async def get_or_create_storage_manager(self, session_id: str) -> DataStorageManager | None:
        """Issue #2084 (stage 3-C): return the session's cached DataStorageManager if
        one is active, else construct and cache one.

        A session isn't guaranteed to have a live storage manager (only loaded while
        the session is active/starting — see the `del self._storage_managers[...]`
        sites), but migration (background or on-demand) needs the one real
        `_write_lock` instance for a session regardless of whether it's currently
        running, so a concurrent live start always finds and serializes against the
        exact same lock instance this call caches.

        The whole check-construct-cache body runs under SessionManager's existing
        per-session lock (`_get_session_lock` — the same lock try_claim_message_
        migration() already uses for its own atomicity). Without it, two concurrent
        first-time callers (e.g. the background migration tick and a user's
        start_session() racing on a session that's never had a storage manager
        cached) could both see no cached instance, each construct their own
        independent DataStorageManager with its own independent, unlocked
        _write_lock, and silently orphan whichever one loses the final dict-assignment
        race — reintroducing, one layer up, the exact lock-identity bug this method
        exists to prevent (found in review).
        """
        async with self.session_manager._get_session_lock(session_id):
            storage = self._storage_managers.get(session_id)
            if storage:
                return storage
            session_dir = await self.session_manager.get_session_directory(session_id)
            if not session_dir:
                return None
            storage = DataStorageManager(session_dir)
            await storage.initialize()
            self._storage_managers[session_id] = storage
            return storage

    async def get_session_resources(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        format_filter: str | None = None,
        sort: str = "newest",
    ) -> dict:
        return await self._session_resources.get_session_resources(
            session_id, limit, offset, search, format_filter, sort
        )

    async def get_session_resource_by_id(self, session_id: str, resource_id: str) -> dict | None:
        return await self._session_resources.get_session_resource_by_id(session_id, resource_id)

    async def get_session_resource_file(self, session_id: str, resource_id: str) -> bytes | None:
        return await self._session_resources.get_session_resource_file(session_id, resource_id)

    async def get_proxy_logs(self, session_id: str, log_type: str = "http", limit: int = 200) -> dict:
        return await self._session_resources.get_proxy_logs(session_id, log_type, limit)

    async def remove_session_resource(self, session_id: str, resource_id: str) -> bool:
        return await self._session_resources.remove_session_resource(session_id, resource_id)

    def set_permission_callback_factory(self, factory: Callable[[str], Callable]) -> None:
        """
        Set the permission callback factory for creating callbacks on demand.

        This factory is used by legion components (overseer_controller, comm_router)
        to create permission callbacks for spawned minions.

        Args:
            factory: Function that takes session_id and returns permission_callback
        """
        self._permission_callback_factory = factory
        coord_logger.info("Permission callback factory registered in SessionCoordinator")

        # Propagate to legion_system if it exists
        if self.legion_system:
            self.legion_system.permission_callback_factory = factory
            coord_logger.info("Permission callback factory propagated to LegionSystem")

    def set_message_callback_registrar(self, registrar: Callable[[str], None]) -> None:
        """
        Set the message callback registrar for registering WebSocket callbacks.

        This registrar is used by legion components (overseer_controller, comm_router)
        to register WebSocket message callbacks for spawned minions.

        Args:
            registrar: Function that takes session_id and registers message callback
        """
        self._message_callback_registrar = registrar
        coord_logger.info("Message callback registrar registered in SessionCoordinator")

        # Propagate to legion_system if it exists
        if self.legion_system:
            self.legion_system.message_callback_registrar = registrar
            coord_logger.info("Message callback registrar propagated to LegionSystem")

    def set_resource_broadcast_callback(self, callback: Callable[[str, dict], None]) -> None:
        return self._session_resources.set_resource_broadcast_callback(callback)

    # Backward compatibility alias
    def set_image_broadcast_callback(self, callback: Callable[[str, dict], None]) -> None:
        return self._session_resources.set_image_broadcast_callback(callback)

    def set_link_broadcast_callback(self, callback: Callable[[str, dict], None]) -> None:
        return self._session_resources.set_link_broadcast_callback(callback)

    def set_enqueue_broadcast_callback(self, callback: Callable[[str, str, dict], Any]) -> None:
        """Set the callback for broadcasting queue_update (enqueued) events."""
        self.queue_processor.set_enqueue_broadcast_callback(callback)

    async def _broadcast_resource_registered(self, session_id: str, resource_metadata: dict) -> None:
        return await self._session_resources._broadcast_resource_registered(session_id, resource_metadata)

    async def _broadcast_link_registered(self, session_id: str, link: dict) -> None:
        return await self._session_resources._broadcast_link_registered(session_id, link)

    async def get_session_links(self, session_id: str) -> list[dict]:
        return await self._session_resources.get_session_links(session_id)

    # Issue #2189 (coordinator-split PR 1): session launch moved to
    # SessionLauncher — one-line delegate.
    async def start_session(
        self,
        session_id: str,
        permission_callback: Callable[[str, dict[str, Any]], bool | dict[str, Any]] | None = None,
        _lifecycle_action: str = "start",
    ) -> bool:
        return await self._session_launcher.start_session(
            session_id, permission_callback, _lifecycle_action
        )

    # Issue #2189 (coordinator-split PR 1): moved to SessionLauncher —
    # one-line delegates. flush_unflushed_subagent_usage renamed public
    # (from _flush_unflushed_subagent_usage) since delete_session, staying on
    # this coordinator, is a legitimate cross-module caller.
    async def _next_turn_seq(self, session_id: str) -> int:
        return await self._session_launcher._next_turn_seq(session_id)

    async def flush_unflushed_subagent_usage(self, session_id: str, allow_retry: bool = True) -> None:
        return await self._session_launcher.flush_unflushed_subagent_usage(session_id, allow_retry)

    async def terminate_session(self, session_id: str) -> bool:
        """Terminate a session and cleanup resources"""
        return await self._session_launcher.terminate_session(session_id)

    async def disconnect_sdk(self, session_id: str) -> bool:
        """Disconnect the active SDK for a session without full termination."""
        return await self._session_launcher.disconnect_sdk(session_id)

    # =========================================================================
    # Ephemeral Session Lifecycle (Issue #578)
    # =========================================================================

    async def create_ephemeral_session(
        self,
        session_config: dict,
        schedule_name: str,
        project_id: str,
        permission_callback=None,
    ) -> str:
        """Create a temporary session from a schedule's stored config.

        Args:
            session_config: Dict with session configuration fields (model, permission_mode, etc.)
            schedule_name: Name of the schedule (used in session name)
            project_id: Legion/project ID to add the session to

        Returns:
            The new session_id
        """
        session_id = str(uuid.uuid4())

        # Issue #498 Phase 1 bugfix: create_session() takes a single bundled
        # `config: SessionConfig`, not individual flat kwargs — this previously
        # passed permission_mode/system_prompt/etc. directly, which create_session()
        # doesn't accept at all (TypeError on any real call). Untested/dead until now.
        config = SessionConfig(
            permission_mode=session_config.get("permission_mode", "acceptEdits"),
            system_prompt=session_config.get("system_prompt"),
            override_system_prompt=session_config.get("override_system_prompt", False),
            allowed_tools=session_config.get("allowed_tools"),
            disallowed_tools=session_config.get("disallowed_tools"),
            model=session_config.get("model"),
            working_directory=session_config.get("working_directory"),
            sandbox_enabled=session_config.get("sandbox_enabled", False),
            setting_sources=session_config.get("setting_sources"),
            docker_enabled=session_config.get("docker_enabled", False),
            docker_image=session_config.get("docker_image"),
            docker_extra_mounts=session_config.get("docker_extra_mounts"),
            thinking_mode=session_config.get("thinking_mode"),
            thinking_budget_tokens=session_config.get("thinking_budget_tokens"),
            effort=session_config.get("effort"),
        )

        await self.create_session(
            session_id=session_id,
            project_id=project_id,
            config=config,
            name=f"[Scheduled] {schedule_name}",
            permission_callback=permission_callback,
        )

        # Mark session as ephemeral and broadcast the updated state so the
        # frontend receives is_ephemeral=True (the initial create_session broadcast
        # fires before this flag is set, creating a race condition).
        session_info = await self.session_manager.get_session_info(session_id)
        if session_info:
            session_info.is_ephemeral = True
            session_info.updated_at = datetime.now(UTC)
            await self.session_manager._persist_session_state(session_id)
            # Issue #2159: this is a metadata-only flag flip, not a SessionState
            # transition — fires session_metadata_updated, not state_change.
            await self.session_manager._notify_metadata_change_callbacks(session_id)

        coord_logger.info(
            f"Created ephemeral session {session_id} for schedule '{schedule_name}' "
            f"in project {project_id}"
        )
        return session_id

    async def archive_and_clear_session(self, session_id: str) -> bool:
        return await self._archive_coordinator.archive_and_clear_session(session_id)

    async def update_session_name(self, session_id: str, name: str) -> bool:
        """Update session name"""
        try:
            # Issue #2159: session_manager.update_session_name() already notifies via
            # its own metadata-change callback chain — this used to ALSO fire a
            # redundant explicit _notify_state_change() on top of that (and on the
            # wrong event type: name is metadata, not a SessionState transition).
            success = await self.session_manager.update_session_name(session_id, name)
            coord_logger.info(f"Updated session {session_id} name to '{name}'")
            return success
        except Exception:
            logger.exception(f"Failed to update session {session_id} name")
            return False

    async def _cascade_delete_children(
        self, session_id: str, archive_reason: str, session_info: SessionInfo | None,
    ) -> list[str]:
        """Step 0: recursively delete all children first (cascading). Checks
        child_minion_ids regardless of is_minion/is_overseer flags."""
        if not (session_info and session_info.child_minion_ids):
            return []
        child_ids = list(session_info.child_minion_ids)  # Make a copy
        return await self._cascade_delete_each_child(session_id, child_ids, archive_reason)

    async def _cascade_delete_each_child(
        self, session_id: str, child_ids: list[str], archive_reason: str,
    ) -> list[str]:
        """Step 0 continued: delete each child, logging the cascade boundaries."""
        coord_logger.info(f"Session {session_id} has {len(child_ids)} children - cascading deletion")
        deleted_ids: list[str] = []
        for child_id in child_ids:
            deleted_ids.extend(await self._cascade_delete_one_child(session_id, child_id, archive_reason))
        coord_logger.info(f"Cascading deletion complete for {session_id} - all {len(child_ids)} children deleted")
        return deleted_ids

    async def _cascade_delete_one_child(
        self, parent_session_id: str, child_id: str, archive_reason: str,
    ) -> list[str]:
        """Step 0 continued: delete a single cascaded child."""
        coord_logger.info(f"Cascading: deleting child minion {child_id} of parent {parent_session_id}")
        result = await self.delete_session(child_id, archive_reason=archive_reason)
        return result.get("deleted_session_ids", []) if result.get("success") else []

    async def _detach_session_from_project(self, session_id: str) -> ProjectInfo | None:
        """Step 1: find and remove session from its project."""
        project = await self.find_project_for_session(session_id)
        if project:
            await self._remove_and_log_project_detach(session_id, project)
        return project

    async def _remove_and_log_project_detach(self, session_id: str, project: ProjectInfo) -> None:
        """Step 1 continued: remove from the project, then log/cleanup the outcome."""
        removal_success, project_was_deleted = await self.project_manager.remove_session_from_project(
            project.project_id, session_id
        )
        self._log_project_detach_outcome(session_id, project, removal_success, project_was_deleted)
        if removal_success and not project_was_deleted:
            # Issue #1722: strip the deleted session's kanban group assignment
            await self.project_manager.cleanup_session_group_assignment(project.project_id, session_id)

    def _log_project_detach_outcome(
        self, session_id: str, project: ProjectInfo, removal_success: bool, project_was_deleted: bool,
    ) -> None:
        """Step 1 continued: log which of the three project-removal outcomes occurred."""
        if not removal_success:
            logger.warning(f"Failed to remove session {session_id} from project {project.project_id}")
        elif project_was_deleted:
            coord_logger.info(f"Removed session {session_id} from project {project.project_id} - project was empty and has been deleted")
        else:
            coord_logger.info(f"Removed session {session_id} from project {project.project_id}")

    async def _detach_session_from_parent(
        self, session_id: str, session_info: SessionInfo | None,
    ) -> SessionInfo | None:
        """Step 1.5: resolve session_info and detach from its parent overseer, if any."""
        if not session_info:
            session_info = await self.session_manager.get_session_info(session_id)
        if session_info and session_info.parent_overseer_id:
            await self._remove_from_parent_child_list(session_id, session_info.parent_overseer_id)
        return session_info

    async def _remove_from_parent_child_list(self, session_id: str, parent_id: str) -> None:
        """Step 1.5 continued: remove session_id from parent_id's child_minion_ids."""
        parent_info = await self.session_manager.get_session_info(parent_id)
        if not parent_info:
            coord_logger.warning(f"Parent overseer {parent_id} not found for minion {session_id}")
            return
        if session_id not in parent_info.child_minion_ids:
            coord_logger.warning(f"Minion {session_id} not found in parent overseer {parent_id}'s child_minion_ids")
            return
        await self._persist_child_removed_from_parent(session_id, parent_id, parent_info)

    async def _persist_child_removed_from_parent(
        self, session_id: str, parent_id: str, parent_info: SessionInfo,
    ) -> None:
        """Step 1.5 continued: persist the parent's child_minion_ids update and log it."""
        parent_info.child_minion_ids.remove(session_id)
        parent_info.updated_at = datetime.now(UTC)
        await self.session_manager._persist_session_state(parent_id)
        coord_logger.info(f"Removed minion {session_id} from parent overseer {parent_id}'s child_minion_ids")

    async def _delete_legion_schedules_for_session(self, session_id: str) -> None:
        """Step 1.55: delete schedules for deleted session (Issue #671)."""
        if not self.legion_system:
            return
        try:
            deleted = await self.legion_system.scheduler_service.delete_schedules_for_minion(session_id)
            if deleted:
                coord_logger.info(f"Deleted {deleted} schedules for deleted session {session_id}")
        except Exception as e:
            coord_logger.warning(f"Failed to delete schedules for session {session_id}: {e}")

    def _unregister_capability_registry(
        self, session_id: str, session_info: SessionInfo | None, project: ProjectInfo | None,
    ) -> None:
        """Step 1.6: clean up capability registry if session has capabilities
        (issue #349: all sessions are minions)."""
        if session_info and session_info.capabilities and project and self.legion_system:
            self.legion_system.legion_coordinator.unregister_minion_capabilities(session_id)
            coord_logger.info(f"Cleaned up {len(session_info.capabilities)} capabilities from registry for minion {session_id}")

    async def _teardown_sdk_before_archive(
        self, session_id: str, session_info: SessionInfo | None,
    ) -> None:
        """Step 1.65: terminate SDK and update state BEFORE archive (Issue #236)
        — ensures archive captures final "terminated" state (same pattern as
        dispose_minion). Gives the SDK time to fully close."""
        await self._session_launcher.teardown_active_sdk(session_id, graceful=False, settle_seconds=0.2)
        if session_info and session_info.state != SessionState.TERMINATED:
            await self.session_manager.terminate_session(session_id)
            coord_logger.info(f"Terminated session {session_id} before archive/deletion")

    async def _archive_session_before_delete(
        self, session_id: str, session_info: SessionInfo | None, project: ProjectInfo | None,
        archive_reason: str, descendants_count: int,
    ) -> None:
        """Step 1.7: archive any session in a project before deletion (Issue #236)."""
        if not (session_info and project and self.legion_system):
            return
        await self._try_archive_minion(session_id, session_info, archive_reason, descendants_count)

    async def _try_archive_minion(
        self, session_id: str, session_info: SessionInfo, archive_reason: str, descendants_count: int,
    ) -> None:
        """Step 1.7 continued: wraps archive_minion() in its own try/except so a
        single legion-archive failure doesn't abort the whole delete."""
        try:
            await self._run_archive_minion(session_id, session_info, archive_reason, descendants_count)
        except Exception:
            coord_logger.exception(f"Error archiving session {session_id} before deletion")

    async def _resolve_parent_name_for_archive(self, session_info: SessionInfo) -> str | None:
        """Step 1.7 continued: look up the parent overseer's display name, if any."""
        if not session_info.parent_overseer_id:
            return None
        parent_info = await self.session_manager.get_session_info(session_info.parent_overseer_id)
        return parent_info.name if parent_info else None

    async def _run_archive_minion(
        self, session_id: str, session_info: SessionInfo, archive_reason: str, descendants_count: int,
    ) -> None:
        """Step 1.7 continued: call archive_manager.archive_minion() and log the outcome."""
        kwargs = await self._build_archive_minion_kwargs(
            session_id, session_info, archive_reason, descendants_count
        )
        archive_result = await self.legion_system.archive_manager.archive_minion(**kwargs)
        self._log_archive_minion_result(session_id, archive_result)

    async def _build_archive_minion_kwargs(
        self, session_id: str, session_info: SessionInfo, archive_reason: str, descendants_count: int,
    ) -> dict:
        """Step 1.7 continued: assemble archive_minion()'s kwargs."""
        parent_name = await self._resolve_parent_name_for_archive(session_info)
        return {
            "minion_id": session_id, "reason": archive_reason, "will_be_deleted": True,
            "parent_overseer_id": session_info.parent_overseer_id, "parent_overseer_name": parent_name,
            "descendants_count": descendants_count,  # Children already deleted in cascade
        }

    def _log_archive_minion_result(self, session_id: str, archive_result) -> None:
        """Step 1.7 continued: log the archive_minion() call's success/failure."""
        if archive_result.success:
            coord_logger.info(f"Archived session {session_id} to {archive_result.archive_path} before deletion")
        else:
            coord_logger.warning(f"Failed to archive session {session_id}: {archive_result.error_message}")

    def _cleanup_legion_directory(
        self, session_id: str, session_info: SessionInfo | None, project: ProjectInfo | None,
    ) -> None:
        """Step 1.8: Legion-specific cleanup — delete the minion directory."""
        if not (session_info and project and self.legion_system):
            return
        legion_id = project.project_id
        coord_logger.info(f"Starting Legion-specific cleanup for minion {session_id} in legion {legion_id}")
        minion_dir = self.data_dir / "legions" / legion_id / "minions" / session_id
        self._rmtree_legion_minion_dir(minion_dir)

    def _rmtree_legion_minion_dir(self, minion_dir: Path) -> None:
        """Step 1.8 continued: delete the minion directory if present, tolerating failure."""
        if not minion_dir.exists():
            coord_logger.debug(f"Legion minion directory does not exist (already cleaned): {minion_dir}")
            return
        try:
            shutil.rmtree(minion_dir)
            coord_logger.info(f"Deleted Legion minion directory: {minion_dir}")
        except Exception:
            coord_logger.exception(f"Failed to delete Legion minion directory {minion_dir}")

    async def _cleanup_storage_manager(self, session_id: str) -> None:
        """Step 2: clean up storage manager and ensure all files are closed."""
        storage_manager = self._storage_managers.get(session_id)
        if storage_manager is not None:
            await storage_manager.cleanup()
            del self._storage_managers[session_id]
        self._clear_task_list(session_id)
        if storage_manager is not None:
            # Give storage manager time to close all file handles
            await asyncio.sleep(0.2)

    def _cleanup_in_memory_callback_state(self, session_id: str) -> None:
        """Step 3: clean up callbacks and other per-session in-memory state."""
        self._message_callbacks.pop(session_id, None)
        self._error_callbacks.pop(session_id, None)
        # Issue #858: Cleanup per-session tool-call event
        self._tool_call_events.pop(session_id, None)
        # Issue #1694: Cleanup per-session message-emitted barrier state
        self._message_emitted_events.pop(session_id, None)
        self._emitted_turn_ids.pop(session_id, None)

    async def _finalize_session_manager_delete(self, session_id: str) -> bool:
        """Step 4: delete through session manager, then flush analytics and notify."""
        success = await self.session_manager.delete_session(session_id)
        if not success:
            return False
        coord_logger.info(f"Session {session_id} deleted")
        await self._flush_analytics_and_notify_deleted(session_id)
        return True

    async def _flush_analytics_and_notify_deleted(self, session_id: str) -> None:
        """Step 4 continued: flush leftover subagent usage (issue #1840/#1941 —
        deletion no longer wipes analytics rows, so this is a real, permanent
        persistence opportunity; allow_retry=False since this session_id is gone
        for good right after), mark analytics deleted, and fire the dedicated
        session_deleted notification (issue #1986)."""
        await self.flush_unflushed_subagent_usage(session_id, allow_retry=False)
        await self._mark_analytics_deleted(session_id)
        await self._notify_session_deleted(session_id)

    async def _mark_analytics_deleted(self, session_id: str) -> None:
        """Step 4 continued: mark analytics deleted (issue #1941), tolerating failure."""
        if not self.analytics_store:
            return
        try:
            await self.analytics_store.mark_session_deleted(session_id)
            self._turn_seq_by_session.pop(session_id, None)
            self._usage_baseline_by_session.pop(session_id, None)
        except Exception:
            logger.exception("Failed to mark analytics deleted for session %s", session_id)

    async def delete_session(self, session_id: str, archive_reason: str = "user_deleted") -> dict:
        """
        Delete a session and cleanup all resources (with cascading deletion for child minions).

        Args:
            session_id: ID of session to delete
            archive_reason: Reason for archival (default: "user_deleted", use "parent_initiated" for dispose_minion)

        Returns a dict with:
            - success: bool indicating if deletion succeeded
            - deleted_session_ids: list of all session IDs deleted (including cascaded children)
        """
        deleted_ids = []
        try:
            session_info = await self.session_manager.get_session_info(session_id)
            deleted_ids.extend(await self._cascade_delete_children(session_id, archive_reason, session_info))

            project = await self._detach_session_from_project(session_id)
            session_info = await self._detach_session_from_parent(session_id, session_info)

            await self._delete_legion_schedules_for_session(session_id)
            self._unregister_capability_registry(session_id, session_info, project)
            await self._teardown_sdk_before_archive(session_id, session_info)
            await self._archive_session_before_delete(
                session_id, session_info, project, archive_reason, len(deleted_ids)
            )
            self._cleanup_legion_directory(session_id, session_info, project)
            await self._cleanup_storage_manager(session_id)
            self._cleanup_in_memory_callback_state(session_id)

            success = await self._finalize_session_manager_delete(session_id)
            if success:
                deleted_ids.append(session_id)
            return {"success": success, "deleted_session_ids": deleted_ids}
        except Exception:
            logger.exception(f"Failed to delete integrated session {session_id}")
            return {"success": False, "deleted_session_ids": deleted_ids}

    async def find_project_for_session(self, session_id: str) -> ProjectInfo | None:
        """Find the project that contains a given session"""
        return await self.project_manager.find_project_for_session(session_id)

    async def get_all_descendants(self, session_id: str) -> list[dict]:
        """Return the complete descendant list (unpaginated). Use for
        membership/cycle checks where truncation produces incorrect results."""
        return await self.session_manager.get_all_descendants(session_id)

    async def get_descendants(self, session_id: str, limit: int = 50, offset: int = 0) -> dict:
        """Get descendant sessions of a session, paginated."""
        return await self.session_manager.get_descendants(session_id, limit, offset)

    async def count_descendants(self, session_id: str) -> int:
        """Count total number of descendants (children, grandchildren, etc.)."""
        return await self.session_manager.count_descendants(session_id)

    async def wait_for_session_ready(self, session_id: str, timeout: float = 60.0) -> bool:
        """Wait until session's SDK is ready to accept messages."""
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return False
        if sdk.is_running():
            return True
        return await sdk.wait_until_ready(timeout=timeout)

    async def send_message(
        self,
        session_id: str,
        message: str,
        metadata: dict | None = None,
        inject_timestamp: bool = True,
    ) -> bool:
        """Send a message through the integrated pipeline.

        inject_timestamp: gates issue #1779 automatic timestamp injection. True for the
        live user-send and queue/cron paths (the feature's intended scope: "user
        messages"). Legion inter-agent comms and system-error messages (comm_router.py)
        pass False — they aren't user messages and injecting into them would desync
        the persisted record (comm.content persisted pre-injection) from the delivered text.
        """
        try:
            sdk = self._active_sdks.get(session_id)
            if not sdk:
                logger.error(f"No SDK found for session {session_id}")
                return False

            # Issue #1130: Track activity for idle watchdog
            self.session_manager.record_activity(session_id)

            # Issue #1779: Automatic timestamp injection, config resolved at session start
            # (same restart-to-take-effect convention as every other config field).
            # `is True` (not truthy check): sdk is a test double in some callers and an
            # unspecced AsyncMock's auto-generated attributes are truthy by default.
            if inject_timestamp and sdk.inject_timestamps_enabled is True:
                session_info = await self.session_manager.get_session_info(session_id)
                message, new_injection_date = maybe_inject_timestamp(
                    message,
                    enabled=True,
                    frequency=sdk.timestamp_injection_frequency,
                    tz_name=sdk.timestamp_injection_timezone,
                    last_injection_date=session_info.last_timestamp_injection_date if session_info else None,
                    now_utc=datetime.now(UTC),
                )
                if new_injection_date:
                    await self.session_manager.update_session(
                        session_id, last_timestamp_injection_date=new_injection_date
                    )

            # Mark session as processing before sending message
            await self.session_manager.start_turn(session_id)

            # Send message through SDK (will be queued and processed)
            result = await sdk.send_message(message, metadata=metadata)

            if not result:
                # Issue #1029: Send failed — set is_processing False directly.
                # If another query is in flight, the next assistant message
                # from the SDK stream will set it back to True.
                await self.session_manager.end_turn(session_id)

            return result

        except Exception:
            logger.exception(f"Failed to send message to session {session_id}")
            try:
                await self.session_manager.end_turn(session_id)
            except Exception:
                pass  # Don't fail on state update error
            return False

    async def interrupt_session(self, session_id: str) -> bool:
        """Interrupt the current Claude Code SDK session"""
        try:
            coord_logger.info(f"Interrupt requested for session {session_id}")

            # Check if SDK exists and is active
            sdk = self._active_sdks.get(session_id)
            if not sdk:
                logger.warning(f"No active SDK found for session {session_id} - cannot interrupt")
                return False

            # Check session state
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                logger.warning(f"Session {session_id} not found - cannot interrupt")
                return False

            # Issue #2159 (UC4): PAUSED is interruptible too, via the shared predicate —
            # this used to be `state not in [ACTIVE] and not is_processing`, which
            # actually let a PAUSED-but-still-processing session through while leaving
            # it stuck PAUSED (nothing resumed it), and depended on a service-wide
            # deny-all the REST router did separately. is_interruptible() plus the
            # scoped deny below replaces both.
            was_paused = session_info.state == SessionState.PAUSED
            if not is_interruptible(session_info.state):
                logger.warning(f"Session {session_id} not in interruptible state (state: {session_info.state}, processing: {session_info.is_processing})")
                return False

            # Call SDK interrupt method
            result = await sdk.interrupt_session()

            # Issue #2159: deny/resolve a PAUSED session's own pending permission(s)
            # regardless of whether the SDK-level interrupt itself reported success —
            # if the SDK's own internal phase has already moved off "connected" (e.g.
            # a concurrent error/terminate mid-flight), sdk.interrupt_session() can
            # return False while the backend-level state is still PAUSED; gating this
            # on `result` would leave that permission Future dangling forever with no
            # other recovery path. Resolving it is a backend-state concern, independent
            # of whatever the transport-level interrupt call itself returns.
            if was_paused and self.permission_service is not None:
                await self.permission_service.deny_for_session(session_id)

            if result:
                coord_logger.info(f"Session {session_id} interrupted")

                # Issue #520: Mark active ToolCalls as interrupted and store ToolCallUpdate entries
                self.mark_session_tools_interrupted(session_id)

                # Send interrupt system message via callback system (following client_launched pattern)
                # Issue #2007: this call's dispatch through _create_message_callback() also
                # resets is_processing = False (relocated from the removed interrupt_success
                # message onto this subtype's dispatch branch).
                await self._send_interrupt_message(session_id)
            else:
                logger.warning(f"Failed to initiate interrupt for session {session_id}")
                # Reset processing state if interrupt initiation failed
                await self.session_manager.end_turn(session_id)

            return result

        except Exception:
            logger.exception(f"Failed to interrupt session {session_id}")
            return False

    async def set_permission_mode(self, session_id: str, mode: str) -> bool:
        """Set the permission mode for a session"""
        try:
            coord_logger.info(f"Setting permission mode to '{mode}' for session {session_id}")

            # Validate mode
            if mode not in PermissionMode._value2member_map_:
                logger.error(f"Invalid permission mode: {mode}")
                return False

            # Check session state
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                logger.warning(f"Session {session_id} not found - cannot set permission mode")
                return False

            # For non-running sessions, just persist to disk — the mode will be applied at startup
            if session_info.state in STOPPED_STATES:
                await self.session_manager.update_permission_mode(
                    session_id, mode, template_manager=self.template_manager
                )
                coord_logger.info(f"Permission mode persisted to '{mode}' for stopped session {session_id}")
                return True

            # Check if SDK exists for live update
            sdk = self._active_sdks.get(session_id)
            if not sdk:
                logger.warning(f"No active SDK found for session {session_id} - cannot set permission mode")
                return False

            # Only allow live permission mode change for active sessions
            if session_info.state != SessionState.ACTIVE:
                logger.warning(f"Session {session_id} not in active state (state: {session_info.state})")
                return False

            # Call SDK set_permission_mode method (may raise if SDK rejects the mode)
            sdk_result = await sdk.set_permission_mode(mode)

            if sdk_result:
                # Update session manager's tracking of current permission mode
                await self.session_manager.update_permission_mode(
                    session_id, mode, template_manager=self.template_manager
                )
                coord_logger.info(f"Permission mode set to '{mode}' for session {session_id}")
            else:
                logger.warning(f"Failed to set permission mode for session {session_id}")

            return sdk_result

        except ValueError:
            raise
        except Exception as e:
            # Re-raise as ValueError so web_server can surface the SDK error message to the user
            logger.exception(f"Failed to set permission mode for session {session_id}")
            raise ValueError(str(e)) from e

    async def set_model(self, session_id: str, model: str) -> bool:
        """Set the model for a session, live-switching if active (no restart required)."""
        try:
            coord_logger.info(f"Setting model to '{model}' for session {session_id}")

            model = model.strip()
            if not model:
                logger.error("Invalid model: empty string")
                return False

            # Check session state
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                logger.warning(f"Session {session_id} not found - cannot set model")
                return False

            # For non-running sessions, just persist to disk — the model will be applied at startup
            if session_info.state in STOPPED_STATES:
                await self.session_manager.update_model(
                    session_id, model, template_manager=self.template_manager
                )
                coord_logger.info(f"Model persisted to '{model}' for stopped session {session_id}")
                return True

            # Check if SDK exists for live update
            sdk = self._active_sdks.get(session_id)
            if not sdk:
                logger.warning(f"No active SDK found for session {session_id} - cannot set model")
                return False

            # Only allow live model change for active sessions
            if session_info.state != SessionState.ACTIVE:
                logger.warning(f"Session {session_id} not in active state (state: {session_info.state})")
                return False

            # Call SDK set_model method (may raise if SDK rejects the model)
            sdk_result = await sdk.set_model(model)

            if sdk_result:
                # Update session manager's tracking of current model
                await self.session_manager.update_model(
                    session_id, model, template_manager=self.template_manager
                )
                coord_logger.info(f"Model set to '{model}' for session {session_id}")
            else:
                logger.warning(f"Failed to set model for session {session_id}")

            return sdk_result

        except ValueError:
            raise
        except Exception as e:
            # Re-raise as ValueError so web_server can surface the SDK error message to the user
            logger.exception(f"Failed to set model for session {session_id}")
            raise ValueError(str(e)) from e

    async def get_mcp_status(self, session_id: str) -> dict:
        """Get MCP server status for a session."""
        try:
            sdk = self._active_sdks.get(session_id)
            if not sdk:
                logger.warning(f"No active SDK found for session {session_id} - cannot get MCP status")
                return {"servers": []}

            return await sdk.get_mcp_status()
        except Exception:
            logger.exception(f"Failed to get MCP status for session {session_id}")
            return {"servers": []}

    async def get_context_usage(self, session_id: str) -> dict:
        """Get context usage for a session via SDK."""
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return {}
        return await sdk.get_context_usage()

    async def toggle_mcp_server(self, session_id: str, name: str, enabled: bool) -> None:
        """Toggle an MCP server on or off for a session. Raises on failure."""
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            raise RuntimeError(f"No active SDK found for session {session_id}")

        await sdk.toggle_mcp_server(name, enabled)

    async def reconnect_mcp_server(self, session_id: str, name: str) -> None:
        """Reconnect a failed MCP server for a session. Raises on failure."""
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            raise RuntimeError(f"No active SDK found for session {session_id}")

        await sdk.reconnect_mcp_server(name)

    async def add_directory(self, session_id: str, directory: str) -> dict:
        """Register a new working directory on an active session, live (issue #1675).

        Unlike a stopped-session config edit, this takes effect immediately via the
        SDK's register_repo_root control request, and is persisted so it survives a
        future restart. Docker-isolated sessions are rejected — new host paths can't
        be mounted into an already-running container. Raises ValueError on any
        rejection (Docker guard, path validation, CLI-side error) so the REST layer
        can surface a clear 400.
        """
        session_info = await self.session_manager.get_session_info(session_id)
        if not session_info:
            raise ValueError(f"Session {session_id} not found")

        if session_info.config.get("docker_enabled"):
            raise ValueError(
                "Adding directories to a running Docker session requires a restart — "
                "the path cannot be mounted into the running container."
            )

        if session_info.state != SessionState.ACTIVE:
            raise ValueError(f"Session not in active state (state: {session_info.state})")

        sdk = self._active_sdks.get(session_id)
        if not sdk:
            raise ValueError(f"No active SDK found for session {session_id}")

        normalized = _validate_new_additional_directory(
            directory,
            session_info.working_directory,
            session_info.config.get("additional_directories"),
        )

        try:
            result = await sdk.register_repo_root(normalized)
        except Exception as e:
            logger.exception(f"Failed to register repo root for session {session_id}")
            raise ValueError(str(e)) from e

        resolved_directory = result.get("directory", normalized) if isinstance(result, dict) else normalized

        await self.session_manager.update_additional_directories(session_id, [resolved_directory])
        coord_logger.info(f"Registered directory '{resolved_directory}' for session {session_id}")

        return {"directory": resolved_directory}

    # Issue #2189 (coordinator-split PR 1): restart/reset and their shared
    # teardown helpers moved to SessionLauncher — one-line delegates.
    async def restart_session(self, session_id: str, permission_callback: Callable | None = None) -> bool:
        return await self._session_launcher.restart_session(session_id, permission_callback)

    async def reset_session(
        self,
        session_id: str,
        permission_callback: Callable | None = None,
        _from_queue_processor: bool = False,
    ) -> bool:
        return await self._session_launcher.reset_session(
            session_id, permission_callback, _from_queue_processor
        )

    async def _archive_session_for_reset(self, session_id: str) -> bool:
        return await self._session_launcher._archive_session_for_reset(session_id)

    async def _rotate_proxy_logs(self, session_id: str) -> None:
        return await self._session_launcher._rotate_proxy_logs(session_id)

    async def _clear_docker_claude_data(
        self, session_id: str, keep_subdirs: set[str] | None = None
    ) -> None:
        return await self._session_launcher._clear_docker_claude_data(session_id, keep_subdirs)

    async def _clear_session_tmp(self, session_id: str) -> None:
        return await self._session_launcher._clear_session_tmp(session_id)

    async def get_session_info(self, session_id: str) -> dict[str, Any] | None:
        return await self._session_query.get_session_info(session_id)

    async def list_sessions(self, limit: int = 500, offset: int = 0) -> dict[str, Any]:
        return await self._session_query.list_sessions(limit, offset)

    # ==================== TASK LEG REGISTRY (Issue #1746) ====================

    async def _get_task_leg_registry(
        self, session_id: str, *, live: bool = False
    ) -> TaskLegRegistry:
        # Kept live on the coordinator (not just internal to SessionQuery):
        # message_pipeline.py calls self._coordinator._get_task_leg_registry(session_id)
        # directly, and test_task_registry.py pokes the same name.
        return await self._session_query._get_task_leg_registry(session_id, live=live)

    async def get_background_agents(self, session_id: str) -> dict[str, Any]:
        return await self._session_query.get_background_agents(session_id)

    async def get_background_tasks(self, session_id: str) -> dict[str, Any]:
        return await self._session_query.get_background_tasks(session_id)

    async def get_session_tasks(self, session_id: str) -> dict[str, Any]:
        return await self._session_query.get_session_tasks(session_id)

    def _clear_task_list(self, session_id: str) -> None:
        self._session_query.clear_task_list(session_id)

    async def get_history_index(self, session_id: str) -> dict[str, Any]:
        return await self._session_query.get_history_index(session_id)

    async def get_archive_history_index(self, session_id: str, archive_id: str) -> dict[str, Any]:
        if not self.legion_system:
            raise ValueError("archives unavailable")
        messages_file = self.legion_system.archive_manager.archive_messages_path(
            session_id, archive_id
        )
        return await self._session_query.get_archive_history_index(
            session_id, archive_id, messages_file
        )

    async def search_history(self, session_id: str, *args: Any) -> dict[str, Any] | None:
        return await self._session_query.search_history(session_id, *args)

    async def search_archive_history(self, sid: str, archive_id: str, *args: Any) -> dict | None:
        if not self.legion_system:
            raise ValueError("archives unavailable")
        path = self.legion_system.archive_manager.archive_messages_path(sid, archive_id)
        return await self._session_query.search_archive_history(sid, archive_id, path, *args)

    # ==================== ARCHIVE METHODS ====================

    async def get_archives(self, session_id: str, limit: int = 50, offset: int = 0) -> dict:
        return await self._archive_coordinator.get_archives(session_id, limit, offset)

    async def get_archive_messages(
        self, session_id: str, archive_id: str, offset: int = 0, limit: int | None = 50,
        **window_params: Any,
    ) -> dict:
        return await self._archive_coordinator.get_archive_messages(
            session_id, archive_id, offset, limit, **window_params
        )

    async def get_archive_state(self, session_id: str, archive_id: str) -> dict | None:
        return await self._archive_coordinator.get_archive_state(session_id, archive_id)

    async def get_archive_resources(
        self,
        session_id: str,
        archive_id: str,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        format_filter: str | None = None,
        sort: str = "newest",
    ) -> dict:
        return await self._archive_coordinator.get_archive_resources(
            session_id, archive_id, limit, offset, search, format_filter, sort
        )

    async def get_archive_resource_by_id(
        self, session_id: str, archive_id: str, resource_id: str
    ) -> dict | None:
        return await self._archive_coordinator.get_archive_resource_by_id(
            session_id, archive_id, resource_id
        )

    async def get_archive_resource_file(
        self, session_id: str, archive_id: str, resource_id: str
    ) -> bytes | None:
        return await self._archive_coordinator.get_archive_resource_file(
            session_id, archive_id, resource_id
        )

    async def archive_exists(self, session_id: str, archive_id: str) -> bool:
        return await self._archive_coordinator.archive_exists(session_id, archive_id)

    async def get_archive_queue(self, session_id: str, archive_id: str) -> list[dict]:
        return await self._archive_coordinator.get_archive_queue(session_id, archive_id)

    async def get_archive_proxy_logs(self, session_id: str, archive_id: str) -> list[dict]:
        return await self._archive_coordinator.get_archive_proxy_logs(session_id, archive_id)

    async def get_archive_proxy_log_content(
        self, session_id: str, archive_id: str, log_name: str,
        offset: int = 0, limit: int = 100, from_end: bool = False,
    ) -> dict:
        return await self._archive_coordinator.get_archive_proxy_log_content(
            session_id, archive_id, log_name, offset, limit, from_end
        )

    async def get_archive_history(self, session_id: str, archive_id: str) -> list[dict]:
        return await self._archive_coordinator.get_archive_history(session_id, archive_id)

    async def get_archive_history_content(
        self, session_id: str, archive_id: str, filename: str
    ) -> str | None:
        return await self._archive_coordinator.get_archive_history_content(
            session_id, archive_id, filename
        )

    async def get_archive_schedules(self, session_id: str, archive_id: str) -> dict:
        return await self._archive_coordinator.get_archive_schedules(session_id, archive_id)

    async def get_archive_attachments(self, session_id: str, archive_id: str) -> list[dict]:
        return await self._archive_coordinator.get_archive_attachments(session_id, archive_id)

    async def get_archive_attachment_file(
        self, session_id: str, archive_id: str, filename: str
    ) -> tuple[bytes, str] | None:
        return await self._archive_coordinator.get_archive_attachment_file(
            session_id, archive_id, filename
        )

    async def list_project_deleted_agents(self, project_id: str, limit: int = 50, offset: int = 0) -> dict:
        return await self._archive_coordinator.list_project_deleted_agents(project_id, limit, offset)

    async def erase_history(self, session_id: str) -> bool:
        return await self._archive_coordinator.erase_history(session_id)

    async def erase_archives(self, session_id: str) -> bool:
        return await self._archive_coordinator.erase_archives(session_id)

    async def check_history_archives(self, session_id: str) -> dict:
        return await self._archive_coordinator.check_history_archives(session_id)

    async def get_session_messages(
        self,
        session_id: str,
        limit: int | None = None,
        offset: int = 0,
        **window_params: Any,
    ) -> dict[str, Any]:
        """Get messages from a session with pagination metadata.

        Issue #2030: ``window``/``before`` (+budget) kwargs use the byte-cursor window reader.

        Issue #2109 (AC3): returns stored records unchanged — every tool
        lifecycle transition is stored and emitted live (issue #2084 stage 3,
        plus #2109's AC1/AC2 closing the restart/pending-emission gaps), so
        no read-time synthesis is needed or performed.
        """
        if any(map(window_params.get, WINDOW_MODES)):
            return await self._session_query.get_window_messages(session_id, **window_params)
        try:
            storage = self._storage_managers.get(session_id)
            if not storage:
                logger.error(f"No storage manager found for session {session_id}")
                return {
                    "messages": [], "total_count": 0,
                    "limit": limit or 50, "offset": offset, "has_more": False
                }

            raw_messages = await storage.read_messages(limit=limit, offset=offset)
            total_count = await storage.get_message_count()
            parsed_messages = [m for m in raw_messages if m]

            actual_limit = limit or 50
            has_more = (offset + len(raw_messages)) < total_count

            return {
                "messages": parsed_messages,
                "total_count": total_count,
                "limit": actual_limit,
                "offset": offset,
                "has_more": has_more,
            }
        except LegacyMessageFormatError:
            # Issue #2125 (AC6): fail loudly rather than silently returning an
            # empty history — propagates to the route's @handle_exceptions, same
            # as every other LegacyMessageFormatError call site in this codebase.
            raise
        except Exception:
            logger.exception(f"Failed to get messages for session {session_id}")
            return {
                "messages": [], "total_count": 0,
                "limit": limit or 50, "offset": offset, "has_more": False
            }

    def add_message_callback(self, session_id: str, callback: Callable):
        """Add callback for session messages"""
        if session_id not in self._message_callbacks:
            self._message_callbacks[session_id] = []
        self._message_callbacks[session_id].append(callback)
        # logger.info(f"Added message callback for session {session_id}, total callbacks: {len(self._message_callbacks[session_id])}")

    def clear_message_callbacks(self, session_id: str) -> None:
        """Clear all message callbacks for a session (prevents duplicate callbacks on restart)."""
        if session_id in self._message_callbacks:
            self._message_callbacks[session_id] = []

    def add_error_callback(self, session_id: str, callback: Callable):
        """Add callback for session errors"""
        if session_id not in self._error_callbacks:
            self._error_callbacks[session_id] = []
        self._error_callbacks[session_id].append(callback)

    def add_state_change_callback(self, callback: Callable):
        """Add callback for session state changes"""
        self._state_change_callbacks.append(callback)

    def add_metadata_change_callback(self, callback: Callable):
        """Add callback for metadata-only session updates (issue #2159)."""
        self._metadata_change_callbacks.append(callback)

    def add_processing_change_callback(self, callback: Callable):
        """Add callback for is_processing-only session updates (issue #2184)."""
        self._processing_change_callbacks.append(callback)

    def add_session_reset_callback(self, callback: Callable):
        """Add callback for session reset events (Issue #500)."""
        self._session_reset_callbacks.append(callback)

    def add_session_deleted_callback(self, callback: Callable):
        """Add callback for session deleted events (Issue #1986)."""
        self._session_deleted_callbacks.append(callback)

    def add_tool_call_broadcast_callback(self, callback: Callable):
        """Add callback for broadcasting tool_call messages via WebSocket (Issue #520)."""
        self._tool_call_broadcast_callbacks.append(callback)

    def set_rate_limit_broadcast_callback(self, callback: Callable) -> None:
        """Issue #899: Set callback for broadcasting rate_limits_update to the UI poll queue."""
        self._rate_limit_broadcast_callback = callback

    # Issue #2189 (coordinator-split PR 2): moved to MessagePipeline.
    async def _on_rate_limits(self, rate_limit_info: Any) -> None:
        return await self._message_pipeline.on_rate_limits(rate_limit_info)

    async def _notify_session_reset(self, session_id: str) -> None:
        """Notify registered callbacks that a session was reset."""
        for cb in self._session_reset_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(session_id)
                else:
                    cb(session_id)
            except Exception:
                logger.exception("Error in session reset callback")

    async def _notify_session_deleted(self, session_id: str) -> None:
        """Notify registered callbacks that a session was deleted (Issue #1986)."""
        for cb in self._session_deleted_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(session_id)
                else:
                    cb(session_id)
            except Exception:
                logger.exception("Error in session deleted callback")

    # Issue #2189 (coordinator-split PR 3): moved to ToolCallTracker. One-line
    # delegates, preserving every externally-facing name exactly — called by
    # web_server.py/permission_service.py/task_utils.py and others.
    def _schedule_tool_call_update_storage(
        self,
        session_id: str,
        tool_call: ToolCall,
        triggering_message: dict[str, Any] | None = None,
    ) -> None:
        return self._tool_call_tracker._schedule_tool_call_update_storage(
            session_id, tool_call, triggering_message
        )

    async def _write_tool_call_update(
        self,
        storage,
        stored_dict: dict[str, Any],
        tool_use_id: str,
    ) -> None:
        return await self._tool_call_tracker._write_tool_call_update(
            storage, stored_dict, tool_use_id
        )

    def _create_auto_approval_callback(self, session_id: str) -> Callable:
        return self._tool_call_tracker._create_auto_approval_callback(session_id)

    def create_tool_call(
        self,
        session_id: str,
        tool_use_id: str,
        name: str,
        input_params: dict[str, Any],
        requires_permission: bool = False,
        parent_tool_use_id: str | None = None,
        turn_id: str | None = None,
    ) -> ToolCall:
        return self._tool_call_tracker.create_tool_call(
            session_id,
            tool_use_id,
            name,
            input_params,
            requires_permission=requires_permission,
            parent_tool_use_id=parent_tool_use_id,
            turn_id=turn_id,
        )

    def update_tool_call_permission_request(
        self,
        session_id: str,
        tool_use_id: str,
        permission_info: PermissionInfo,
        request_id: str | None = None,
        triggering_message: dict[str, Any] | None = None,
    ) -> ToolCall | None:
        return self._tool_call_tracker.update_tool_call_permission_request(
            session_id, tool_use_id, permission_info, request_id, triggering_message
        )

    def update_tool_call_permission_response(
        self,
        session_id: str,
        tool_use_id: str,
        granted: bool,
        triggering_message: dict[str, Any] | None = None,
        applied_updates: list[dict[str, Any]] | None = None,
    ) -> ToolCall | None:
        return self._tool_call_tracker.update_tool_call_permission_response(
            session_id, tool_use_id, granted, triggering_message, applied_updates
        )

    def update_tool_call_running(
        self,
        session_id: str,
        tool_use_id: str,
    ) -> ToolCall | None:
        return self._tool_call_tracker.update_tool_call_running(session_id, tool_use_id)

    def update_tool_call_result(
        self,
        session_id: str,
        tool_use_id: str,
        result: Any,
        is_error: bool = False,
        triggering_message: dict[str, Any] | None = None,
        sender_attachments: list[dict] | None = None,
    ) -> ToolCall | None:
        return self._tool_call_tracker.update_tool_call_result(
            session_id,
            tool_use_id,
            result,
            is_error=is_error,
            triggering_message=triggering_message,
            sender_attachments=sender_attachments,
        )

    def update_tool_call_input(
        self,
        session_id: str,
        tool_use_id: str,
        input_params: dict[str, Any],
    ) -> ToolCall | None:
        return self._tool_call_tracker.update_tool_call_input(session_id, tool_use_id, input_params)

    def _parse_send_comm_sender_attachments(
        self,
        result_content: str,
    ) -> list[dict] | None:
        """Issue #1593/#1730: Parse sender resource IDs for mcp__legion__send_comm attachments.

        Delegates to the module-level _extract_send_comm_sender_attachments(). Kept as an
        instance method (unused self) for existing call sites/tests.
        """
        return _extract_send_comm_sender_attachments(result_content)

    def mark_session_tools_interrupted(self, session_id: str) -> list[ToolCall]:
        return self._tool_call_tracker.mark_session_tools_interrupted(session_id)

    def get_tool_call_event(self, session_id: str) -> asyncio.Event:
        return self._tool_call_tracker.get_tool_call_event(session_id)

    # Issue #2189 (coordinator-split PR 2): moved to MessagePipeline. One-line
    # delegates, preserving these 3 externally-facing names exactly — called by
    # web_server.py/permission_service.py.
    def mark_assistant_message_emitted(self, session_id: str, turn_id: str) -> None:
        return self._message_pipeline.mark_assistant_message_emitted(session_id, turn_id)

    def is_assistant_message_emitted(self, session_id: str, turn_id: str) -> bool:
        return self._message_pipeline.is_assistant_message_emitted(session_id, turn_id)

    def get_message_emitted_event(self, session_id: str) -> asyncio.Event:
        return self._message_pipeline.get_message_emitted_event(session_id)

    def get_tool_call_by_id(self, session_id: str, tool_use_id: str) -> ToolCall | None:
        return self._tool_call_tracker.get_tool_call_by_id(session_id, tool_use_id)

    def _get_active_tool_call(self, session_id: str, tool_use_id: str) -> ToolCall | None:
        return self._tool_call_tracker._get_active_tool_call(session_id, tool_use_id)

    def apply_tool_call_snapshot(self, session_id: str, tool_call: ToolCall) -> ToolCall:
        return self._tool_call_tracker.apply_tool_call_snapshot(session_id, tool_call)

    def _remove_active_tool_call(self, session_id: str, tool_use_id: str) -> None:
        return self._tool_call_tracker._remove_active_tool_call(session_id, tool_use_id)

    def get_tool_call_by_request_id(self, session_id: str, request_id: str) -> ToolCall | None:
        return self._tool_call_tracker.get_tool_call_by_request_id(session_id, request_id)

    # Issue #2189 (coordinator-split PR 2): moved to MessagePipeline.
    async def _store_processed_message(self, session_id: str, message_data: dict[str, Any]):
        return await self._message_pipeline._store_processed_message(session_id, message_data)

    # Issue #2189 (coordinator-split PR 1): moved to SessionLauncher — despite
    # physical location in this message-pipeline section, both are stateless,
    # single-caller (start_session-only) helpers. One-line delegates.
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
        return self._session_launcher._build_permission_handler(
            session_dir,
            knowledge_mgmt_enabled,
            auto_memory_mode,
            skill_creating_enabled=skill_creating_enabled,
            working_directory=working_directory,
            is_legion=is_legion,
            allow_background_agent=allow_background_agent,
            block_cross_session_messaging=block_cross_session_messaging,
        )

    def _resolve_default_proxy_image(self) -> str:
        return self._session_launcher._resolve_default_proxy_image()

    # Issue #2189 (coordinator-split PR 2): moved to MessagePipeline. One-line
    # delegates, preserving every external-facing name exactly — 20+ tests poke
    # _create_message_callback directly, 13+ poke _create_error_callback, etc.
    def _create_message_callback(self, session_id: str) -> Callable:
        return self._message_pipeline.create_message_callback(session_id)

    def _create_phase_callback(self, session_id: str) -> Callable:
        return self._message_pipeline.create_phase_callback(session_id)

    async def _transition_to_error(
        self, session_id: str, error_message: str, **structured_error_kwargs: Any
    ) -> None:
        return await self._message_pipeline.transition_to_error(
            session_id, error_message, **structured_error_kwargs
        )

    def _resolve_auto_resume_fire_at(
        self,
        rejected_entry: dict | None,
        synthetic_entry: tuple[str, float] | None,
        fallback_ts: float,
    ) -> tuple[float, str]:
        return self._message_pipeline._resolve_auto_resume_fire_at(
            rejected_entry, synthetic_entry, fallback_ts
        )

    async def _maybe_schedule_auto_resume(
        self, session_id: str, structured_error_kwargs: dict
    ) -> None:
        return await self._message_pipeline._maybe_schedule_auto_resume(
            session_id, structured_error_kwargs
        )

    async def _handle_rate_limited_result(
        self,
        session_id: str,
        record_ts: float,
        synthetic_entry: tuple[str, float] | None,
    ) -> None:
        return await self._message_pipeline._handle_rate_limited_result(
            session_id, record_ts, synthetic_entry
        )

    def _create_error_callback(self, session_id: str) -> Callable:
        return self._message_pipeline.create_error_callback(session_id)

    def _create_stderr_callback(self, session_id: str) -> Callable:
        return self._message_pipeline.create_stderr_callback(session_id)

    async def _send_mcp_degraded_message(
        self, session_id: str, unavailable: list[tuple[str, str]]
    ) -> None:
        return await self._message_pipeline.send_mcp_degraded_message(session_id, unavailable)

    async def _send_client_launched_message(self, session_id: str):
        return await self._message_pipeline.send_client_launched_message(session_id)

    async def _send_session_failure_message(
        self,
        session_id: str,
        error_message: str,
        raw_error: str | None = None,
        *,
        error_subtype: str | None = None,
        error_terminal_reason: str | None = None,
        error_api_error_status: int | None = None,
        error_list: list[str] | None = None,
    ):
        return await self._message_pipeline.send_session_failure_message(
            session_id,
            error_message,
            raw_error,
            error_subtype=error_subtype,
            error_terminal_reason=error_terminal_reason,
            error_api_error_status=error_api_error_status,
            error_list=error_list,
        )

    async def _send_interrupt_message(self, session_id: str):
        return await self._message_pipeline._send_interrupt_message(session_id)

    async def resolve_orphaned_ephemeral_session(self, session_id: str) -> None:
        return await self._message_pipeline.resolve_orphaned_ephemeral_session(session_id)

    def _format_failure_content(self, friendly_error: str, raw_error: str | None) -> str:
        return self._message_pipeline._format_failure_content(friendly_error, raw_error)

    def _result_error_structured_kwargs(self, e: Any) -> dict:
        return self._message_pipeline.result_error_structured_kwargs(e)

    def _format_result_error_friendly_message(self, e: Any, fallback: str | None = None) -> str:
        return self._message_pipeline._format_result_error_friendly_message(e, fallback)

    def _extract_claude_cli_error(self, error_message: str, result_error: ResultError | None = None) -> str:
        return self._message_pipeline.extract_claude_cli_error(error_message, result_error)

    async def _notify_state_change(self, session_id: str, new_state: SessionState):
        """Notify registered callbacks about state changes"""
        try:
            state_data = {
                "session_id": session_id,
                "new_state": new_state.value,
                "timestamp": get_unix_timestamp()
            }

            for callback in self._state_change_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(state_data)
                    else:
                        callback(state_data)
                except Exception:
                    logger.exception("Error in state change callback")

        except Exception:
            logger.exception(f"Error notifying state change for {session_id}")

    async def _on_session_manager_state_change(self, session_id: str, new_state: SessionState, is_processing: bool = False, project_id: str | None = None):
        """Handle state changes from session manager"""
        # logger.info(f"Received state change from session manager: {session_id} -> {new_state.value}")
        await self._notify_state_change(session_id, new_state)

    async def _notify_metadata_change(self, session_id: str) -> None:
        """Notify registered callbacks about a metadata-only update (issue #2159)."""
        try:
            for callback in self._metadata_change_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(session_id)
                    else:
                        callback(session_id)
                except Exception:
                    logger.exception("Error in metadata change callback")
        except Exception:
            logger.exception(f"Error notifying metadata change for {session_id}")

    async def _on_session_manager_metadata_change(self, session_id: str) -> None:
        """Handle metadata-only updates from session manager (issue #2159)."""
        await self._notify_metadata_change(session_id)

    async def _notify_processing_change(self, session_id: str, is_processing: bool) -> None:
        """Notify registered callbacks about an is_processing-only update (issue #2184)."""
        try:
            for callback in self._processing_change_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(session_id, is_processing)
                    else:
                        callback(session_id, is_processing)
                except Exception:
                    logger.exception("Error in processing change callback")
        except Exception:
            logger.exception(f"Error notifying processing change for {session_id}")

    async def _on_session_manager_processing_change(self, session_id: str, is_processing: bool) -> None:
        """Handle is_processing-only updates from session manager (issue #2184)."""
        await self._notify_processing_change(session_id, is_processing)

    async def _on_session_manager_lifecycle_record(self, session_id: str, action: str) -> None:
        """Handle a logical lifecycle action from session manager (issue #2184) —
        start/restart/reset/pause/resume/terminate — by recording it into this
        session's SessionRecorder, if one is active.

        Replaces the recorder lookup + record_lifecycle(action) + exception handling
        previously duplicated across permission_service.py's pause/resume call sites
        and this file's own start_session()/terminate_session() call sites.
        """
        recorder = self._session_recorders.get(session_id)
        if recorder is None:
            return
        try:
            recorder.record_lifecycle(action)
        except Exception:
            logger.exception(
                f"Recorder failed to capture '{action}' lifecycle event for session {session_id}"
            )

    async def _check_sdk_generated_name(self, session_id: str) -> None:
        """Check if SDK has generated a session title and store it (issue #904)."""
        # Deferred import: NOT a circular-import break (confirmed during issue #2189
        # PR 11's AC6 cleanup) — it's an optional/version-guarded SDK API. Older
        # claude_agent_sdk versions may lack get_session_info entirely, so this stays
        # import-time-optional rather than a hard top-level dependency.
        try:
            from claude_agent_sdk import get_session_info as sdk_get_session_info
        except ImportError:
            return
        try:
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                return
            sdk_session_id = session_info.claude_code_session_id
            working_dir = str(session_info.working_directory) if session_info.working_directory else None
            if not sdk_session_id:
                return

            sdk_info = await asyncio.to_thread(sdk_get_session_info, sdk_session_id, working_dir)
            if sdk_info and sdk_info.custom_title:
                await self.session_manager.update_sdk_generated_name(session_id, sdk_info.custom_title)
        except Exception:
            coord_logger.debug(f"SDK title check failed for {session_id}", exc_info=True)

    async def _validate_and_cleanup_projects(self):
        """
        Validate project/session references and clean up orphaned data.

        This runs on startup to fix any data corruption from previous bugs (like issue #63).
        Removes orphaned session references and deletes empty projects.
        Logs all cleanup actions for debugging.
        """
        try:
            projects = await self.project_manager.list_projects()
            coord_logger.info(f"Validating {len(projects)} projects for orphaned session references")

            cleanup_count = 0

            for project in projects:
                project_id = project.project_id
                session_ids_before = list(project.session_ids)  # Make a copy
                valid_session_ids = []

                # Check each session ID to see if it actually exists
                for session_id in session_ids_before:
                    session_info = await self.session_manager.get_session_info(session_id)
                    if session_info:
                        valid_session_ids.append(session_id)
                    else:
                        coord_logger.warning(f"Found orphaned session reference '{session_id}' in project {project_id} - will be removed")
                        cleanup_count += 1

                # If we found orphaned references, update or delete the project
                if len(valid_session_ids) != len(session_ids_before):
                    orphaned_count = len(session_ids_before) - len(valid_session_ids)

                    # Clean the session_ids list but keep the project (even if empty)
                    coord_logger.info(f"Removing {orphaned_count} orphaned session(s) from project {project_id} ({project.name})")
                    project.session_ids = valid_session_ids
                    project.updated_at = datetime.now(UTC)
                    await self.project_manager._persist_project_state(project_id)

                    if len(valid_session_ids) == 0:
                        coord_logger.info(f"Project {project_id} ({project.name}) now has no sessions but will remain for new minions")

            # Also validate parent-child minion relationships (overseer child_minion_ids)
            sessions = await self.session_manager.list_sessions()
            child_cleanup_count = 0

            for session in sessions:
                # Issue #349: All sessions are minions - just check is_overseer and child_minion_ids
                if session.is_overseer and session.child_minion_ids:
                    # This is a parent overseer - validate its children
                    valid_children = []
                    children_before = list(session.child_minion_ids)

                    for child_id in children_before:
                        child_info = await self.session_manager.get_session_info(child_id)
                        if child_info:
                            valid_children.append(child_id)
                        else:
                            coord_logger.warning(f"Found orphaned child minion reference '{child_id}' in overseer {session.session_id} - will be removed")
                            child_cleanup_count += 1

                    # Update if we found orphaned children
                    if len(valid_children) != len(children_before):
                        session.child_minion_ids = valid_children
                        session.updated_at = datetime.now(UTC)
                        await self.session_manager._persist_session_state(session.session_id)
                        coord_logger.info(f"Removed {len(children_before) - len(valid_children)} orphaned child(ren) from overseer {session.session_id}")

            if cleanup_count > 0 or child_cleanup_count > 0:
                coord_logger.info(f"Startup cleanup complete: removed {cleanup_count} orphaned project session(s), {child_cleanup_count} orphaned child minion(s)")
            else:
                coord_logger.info("Startup validation complete: no orphaned data found")

        except Exception:
            logger.exception("Error during startup project validation")
            # Don't raise - this shouldn't block startup

    # ==================== FILE UPLOAD TRACKING (Issue #403) ====================

    async def register_uploaded_file(self, session_id: str, file_path: str) -> None:
        return await self._session_resources.register_uploaded_file(session_id, file_path)

    async def unregister_uploaded_file(self, session_id: str, file_path: str) -> None:
        return await self._session_resources.unregister_uploaded_file(session_id, file_path)

    def is_uploaded_file(self, session_id: str, file_path: str) -> bool:
        return self._session_resources.is_uploaded_file(session_id, file_path)

    def get_uploaded_files(self, session_id: str) -> set[str]:
        return self._session_resources.get_uploaded_files(session_id)

    def clear_uploaded_files(self, session_id: str) -> None:
        return self._session_resources.clear_uploaded_files(session_id)

    # ==================== COMPACTION-TIME DISTILLATION (Issue #1628) ====================

    async def _handle_compact_boundary(self, session_id: str, parsed_message) -> None:
        return await self._archive_coordinator._handle_compact_boundary(session_id, parsed_message)

    async def _distill_compaction(self, session_id: str, boundary_ts: float) -> None:
        return await self._archive_coordinator._distill_compaction(session_id, boundary_ts)

    # ==================== RESOURCE GALLERY INTEGRATION (Issue #404) ====================

    async def register_uploaded_resource(
        self,
        session_id: str,
        file_path: str,
        title: str | None = None,
        description: str | None = None
    ) -> dict | None:
        return await self._session_resources.register_uploaded_resource(
            session_id, file_path, title, description
        )

    # Backward compatibility alias
    async def register_uploaded_image(
        self,
        session_id: str,
        file_path: str,
        title: str | None = None,
        description: str | None = None
    ) -> None:
        return await self._session_resources.register_uploaded_image(
            session_id, file_path, title, description
        )

    async def cleanup(self):
        """Cleanup all resources"""
        try:
            # Stop scheduler service and history rotator (issue #495, #1372)
            if hasattr(self, 'legion_system') and self.legion_system is not None:
                await self.legion_system.history_rotator.stop()
                await self.legion_system.scheduler_service.stop()

            # Terminate all active sessions
            session_ids = list(self._active_sdks.keys())
            for session_id in session_ids:
                await self.terminate_session(session_id)

            # Issue #1484: close shared MCP upstream connections
            try:
                await self.shared_mcp_manager.shutdown()
            except Exception:
                logger.exception("Error shutting down shared MCP manager")

            coord_logger.info("Session coordinator cleanup completed")

        except Exception:
            logger.exception("Error during session coordinator cleanup")

    async def _init_session_overrides(self, session_id: str, config: "SessionConfig") -> None:
        """Compute and store initial session.config overrides for a template-linked session.

        Called at create time so that fields the caller explicitly customised (present in
        config.model_fields_set) survive restarts and later template edits, instead of being
        silently dropped or, conversely, having merely-inherited values incorrectly frozen
        (#1875). Always runs (even producing an empty override set) so it also clears out any
        false-positive CONFIG_FIELDS entries create_session()'s cruder class-default-only
        heuristic may have just written into session.config moments earlier.
        """
        if not config.template_id:
            return
        try:

            template = await self.template_manager.get_template(config.template_id)
            template_values = (
                await resolve_template_config(template, self.profile_manager)
                if template is not None
                else {}  # invalid/deleted template_id — nothing to inherit from, fall
                # through so the loop below still clears create_session()'s false positives
            )

            overrides: dict = {}
            for field_name in CONFIG_FIELDS:
                if field_name not in config.model_fields_set:
                    continue  # caller never touched this field — not a customization
                config_value = getattr(config, field_name)
                if config_value is None:
                    continue  # explicit None = "inherit"; never freeze as an override
                template_value = template_values.get(field_name, DEFAULTS.get(field_name))
                if config_value != template_value:
                    overrides[field_name] = config_value

            await self.session_manager.update_session(session_id, _replace_config=overrides)
            if overrides:
                coord_logger.debug(
                    f"Session {session_id} initial overrides computed: {list(overrides.keys())}"
                )
        except Exception:
            logger.exception(f"Failed to compute initial session_overrides for {session_id}")

    async def _prune_inherited_mcp_snapshot(self, session_info) -> bool:
        """Remove MCP config fields from session.config that merely mirror the current
        template/profile resolution (inherited snapshots, not real overrides).

        Returns True if the session was modified. Issue #1660.
        """
        if not session_info.template_id:
            return False
        try:
            template = await self.template_manager.get_template(session_info.template_id)
            if template is None:
                return False
            resolved = await resolve_template_config(template, self.profile_manager)
            cfg = dict(session_info.config or {})
            changed = False
            for field in _MCP_INHERITABLE_FIELDS:
                if field not in cfg:
                    continue
                if _mcp_field_equal(field, cfg[field], resolved.get(field)):
                    del cfg[field]
                    changed = True
            if changed:
                await self.session_manager.update_session(
                    session_info.session_id, _replace_config=cfg
                )
            return changed
        except Exception:
            logger.exception(
                f"Failed to prune MCP snapshot for session {session_info.session_id}"
            )
            return False

    async def _migrate_stale_mcp_snapshots(self) -> None:
        """Issue #1660: one-time cleanup of inherited MCP snapshots persisted into
        session.config by the spawn path. Conservative — only clears fields that still
        equal the current template/profile resolution. Never blocks startup."""
        try:
            sessions = await self.session_manager.list_sessions()
            cleared = 0
            for s in sessions:
                if await self._prune_inherited_mcp_snapshot(s):
                    cleared += 1
            if cleared:
                coord_logger.info(
                    f"#1660 migration: cleared stale MCP snapshots on {cleared} session(s)"
                )
        except Exception:
            logger.exception("Failed MCP snapshot migration (#1660)")
