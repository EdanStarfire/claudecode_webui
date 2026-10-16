"""
Session Coordinator for Claude Code WebUI

Integrates session management, data storage, and SDK interaction
into a unified system for managing Claude Code sessions.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from .archive_coordinator import ArchiveCoordinator
from .claude_sdk import ClaudeSDK, ResultError
from .data_storage import DataStorageManager
from .hooks.pretooluse_handler import InternalPermissionHandler
from .litellm_proxy_manager import make_model_alias
from .message_parser import MessageParser, MessageProcessor
from .message_pipeline import MessagePipeline, MessagePipelineCallbacks
from .models.messages import (
    LegacyMessageFormatError,
    PermissionInfo,
    ToolCall,
)
from .models.permission_mode import PermissionMode
from .oauth_refresh_manager import OAuthRefreshManager
from .project_manager import ProjectInfo, ProjectManager
from .queue_manager import QueueManager
from .queue_processor import QueueProcessor
from .session_config import SessionConfig
from .session_launcher import (
    STARTUP_ATTACH_TIMEOUT_SECONDS,  # noqa: F401 — re-exported; test_session_coordinator.py imports it from here
    LauncherCallbacks,
    SessionLauncher,
)
from .session_lifecycle import is_interruptible
from .session_manager import STOPPED_STATES, SessionManager, SessionState
from .session_query import SessionQuery
from .session_recorder import SessionRecorder
from .session_resources import SessionResources
from .task_registry import TaskLegRegistry
from .timestamp_injection import maybe_inject_timestamp
from .timestamp_utils import get_unix_timestamp
from .tool_call_tracker import ToolCallTracker

# Get specialized logger for coordinator actions
coord_logger = get_logger('coordinator', category='COORDINATOR')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


def _normalize_result_usage(
    usage: dict | None,
    model_usage: dict | None,
) -> tuple[dict, float | None]:
    """Return (snake_case usage dict, aggregate cost from model_usage or None).

    Prefers SDK 'usage' when it carries non-zero token counts. Otherwise
    aggregates per-model 'model_usage' entries (camelCase keys, keyed by
    model name) into the snake_case shape that AnalyticsStore.record_turn
    already understands.
    """
    snake_keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    if isinstance(usage, dict) and any(int(usage.get(k) or 0) for k in snake_keys):
        return dict(usage), None

    if not isinstance(model_usage, dict) or not model_usage:
        return (dict(usage) if isinstance(usage, dict) else {}), None

    agg = {k: 0 for k in snake_keys}
    cost = 0.0
    has_cost = False
    for entry in model_usage.values():
        if not isinstance(entry, dict):
            continue
        agg["input_tokens"] += int(entry.get("inputTokens") or 0)
        agg["output_tokens"] += int(entry.get("outputTokens") or 0)
        agg["cache_creation_input_tokens"] += int(entry.get("cacheCreationInputTokens") or 0)
        agg["cache_read_input_tokens"] += int(entry.get("cacheReadInputTokens") or 0)
        c = entry.get("costUSD")
        if c is not None:
            cost += float(c)
            has_cost = True
    return agg, (cost if has_cost else None)


def _delta_from_baseline(
    usage: dict,
    sdk_cost: float | None,
    baseline: dict[str, float] | None,
) -> tuple[dict, float | None, dict[str, float]]:
    """Compute per-turn deltas from cumulative-since-subprocess snapshots.

    `baseline` is None at the first turn of a new epoch (post-restart or
    brand new session) — treated as an all-zero prior snapshot, so the
    delta equals the turn's full raw value, exactly as intended.

    A field's absence is NOT the same as a real zero: `usage` is not
    guaranteed to carry all 4 token keys on every ResultMessage — e.g.
    backend/tests/fixtures/multi_turn/messages.jsonl has ResultMessages
    with `total_cost_usd` present and `usage` entirely absent, and
    `_normalize_result_usage()`'s passthrough branch
    (`return dict(usage), None`) copies whatever partial shape the SDK
    sent rather than guaranteeing all 4 keys. Treating a missing key as 0
    and writing that 0 into the baseline would corrupt the *next* turn's
    delta for that field (the same failure mode `sdk_cost is None`
    already guards against for cost) — so missing/None fields contribute
    0 delta for *this* turn but leave that field's baseline untouched,
    symmetric with the cost handling below.
    """
    fields = (
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
    )
    baseline = dict(baseline or {})
    delta: dict[str, float] = {}
    for k in fields:
        if k in usage and usage[k] is not None:
            current = float(usage[k])
            delta[k] = max(0.0, current - baseline.get(k, 0.0))
            baseline[k] = current
        else:
            delta[k] = 0.0
            # field not reported this turn — leave baseline[k] untouched

    delta_cost = None
    if sdk_cost is not None:
        delta_cost = max(0.0, float(sdk_cost) - baseline.get("total_cost_usd", 0.0))
        baseline["total_cost_usd"] = float(sdk_cost)
    # else: no cost reported this turn — leave total_cost_usd baseline
    # untouched so the next turn's delta is still computed correctly.

    return delta, delta_cost, baseline


def _accumulate_subagent_usage(
    accum: dict[str, float] | None,
    usage: dict,
) -> dict[str, float]:
    """Sum one subagent AssistantMessage's usage into a running per-session accumulator.

    Unlike the main thread's cumulative-since-subprocess-start snapshots (see
    `_delta_from_baseline`), each subagent `AssistantMessage.usage` is already a
    per-call delta for that individual API call — it must be summed directly,
    never baseline-diffed (diffing would silently zero out everything after the
    first call, since there's no shared cumulative counter to diff against).
    """
    accum = dict(accum or {})
    cache_creation = usage.get("cache_creation") or {}
    cache_5m = int(cache_creation.get("ephemeral_5m_input_tokens") or 0)
    cache_1h = int(cache_creation.get("ephemeral_1h_input_tokens") or 0)
    accum["input_tokens"] = accum.get("input_tokens", 0) + int(usage.get("input_tokens") or 0)
    accum["output_tokens"] = accum.get("output_tokens", 0) + int(usage.get("output_tokens") or 0)
    accum["cache_read_input_tokens"] = accum.get("cache_read_input_tokens", 0) + int(
        usage.get("cache_read_input_tokens") or 0
    )
    accum["cache_write_tokens_5m"] = accum.get("cache_write_tokens_5m", 0) + cache_5m
    accum["cache_write_tokens_1h"] = accum.get("cache_write_tokens_1h", 0) + cache_1h
    # Flat total, kept in sync with the two TTL tiers above so callers never need
    # to re-derive it — AnalyticsStore.record_turn()/compute_cost() key off of this
    # flat name (or "cache_creation_input_tokens"), not the TTL-tier split.
    accum["cache_write_tokens"] = accum.get("cache_write_tokens", 0) + cache_5m + cache_1h
    return accum


def _render_inject_file(placeholder: str, fmt: str, key_path: str | None) -> str:
    """Render placeholder into an inject_file's content.

    Issue #1134: builds a formatted file containing only the placeholder string
    so the agent never sees the real credential value.
    """
    if fmt == "raw" or not key_path:
        return placeholder

    keys = key_path.split(".")

    if fmt == "json":
        import json as _json
        obj: dict = {}
        cur = obj
        for key in keys[:-1]:
            cur[key] = {}
            cur = cur[key]
        cur[keys[-1]] = placeholder
        return _json.dumps(obj, indent=2)

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
    ):
        self.data_dir = data_dir or Path("data")
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
        # Issue #2184: accept an externally-constructed SessionManager (web_server.py
        # needs one before this coordinator exists, to build SessionWatchdogService
        # from it) rather than unconditionally constructing a second instance here —
        # construct-if-not-given keeps every other caller (tests, ApplicationService
        # call sites that construct a bare SessionCoordinator) working unchanged.
        self.session_manager = session_manager or SessionManager(self.data_dir)
        self.project_manager = ProjectManager(self.data_dir)
        self.message_parser = MessageParser()
        self.message_processor = MessageProcessor(self.message_parser)

        # Credential vault for proxy credential management (issue #1053)
        from backend.credential_vault import CredentialVault
        self.credential_vault = CredentialVault(self.data_dir)

        # Template manager for minion templates (issue #2184: construct-if-not-given,
        # same convention as session_manager above).
        from backend.template_manager import TemplateManager
        self.template_manager = template_manager or TemplateManager(self.data_dir)

        # Profile manager for composable session config profiles (issue #1062)
        from backend.profile_manager import ProfileManager
        self.profile_manager = ProfileManager(self.data_dir)

        # MCP config manager for global MCP server configurations (issue #676)
        from backend.mcp_config_manager import McpConfigManager
        self.mcp_config_manager = McpConfigManager(self.data_dir)

        # Hook config manager for global hook configurations (issue #1629)
        from backend.hook_config_manager import HookConfigManager
        self.hook_config_manager = HookConfigManager(self.data_dir)

        # Provider catalog store — data/providers.json (issue #1465)
        from backend.provider_catalog import ProviderCatalogStore
        self.provider_catalog_store = ProviderCatalogStore(self.data_dir)

        # OAuth 2.1 flow manager for MCP servers (issue #813)
        from backend.oauth_manager import OAuthFlowManager
        self.oauth_manager = OAuthFlowManager(self.data_dir)

        # Active SDK instances
        self._active_sdks: dict[str, ClaudeSDK] = {}
        self._storage_managers: dict[str, DataStorageManager] = {}
        # Issue #1998: raw-fidelity session recorders, keyed by session_id. Created lazily in
        # start_session() when the effective config opts in and the flag above is on; reused
        # across restart_session/reset_session (never recreated); dropped in terminate_session.
        self._session_recorders: dict[str, SessionRecorder] = {}

        # Issue #976/#989: OAuth token refresh lifecycle (extracted to OAuthRefreshManager)
        self.oauth_refresh_manager = OAuthRefreshManager(self.oauth_manager)

        # Issue #1484: shared-connection MCP manager for opt-in passthrough configs.
        # Receives credential_vault so ${secret:...} refs in shared configs are resolved
        # to plaintext inline (no Docker proxy sidecar in the shared-connection egress path).
        from backend.mcp.shared_connection_manager import SharedMcpConnectionManager
        self.shared_mcp_manager = SharedMcpConnectionManager(
            self.oauth_manager, self.oauth_refresh_manager, self.credential_vault,
        )

        async def _cfg_lookup(server_id: str):
            return await self.mcp_config_manager.get_config(server_id)

        self.shared_mcp_manager.set_cfg_lookup(_cfg_lookup)

        # Issue #1800: one-shot connection lifecycle for non-shared MCP configs'
        # "Test connection" affordance. Independent of shared_mcp_manager — see
        # src/mcp/oneshot_connection.py module docstring for why.
        from backend.mcp.oneshot_connection import McpOneshotConnector
        self.mcp_oneshot_connector = McpOneshotConnector(self.oauth_manager, self.credential_vault)

        # Issue #1789: custom OAuth callback path/port listeners for Shared MCP servers.
        # Broadcast callback (UI notification) is injected later via set_broadcast_callback,
        # same pattern as oauth_refresh_manager below.
        from backend.oauth_callback_listener_manager import OAuthCallbackListenerManager
        self.oauth_callback_listener_manager = OAuthCallbackListenerManager(
            self.oauth_manager, host=self.host,
        )

        # Issue #1387: Proactive vault OAuth2 secret refresh
        from .vault_refresh_manager import VaultRefreshManager
        self.vault_refresh_manager = VaultRefreshManager(self.credential_vault)

        # Secrets Vault CRUD + session-scoped resolution (issue #2189 AC5,
        # router-boundary PR B) — constructed last among its 6 collaborators
        # (vault_refresh_manager, constructed just above, is the latest of the six).
        from .secrets_service import SecretsService
        self.secrets_service = SecretsService(
            self.credential_vault,
            self.vault_refresh_manager,
            self.session_manager,
            self.template_manager,
            self.profile_manager,
            self.mcp_config_manager,
        )

        # OAuth 2.1 token/secret-bundle management (issue #2189 AC5, router-boundary
        # PR B) — all 4 collaborators (credential_vault, oauth_manager,
        # mcp_config_manager, vault_refresh_manager) already exist by this point.
        from .oauth_secret_bundle_manager import OAuthSecretBundleManager
        self.oauth_secret_bundle_manager = OAuthSecretBundleManager(
            self.credential_vault,
            self.oauth_manager,
            self.mcp_config_manager,
            self.vault_refresh_manager,
        )

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

        # Initialize Legion multi-agent system
        from backend.config_manager import load_config
        from backend.legion_system import LegionSystem
        # Create a dummy storage manager for now (legion will create its own per-legion storage)
        dummy_storage = DataStorageManager(self.data_dir / "legion_temp")
        _app_config = load_config()
        self.legion_system = LegionSystem(
            session_coordinator=self,
            data_storage_manager=dummy_storage,
            template_manager=self.template_manager,
            default_max_minions=_app_config.legion.max_concurrent_minions,
            history_retention=_app_config.history_retention,
        )

        # Issue #500: Message queue system
        self.queue_manager = QueueManager()
        self.queue_processor = QueueProcessor(self)

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
        from backend.mcp.resource_mcp_tools import ResourceMCPTools
        self.resource_mcp_tools = ResourceMCPTools(
            session_coordinator=self,
            broadcast_callback=self._broadcast_resource_registered
        )

        # Issue #1530: Links MCP tools for registering persistent named links
        # Callback for broadcasting link_registered events will be set by web_server
        from backend.mcp.links_mcp_tools import LinksMCPTools
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

    def _apply_audit_writer(self, storage_manager, session_id: str) -> None:
        """Configure audit callbacks on a newly created DataStorageManager."""
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
    ) -> dict:
        """Enqueue a message for a session. Returns the queue item dict."""
        return await self.queue_processor.enqueue_message(
            session_id, content, reset_session=reset_session,
            metadata=metadata, inject_timestamp=inject_timestamp,
        )

    async def cancel_queue_item(self, session_id: str, queue_id: str) -> dict | None:
        """Cancel a pending queue item. Returns the item dict or None."""
        return await self.queue_processor.cancel_queue_item(session_id, queue_id)

    async def requeue_item(self, session_id: str, queue_id: str) -> dict | None:
        """Re-queue a sent/failed item at the front. Returns the new item dict."""
        return await self.queue_processor.requeue_item(session_id, queue_id)

    async def clear_queue(self, session_id: str) -> int:
        """Clear all pending queue items. Returns count of cancelled items."""
        return await self.queue_processor.clear_queue(session_id)

    async def clear_queue_history(self, session_id: str) -> int:
        """Remove terminal (sent/failed/cancelled) queue items. Returns count removed."""
        return await self.queue_processor.clear_queue_history(session_id)

    async def get_queue(self, session_id: str, limit: int = 100, offset: int = 0) -> dict:
        """Get queue items for a session, paginated, most recent first."""
        return self.queue_manager.get_queue_page(session_id, limit=limit, offset=offset)

    async def pause_queue(self, session_id: str, paused: bool) -> bool:
        """Pause or resume the queue for a session."""
        return await self.queue_processor.pause_queue(session_id, paused)

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
        import uuid
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
            # Step 0: If this session has children, recursively delete all children first (cascading)
            # Check child_minion_ids regardless of is_minion/is_overseer flags - any session with
            # children should cascade the deletion
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info and session_info.child_minion_ids:
                child_ids = list(session_info.child_minion_ids)  # Make a copy
                coord_logger.info(f"Session {session_id} has {len(child_ids)} children - cascading deletion")

                for child_id in child_ids:
                    coord_logger.info(f"Cascading: deleting child minion {child_id} of parent {session_id}")
                    # Recursively delete child (which may have its own children)
                    # Pass archive_reason through to children
                    result = await self.delete_session(child_id, archive_reason=archive_reason)
                    if result.get("success"):
                        deleted_ids.extend(result.get("deleted_session_ids", []))

                coord_logger.info(f"Cascading deletion complete for {session_id} - all {len(child_ids)} children deleted")

            # Step 1: Find and remove session from its project
            project = await self.find_project_for_session(session_id)
            project_was_deleted = False

            if project:
                removal_success, project_was_deleted = await self.project_manager.remove_session_from_project(project.project_id, session_id)

                if removal_success:
                    if project_was_deleted:
                        coord_logger.info(f"Removed session {session_id} from project {project.project_id} - project was empty and has been deleted")
                    else:
                        coord_logger.info(f"Removed session {session_id} from project {project.project_id}")
                        # Issue #1722: strip the deleted session's kanban group assignment,
                        # if any (no-op if the project itself was just deleted above)
                        await self.project_manager.cleanup_session_group_assignment(project.project_id, session_id)
                else:
                    logger.warning(f"Failed to remove session {session_id} from project {project.project_id}")

            # Step 1.5: If this is a minion with a parent overseer, remove from parent's child_minion_ids
            # (session_info was already fetched in Step 0)
            if not session_info:
                session_info = await self.session_manager.get_session_info(session_id)

            # Issue #349: All sessions are minions - check parent relationship directly
            if session_info and session_info.parent_overseer_id:
                parent_id = session_info.parent_overseer_id
                parent_info = await self.session_manager.get_session_info(parent_id)

                if parent_info and session_id in parent_info.child_minion_ids:
                    parent_info.child_minion_ids.remove(session_id)
                    parent_info.updated_at = datetime.now(UTC)
                    await self.session_manager._persist_session_state(parent_id)
                    coord_logger.info(f"Removed minion {session_id} from parent overseer {parent_id}'s child_minion_ids")
                elif parent_info:
                    coord_logger.warning(f"Minion {session_id} not found in parent overseer {parent_id}'s child_minion_ids")
                else:
                    coord_logger.warning(f"Parent overseer {parent_id} not found for minion {session_id}")

            # Step 1.55: Delete schedules for deleted session (Issue #671)
            if self.legion_system:
                try:
                    deleted = await self.legion_system.scheduler_service.delete_schedules_for_minion(session_id)
                    if deleted:
                        coord_logger.info(
                            f"Deleted {deleted} schedules for deleted session {session_id}"
                        )
                except Exception as e:
                    coord_logger.warning(
                        f"Failed to delete schedules for session {session_id}: {e}"
                    )

            # Step 1.6: Clean up capability registry if session has capabilities (issue #349: all sessions are minions)
            if session_info and session_info.capabilities:
                # Clean up capability registry
                if project and self.legion_system:
                    self.legion_system.legion_coordinator.unregister_minion_capabilities(session_id)
                    coord_logger.info(f"Cleaned up {len(session_info.capabilities)} capabilities from registry for minion {session_id}")

            # Step 1.65: Terminate SDK and update state BEFORE archive (Issue #236)
            # This ensures archive captures final "terminated" state (same pattern as dispose_minion)
            sdk = self._active_sdks.get(session_id)
            if sdk:
                await sdk.terminate()
                del self._active_sdks[session_id]
                await asyncio.sleep(0.2)  # Give SDK time to fully close

            # Update session state to terminated (so archive captures correct final_state)
            if session_info and session_info.state != SessionState.TERMINATED:
                await self.session_manager.terminate_session(session_id)
                coord_logger.info(f"Terminated session {session_id} before archive/deletion")

            # Step 1.7: Archive session before deletion (Issue #236)
            # Archive any session in a project before deletion
            if session_info and project and self.legion_system:
                try:
                    # Get parent info for archive metadata
                    parent_name = None
                    if session_info.parent_overseer_id:
                        parent_info = await self.session_manager.get_session_info(session_info.parent_overseer_id)
                        parent_name = parent_info.name if parent_info else None

                    archive_result = await self.legion_system.archive_manager.archive_minion(
                        minion_id=session_id,
                        reason=archive_reason,
                        parent_overseer_id=session_info.parent_overseer_id,
                        parent_overseer_name=parent_name,
                        descendants_count=len(deleted_ids),  # Children already deleted in cascade
                        will_be_deleted=True  # This is a hard delete
                    )
                    if archive_result.success:
                        coord_logger.info(f"Archived session {session_id} to {archive_result.archive_path} before deletion")
                    else:
                        coord_logger.warning(f"Failed to archive session {session_id}: {archive_result.error_message}")
                except Exception:
                    coord_logger.exception(f"Error archiving session {session_id} before deletion")

            # Step 1.8: Legion-specific cleanup (issue #349: all sessions are minions)
            if session_info and project and self.legion_system:
                legion_id = project.project_id
                coord_logger.info(f"Starting Legion-specific cleanup for minion {session_id} in legion {legion_id}")

                # 1.8a: Delete minion directory in legions/{legion_id}/minions/{minion_id}/
                minion_dir = self.data_dir / "legions" / legion_id / "minions" / session_id
                if minion_dir.exists():
                    try:
                        import shutil
                        shutil.rmtree(minion_dir)
                        coord_logger.info(f"Deleted Legion minion directory: {minion_dir}")
                    except Exception:
                        coord_logger.exception(f"Failed to delete Legion minion directory {minion_dir}")
                else:
                    coord_logger.debug(f"Legion minion directory does not exist (already cleaned): {minion_dir}")

            # Step 2: Clean up storage manager and ensure all files are closed
            if session_id in self._storage_managers:
                storage_manager = self._storage_managers[session_id]
                # logger.info(f"Cleaning up storage manager for session {session_id}")
                await storage_manager.cleanup()
                del self._storage_managers[session_id]
                # Give storage manager time to close all file handles
                await asyncio.sleep(0.2)

            # Step 3: Clean up callbacks
            if session_id in self._message_callbacks:
                del self._message_callbacks[session_id]
            if session_id in self._error_callbacks:
                del self._error_callbacks[session_id]
            # Issue #858: Cleanup per-session tool-call event
            self._tool_call_events.pop(session_id, None)
            # Issue #1694: Cleanup per-session message-emitted barrier state
            self._message_emitted_events.pop(session_id, None)
            self._emitted_turn_ids.pop(session_id, None)

            # Step 4: Delete through session manager (this removes from active sessions and deletes files)
            # Issue #1942: the unconditional gc.collect() calls formerly here (and the
            # Windows-specific one that followed) reclaimed nothing — storage_manager.cleanup()
            # already closes every file handle deterministically (see #1933), and the real
            # Windows fallback for a stuck directory delete already lives, properly gated on
            # an actual shutil.rmtree() failure, inside session_manager.delete_session() below.
            # logger.info(f"Deleting session files for session {session_id}")
            success = await self.session_manager.delete_session(session_id)

            if success:
                coord_logger.info(f"Session {session_id} deleted")
                # Issue #1840/#1941: flush any unflushed subagent usage before marking
                # analytics deleted below — deletion no longer wipes analytics rows, so
                # this is a real, permanent persistence opportunity, not a no-op. Also
                # owns clearing _last_turn_model_by_session (formerly a standalone
                # Issue #1831 pop here; folded into the shared helper since the flush
                # needs that same value to attribute its catch-up row). allow_retry=False:
                # unlike terminate_session(), this session_id is gone for good right after
                # this — restoring the accumulator on a write failure would just leak it in
                # memory forever with no code path left to ever retry the flush.
                await self.flush_unflushed_subagent_usage(session_id, allow_retry=False)
                # Issue #1941: mark analytics deleted instead of removing rows, so
                # historical totals survive session/project deletion.
                if self.analytics_store:
                    try:
                        await self.analytics_store.mark_session_deleted(session_id)
                        self._turn_seq_by_session.pop(session_id, None)
                        self._usage_baseline_by_session.pop(session_id, None)
                    except Exception:
                        logger.exception("Failed to mark analytics deleted for session %s", session_id)
                # Notify about session deletion (issue #1986: dedicated event, mirrors
                # session_reset — _notify_state_change() requires a SessionState enum
                # member and there is no DELETED one, and by this point the session is
                # already gone from session_manager so a state-change lookup would no-op)
                await self._notify_session_deleted(session_id)
                # Add this session to the deleted list
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
        pass False — they aren't user messages and injecting into them would desync the
        Legion timeline (comm.content persisted pre-injection) from the delivered text.
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
        from .routers._models import _validate_new_additional_directory

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

    async def _get_task_leg_registry(self, session_id: str) -> TaskLegRegistry:
        # Kept live on the coordinator (not just internal to SessionQuery):
        # message_pipeline.py calls self._coordinator._get_task_leg_registry(session_id)
        # directly, and test_task_registry.py pokes the same name.
        return await self._session_query._get_task_leg_registry(session_id)

    async def get_background_agents(self, session_id: str) -> dict[str, Any]:
        return await self._session_query.get_background_agents(session_id)

    # ==================== ARCHIVE METHODS ====================

    async def get_archives(self, session_id: str, limit: int = 50, offset: int = 0) -> dict:
        return await self._archive_coordinator.get_archives(session_id, limit, offset)

    async def get_archive_messages(
        self, session_id: str, archive_id: str, offset: int = 0, limit: int | None = 50
    ) -> dict:
        return await self._archive_coordinator.get_archive_messages(
            session_id, archive_id, offset, limit
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
        offset: int = 0
    ) -> dict[str, Any]:
        """Get messages from a session with pagination metadata.

        Issue #2109 (AC3): returns stored records unchanged — every tool
        lifecycle transition is stored and emitted live (issue #2084 stage 3,
        plus #2109's AC1/AC2 closing the restart/pending-emission gaps), so
        no read-time synthesis is needed or performed.
        """
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
            from backend.config_resolution import CONFIG_FIELDS, resolve_template_config
            from backend.session_config import DEFAULTS

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
            from backend.config_resolution import (
                _MCP_INHERITABLE_FIELDS,
                _mcp_field_equal,
                resolve_template_config,
            )

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
