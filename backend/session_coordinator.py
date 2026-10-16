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
from uuid import uuid4

from backend.docker_utils import classify_docker_output
from shared.logging_config import get_logger

from .claude_sdk import ClaudeSDK, ResultError
from .config_resolution import resolve_effective_config
from .data_storage import DataStorageManager
from .hooks.pretooluse_handler import InternalPermissionHandler
from .litellm_proxy_manager import make_model_alias
from .message_parser import MessageParser, MessageProcessor
from .models.messages import (
    LegacyMessageFormatError,
    MessageRecord,
    PermissionInfo,
    ToolCall,
    ToolDisplayInfo,
    ToolState,
    _message_dict_to_record,
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
from .session_recorder import SessionRecorder
from .task_registry import TASK_LIFECYCLE_SUBTYPES, TaskLegRegistry
from .task_utils import task_done_log_exception
from .timestamp_injection import maybe_inject_timestamp
from .timestamp_utils import get_unix_timestamp, parse_rate_limit_reset_time

# Get specialized logger for coordinator actions
coord_logger = get_logger('coordinator', category='COORDINATOR')
# Keep standard logger for errors
logger = logging.getLogger(__name__)

# Issue #1572: auto-resume timing — fire 5 minutes after a known rate-limit reset,
# or fall back to a flat 1 hour out when no fresh reset timestamp is available (AC4).
AUTO_RESUME_GRACE_PERIOD_SECONDS = 5 * 60
AUTO_RESUME_FALLBACK_DELAY_SECONDS = 60 * 60

# Resource format groups for filtering
_TEXT_RESOURCE_FORMATS = {
    "py", "js", "ts", "json", "md", "txt", "yaml", "yml",
    "toml", "cfg", "ini", "log", "csv", "xml", "html", "css",
    "sh", "bash", "rs", "go", "java", "c", "cpp", "h", "rb",
    "php", "sql", "r", "swift", "kt",
}

# Issue #1660: MCP config fields that the spawn path snapshots into session.config.
# These are the only fields subject to stale-snapshot pruning.
_MCP_INHERITABLE_FIELDS = ("mcp_server_ids", "enable_claudeai_mcp_servers", "strict_mcp_config")


def _mcp_field_equal(field: str, a, b) -> bool:
    """Compare an MCP config field value, treating mcp_server_ids order-insensitively.

    None and [] are treated as distinct: None means "inherit from chain", [] means
    "explicitly no servers". Only a list-vs-list comparison uses set equality.
    """
    if field == "mcp_server_ids":
        if (a is None) != (b is None):
            return False
        return set(a or []) == set(b or [])
    return a == b


def _apply_resource_filters(
    resources: list[dict],
    search: str | None,
    format_filter: str | None,
    sort: str,
) -> list[dict]:
    """Filter and sort a list of resource dicts. Returns a new list."""
    result = list(resources)

    if search:
        q = search.lower()
        result = [
            r
            for r in result
            if q in (r.get("title") or "").lower() or q in (r.get("original_name") or "").lower()
        ]

    if format_filter:
        if format_filter == "image":
            result = [r for r in result if r.get("is_image")]
        elif format_filter == "video":
            result = [r for r in result if r.get("is_video")]
        elif format_filter == "text":
            result = [r for r in result if r.get("format") in _TEXT_RESOURCE_FORMATS]
        else:
            result = [r for r in result if r.get("format") == format_filter]

    if sort == "newest":
        result.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    elif sort == "oldest":
        result.sort(key=lambda x: x.get("timestamp", 0))
    elif sort == "name-asc":
        result.sort(key=lambda x: (x.get("title") or x.get("original_name") or "").lower())
    elif sort == "name-desc":
        result.sort(
            key=lambda x: (x.get("title") or x.get("original_name") or "").lower(),
            reverse=True,
        )

    return result


def _group_resources_by_filename(resources: list[dict]) -> list[dict]:
    """Group resources into versions by original_name (case-insensitive).

    Issue #1680: re-registering a resource under the same filename represents
    an update, not a new independent entry. Groups by the full filename
    (including extension) so "report.md" and "report.txt" stay separate.

    Each returned item is the group's latest entry (by timestamp), augmented
    with `version_count`. Groups with more than one entry also carry a
    `versions` list (newest-first, each entry tagged with `version_number`,
    oldest = 1). Single-entry groups pass through with only `version_count=1`.
    """
    groups: dict[str, list[dict]] = {}
    for r in resources:
        key = (r.get("original_name") or "").lower()
        groups.setdefault(key, []).append(r)

    result = []
    for entries in groups.values():
        ordered = sorted(entries, key=lambda x: x.get("timestamp", 0))
        version_count = len(ordered)
        versions = [dict(entry, version_number=i + 1) for i, entry in enumerate(ordered)]
        versions.reverse()  # newest first

        latest = dict(versions[0])
        latest["version_count"] = version_count
        if version_count > 1:
            latest["versions"] = versions
        result.append(latest)

    return result


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


def _tail_read_lines(path: "Path", limit: int) -> list[str]:
    """Read the last `limit` lines from a file efficiently using a deque."""
    from collections import deque

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return list(deque(f, maxlen=limit))
    except OSError:
        return []


def _count_file_lines(path: "Path") -> int:
    """Count total lines in a file efficiently."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


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

        # Issue #403: Uploaded file paths per session for auto-approve Read permissions
        # Maps session_id -> set of absolute file paths
        self._uploaded_file_paths: dict[str, set[str]] = {}

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
        self._resource_broadcast_callback: Callable[[str, dict], None] | None = None
        # Callback for broadcasting queue_update events (enqueued) will be set by web_server
        self._enqueue_broadcast_callback: Callable[[str, str, dict], Any] | None = None
        self.resource_mcp_tools = ResourceMCPTools(
            session_coordinator=self,
            broadcast_callback=self._broadcast_resource_registered
        )

        # Issue #1530: Links MCP tools for registering persistent named links
        # Callback for broadcasting link_registered events will be set by web_server
        from backend.mcp.links_mcp_tools import LinksMCPTools
        self._link_broadcast_callback: Callable[[str, dict], None] | None = None
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
            return await self._on_rate_limits(rate_limit_info)

        launcher_callbacks = LauncherCallbacks(
            create_message_callback=self._create_message_callback,
            create_error_callback=self._create_error_callback,
            create_stderr_callback=self._create_stderr_callback,
            create_phase_callback=self._create_phase_callback,
            create_auto_approval_callback=self._create_auto_approval_callback,
            on_rate_limits=_on_rate_limits_dispatch,
            transition_to_error=self._transition_to_error,
            send_session_failure_message=self._send_session_failure_message,
            send_client_launched_message=self._send_client_launched_message,
            send_mcp_degraded_message=self._send_mcp_degraded_message,
            result_error_structured_kwargs=self._result_error_structured_kwargs,
            extract_claude_cli_error=self._extract_claude_cli_error,
            # Issue #2189: the one field with a demonstrated staleness bug — two
            # existing tests do `patch.object(coordinator, "mark_session_tools_interrupted")`
            # AFTER construction, which a pre-captured bound method would never see.
            # A lambda defers the `self.X` attribute lookup to call time instead.
            mark_session_tools_interrupted=lambda session_id: self.mark_session_tools_interrupted(session_id),
            notify_session_reset=self._notify_session_reset,
        )
        self._session_launcher = SessionLauncher(
            coordinator=self,
            callbacks=launcher_callbacks,
        )

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
        """Issue #2111: repair tool calls left non-terminal across a genuine Backend
        process crash.

        mark_session_tools_interrupted() is the only code that ever writes a terminal
        INTERRUPTED tool_call record, and it operates purely on the in-memory
        _active_tool_calls dict — populated only while a live Backend process is
        running. If the process crashes outright (not a graceful terminate_session()/
        interrupt_session()/restart_session()/critical-error callback), none of those
        call sites ever runs, and the open tool's last on-disk record stays
        pending/awaiting_permission/running forever.

        Scoped to exactly the sessions self.session_manager flags as needing a repair
        scan this boot (the only ones that could plausibly have had an open tool call)
        so this is a no-op once a session has settled into TERMINATED/CREATED on a
        prior boot. Mirrors mark_session_tools_interrupted()'s mutation exactly, but
        writes directly via get_or_create_storage_manager() + append_message() since
        _active_tool_calls is empty at boot and there's no broadcast subscriber yet to
        notify.

        Issue #2127: this repair write lands before BackendApp wires its AuditWriter
        (set_audit_writer() runs after coordinator.initialize() returns), so there's
        no `on_append` hook attached yet to capture the synthetic repaired record the
        way every live mark_session_tools_interrupted() write is captured. Instead,
        each repaired record is queued into `self._crash_repair_audit_backlog`
        alongside the storage write; `set_audit_writer()` drains that backlog and
        replays the records into the real AuditWriter once it exists. Each session is
        repaired independently and failures are isolated (see except below) so a
        single bad session can't block the others or abort Backend startup, but
        nothing currently retries a session whose repair itself fails this boot;
        that remains an accepted scope boundary for this issue, not addressed here.
        """
        import time

        non_terminal_statuses = {
            ToolState.PENDING.value,
            ToolState.AWAITING_PERMISSION.value,
            ToolState.RUNNING.value,
        }

        for session_id in self.session_manager.get_sessions_needing_tool_repair_scan():
            try:
                storage = await self.get_or_create_storage_manager(session_id)
                if storage is None:
                    continue

                # Cumulative snapshots: keep only the last-occurring record per
                # tool_use_id (append order, not timestamp value).
                latest_by_tool_use_id: dict[str, dict[str, Any]] = {}
                for record in await storage.read_messages():
                    if record.get("type") != "tool_call":
                        continue
                    tool_use_id = record.get("tool_use_id")
                    if tool_use_id:
                        latest_by_tool_use_id[tool_use_id] = record

                for latest_record in latest_by_tool_use_id.values():
                    if latest_record.get("status") not in non_terminal_statuses:
                        continue

                    tool_call = ToolCall.from_dict(latest_record)
                    tool_call.status = ToolState.INTERRUPTED
                    tool_call.completed_at = time.time()
                    tool_call.repair_reason = "boot_crash_repair"
                    if tool_call.display:
                        tool_call.display.state = ToolState.INTERRUPTED
                        tool_call.display.style = "orphaned"

                    record = MessageRecord.from_tool_call(tool_call).to_dict()
                    await storage.append_message(record)
                    self._crash_repair_audit_backlog.append((session_id, record))
                    coord_logger.info(
                        f"Issue #2111: repaired crash-orphaned tool call "
                        f"{tool_call.tool_use_id} for session {session_id} on boot"
                    )
            except Exception:
                # Don't raise - one session's repair failure shouldn't block
                # coordinator initialization or the repair of other sessions.
                coord_logger.exception(
                    f"Issue #2111: failed to repair crash-orphaned tool calls for "
                    f"session {session_id}"
                )

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
        try:
            sessions = await self.session_manager.list_sessions()
            for session in sessions:
                session_id = session.session_id
                session_dir = await self.session_manager.get_session_directory(session_id)
                if session_dir:
                    await self.queue_manager.load_queue(session_id, session_dir)
                    # Auto-start processor if there are pending items
                    if self.queue_manager.get_pending_count(session_id) > 0:
                        queue_paused = getattr(session, 'queue_paused', False)
                        if not queue_paused:
                            self.queue_processor.ensure_running(session_id)
        except Exception:
            logger.exception("Failed to initialize queues")

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
        session_info = await self.session_manager.get_session_info(session_id)
        if not session_info:
            raise ValueError(f"Session {session_id} not found")

        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            raise ValueError(f"Session directory not found for {session_id}")

        queue_config = getattr(session_info, 'queue_config', None) or {}
        max_queue_size = queue_config.get("max_queue_size", 100)

        # Use session default if not specified
        if reset_session is None:
            reset_session = queue_config.get("default_reset_session", True)

        item = await self.queue_manager.enqueue(
            session_id=session_id,
            session_dir=session_dir,
            content=content,
            reset_session=reset_session,
            metadata=metadata,
            max_queue_size=max_queue_size,
            inject_timestamp=inject_timestamp,
        )

        # Start processor if not paused
        queue_paused = getattr(session_info, 'queue_paused', False)
        if not queue_paused:
            self.queue_processor.ensure_running(session_id)

        item_dict = item.to_dict()
        if self._enqueue_broadcast_callback:
            try:
                if asyncio.iscoroutinefunction(self._enqueue_broadcast_callback):
                    await self._enqueue_broadcast_callback(session_id, "enqueued", item_dict)
                else:
                    self._enqueue_broadcast_callback(session_id, "enqueued", item_dict)
            except Exception:
                logger.exception(f"Failed to broadcast queue enqueued for {session_id}")
        return item_dict

    async def cancel_queue_item(self, session_id: str, queue_id: str) -> dict | None:
        """Cancel a pending queue item. Returns the item dict or None."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return None
        item = await self.queue_manager.cancel(session_id, session_dir, queue_id)
        return item.to_dict() if item else None

    async def requeue_item(self, session_id: str, queue_id: str) -> dict | None:
        """Re-queue a sent/failed item at the front. Returns the new item dict."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return None
        item = await self.queue_manager.requeue(session_id, session_dir, queue_id)
        if item:
            session_info = await self.session_manager.get_session_info(session_id)
            queue_paused = getattr(session_info, 'queue_paused', False) if session_info else False
            if not queue_paused:
                self.queue_processor.ensure_running(session_id)
        return item.to_dict() if item else None

    async def clear_queue(self, session_id: str) -> int:
        """Clear all pending queue items. Returns count of cancelled items."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return 0
        return await self.queue_manager.clear_pending(session_id, session_dir)

    async def clear_queue_history(self, session_id: str) -> int:
        """Remove terminal (sent/failed/cancelled) queue items. Returns count removed."""
        session_dir = await self.session_manager.get_session_directory(session_id)
        if not session_dir:
            return 0
        return await self.queue_manager.clear_history(session_id, session_dir)

    async def get_queue(self, session_id: str, limit: int = 100, offset: int = 0) -> dict:
        """Get queue items for a session, paginated, most recent first."""
        all_items = [item.to_dict() for item in self.queue_manager.get_queue(session_id)]
        all_items.reverse()  # Most recent (highest position) first
        total = len(all_items)
        pending_count = sum(1 for i in all_items if i.get("status") == "pending")
        sliced = all_items[offset : offset + limit]
        return {
            "items": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
            "pending_count": pending_count,
        }

    async def pause_queue(self, session_id: str, paused: bool) -> bool:
        """Pause or resume the queue for a session."""
        success = await self.session_manager.update_session(
            session_id, queue_paused=paused
        )
        if success and not paused:
            # Resume: start processor if there are pending items
            if self.queue_manager.get_pending_count(session_id) > 0:
                self.queue_processor.ensure_running(session_id)
        return success

    async def update_queue_config(self, session_id: str, config: dict) -> bool:
        """Update queue configuration for a session."""
        session_info = await self.session_manager.get_session_info(session_id)
        if not session_info:
            return False
        current = getattr(session_info, 'queue_config', None) or {}
        current.update(config)
        return await self.session_manager.update_session(
            session_id, queue_config=current
        )

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
        """
        Get resource metadata for a session, paginated with optional filter/sort.

        Issue #404: Used by REST endpoint to list session resources.
        Issue #972: Added server-side search, format_filter, and sort params.

        Args:
            session_id: Session ID
            limit: Maximum number of resources to return
            offset: Number of resources to skip
            search: Substring match on title or original_name (case-insensitive)
            format_filter: "image", "text", or a specific extension (e.g. "pdf")
            sort: "newest" | "oldest" | "name-asc" | "name-desc"

        Returns:
            Paginated dict with resources, total, limit, offset, has_more
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            # Try to get from session directory
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        all_resources: list[dict] = []
        if storage_manager:
            all_resources = await storage_manager.read_resources()

        all_resources = _group_resources_by_filename(all_resources)
        all_resources = _apply_resource_filters(all_resources, search, format_filter, sort)

        total = len(all_resources)
        sliced = all_resources[offset : offset + limit]
        return {
            "resources": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def get_session_resource_by_id(self, session_id: str, resource_id: str) -> dict | None:
        """
        Look up a single resource's metadata by ID against the raw (ungrouped) log.

        Issue #1680: byte-serving endpoints must resolve the exact version
        requested, not just the latest representative of its filename group.

        Args:
            session_id: Session ID
            resource_id: Resource ID

        Returns:
            Resource metadata dict or None
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if not storage_manager:
            return None

        all_resources = await storage_manager.read_resources()
        return next((r for r in all_resources if r.get("resource_id") == resource_id), None)

    async def get_session_resource_file(self, session_id: str, resource_id: str) -> bytes | None:
        """
        Get raw file bytes for a specific resource.

        Issue #404: Used by REST endpoint to serve resource files.

        Args:
            session_id: Session ID
            resource_id: Resource ID

        Returns:
            Raw file bytes or None
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if storage_manager:
            return await storage_manager.get_resource_file(resource_id)

        return None

    async def get_proxy_logs(self, session_id: str, log_type: str = "http", limit: int = 200) -> dict:
        """
        Get proxy log entries for a session.

        Issue #1102: Reads access.log (HTTP) or dns.log (DNS) from the proxy sidecar
        data directory and returns the last `limit` parsed entries.
        Issue #1214: Also supports socks5.log for SOCKS5 connection entries.

        Args:
            session_id: Session ID
            log_type: "http", "dns", or "socks5"
            limit: Maximum number of entries to return (tail-read)

        Returns:
            dict with entries, total_lines, and log_type
        """
        session_dir = self.data_dir / "sessions" / session_id
        if log_type == "http":
            log_path = session_dir / "docker_claude_data" / "proxy" / "access.log"
        elif log_type == "socks5":
            log_path = session_dir / "docker_claude_data" / "proxy" / "socks5.log"
        else:
            log_path = session_dir / "docker_claude_data" / "proxy" / "dns.log"

        if not log_path.exists():
            return {"entries": [], "total_lines": 0, "log_type": log_type}

        # Tail-read: read last `limit` lines efficiently
        lines = _tail_read_lines(log_path, limit)
        total_lines = _count_file_lines(log_path)

        entries = []
        if log_type == "http":
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    entries.append({
                        "ts": raw.get("ts"),
                        "host": raw.get("host"),
                        "method": raw.get("method"),
                        "path": raw.get("path"),
                        "status": raw.get("status"),
                        "allowed": raw.get("allowed"),
                        "credential_used": raw.get("credential_used"),
                        "scheme": raw.get("scheme"),
                        "port": raw.get("port"),
                        "bytes": raw.get("bytes"),
                        "routed_via_litellm": raw.get("routed_via_litellm", False),
                        "original_host": raw.get("original_host", raw.get("host")),
                    })
                except (json.JSONDecodeError, ValueError):
                    self.logger.warning(f"Skipping malformed HTTP proxy log line in session {session_id}")
        elif log_type == "socks5":
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    entries.append({
                        "ts": raw.get("ts"),
                        "host": raw.get("host"),
                        "port": raw.get("port"),
                        "allowed": raw.get("allowed"),
                        "reason": raw.get("reason"),
                    })
                except (json.JSONDecodeError, ValueError):
                    self.logger.warning(f"Skipping malformed SOCKS5 proxy log line in session {session_id}")
        else:
            # CoreDNS log format:
            # [INFO] 10.0.0.2:54312 - 12345 "A IN api.github.com. udp 128 false 4096" NOERROR qr,rd,ra 73 0.023s
            dns_pattern = re.compile(
                r'\[INFO\].*?"(\w+) IN (\S+?)\. \w+ \d+ \w+ \d+"\s+(\w+)'
            )
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                m = dns_pattern.search(line)
                if m:
                    entries.append({
                        "query_type": m.group(1),
                        "hostname": m.group(2),
                        "result": m.group(3),
                    })

        return {"entries": entries, "total_lines": total_lines, "log_type": log_type}

    async def remove_session_resource(self, session_id: str, resource_id: str) -> bool:
        """
        Soft-remove a resource from the session display.

        Issue #423: Appends a removal marker to resources.jsonl so the resource
        is hidden from the gallery. The .bin file is NOT deleted.

        Args:
            session_id: Session ID
            resource_id: Resource ID to remove from display

        Returns:
            True if removed successfully, False otherwise
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if storage_manager:
            return await storage_manager.remove_resource_from_display(resource_id)

        return False

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
        """
        Set the callback for broadcasting resource_registered events to WebSocket.

        Issue #404: Resource MCP tool integration.

        Args:
            callback: Async function(session_id, resource_metadata) for WebSocket broadcast
        """
        self._resource_broadcast_callback = callback
        coord_logger.info("Resource broadcast callback registered in SessionCoordinator")

    # Backward compatibility alias
    def set_image_broadcast_callback(self, callback: Callable[[str, dict], None]) -> None:
        """Deprecated: Use set_resource_broadcast_callback instead."""
        self.set_resource_broadcast_callback(callback)

    def set_link_broadcast_callback(self, callback: Callable[[str, dict], None]) -> None:
        """Set the callback for broadcasting link_registered events to the session poll queue.

        Issue #1530: Called by web_server after coordinator is initialised.
        """
        self._link_broadcast_callback = callback
        coord_logger.info("Link broadcast callback registered in SessionCoordinator")

    def set_enqueue_broadcast_callback(self, callback: Callable[[str, str, dict], Any]) -> None:
        """Set the callback for broadcasting queue_update (enqueued) events."""
        self._enqueue_broadcast_callback = callback
        coord_logger.info("Enqueue broadcast callback registered in SessionCoordinator")

    async def _broadcast_resource_registered(self, session_id: str, resource_metadata: dict) -> None:
        """
        Broadcast resource_registered event via the injected callback.

        Issue #404: Called by ResourceMCPTools when a resource is registered.

        Args:
            session_id: Session that registered the resource
            resource_metadata: Resource metadata dict (resource_id, title, is_image, etc.)
        """
        if self._resource_broadcast_callback:
            try:
                if asyncio.iscoroutinefunction(self._resource_broadcast_callback):
                    await self._resource_broadcast_callback(session_id, resource_metadata)
                else:
                    self._resource_broadcast_callback(session_id, resource_metadata)
            except Exception:
                logger.exception(f"Failed to broadcast resource_registered for {session_id}")

    async def _broadcast_link_registered(self, session_id: str, link: dict) -> None:
        """Broadcast link_registered event via the injected callback.

        Issue #1530: Called by LinksMCPTools when a link is registered or updated.
        """
        if self._link_broadcast_callback:
            try:
                if asyncio.iscoroutinefunction(self._link_broadcast_callback):
                    await self._link_broadcast_callback(session_id, link)
                else:
                    self._link_broadcast_callback(session_id, link)
            except Exception:
                logger.exception(f"Failed to broadcast link_registered for {session_id}")

    async def get_session_links(self, session_id: str) -> list[dict]:
        """Return the list of agent-registered links for a session.

        Issue #1530: Used by the REST endpoint GET /api/sessions/{id}/links.
        """
        session_info = await self.session_manager.get_session_info(session_id)
        if not session_info:
            return []
        return session_info.links or []

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
        """Archive session data and clear messages, then terminate.

        Used by the scheduler after an ephemeral schedule run completes.
        Archives with the current timestamp (completion time), clears messages,
        and terminates the session — leaving it in TERMINATED state ready for
        the next scheduled fire.

        Unlike reset_session(), this does NOT restart the session after clearing.

        Args:
            session_id: The session to archive and clear

        Returns:
            True if cleanup succeeded
        """
        try:
            coord_logger.info(f"Archive-and-clear session {session_id}")

            # Archive session data (copies messages, state, resources to timestamped dir)
            await self._archive_session_for_reset(session_id)

            # Clear message history
            storage = self._storage_managers.get(session_id)
            if not storage:
                session_dir = await self.session_manager.get_session_directory(session_id)
                storage = DataStorageManager(session_dir)
                await storage.initialize()
                self._storage_managers[session_id] = storage

            if storage:
                await storage.clear_messages()
                coord_logger.info(f"Cleared message history for session {session_id}")

            # Issue #1746: Drop cached task leg registry — it would otherwise
            # keep serving stale legs from before the message history wipe.
            self._task_leg_registries.pop(session_id, None)

            # Notify frontend to clear messages
            await self._notify_session_reset(session_id)

            # Terminate the session (disconnect SDK, set TERMINATED state)
            await self.terminate_session(session_id)

            coord_logger.info(f"Archive-and-clear complete for session {session_id}")
            return True

        except Exception:
            logger.exception(f"Failed to archive-and-clear session {session_id}")
            return False

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
            project = await self._find_project_for_session(session_id)
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

    async def _find_project_for_session(self, session_id: str) -> ProjectInfo | None:
        """Find the project that contains a given session"""
        projects = await self.project_manager.list_projects()
        for project in projects:
            if session_id in project.session_ids:
                return project
        return None

    async def find_project_for_session(self, session_id: str) -> ProjectInfo | None:
        """Public wrapper: find the project containing the given session."""
        return await self._find_project_for_session(session_id)

    async def get_all_descendants(self, session_id: str) -> list[dict]:
        """Return the complete descendant list (unpaginated). Use for
        membership/cycle checks where truncation produces incorrect results."""
        return await self._get_all_descendants(session_id)

    async def _get_all_descendants(self, session_id: str) -> list[dict]:
        """
        Get all descendant sessions (children, grandchildren, etc.) of a session.

        Returns a list of session info dicts for all descendants, or empty list
        if the session has no children or doesn't exist.
        """
        descendants = []

        session_info = await self.session_manager.get_session_info(session_id)
        if not session_info:
            return descendants

        # Check if session has any children - any session with child_minion_ids
        # can have descendants, regardless of is_minion/is_overseer flags
        if not session_info.child_minion_ids:
            return descendants

        # Process each child
        for child_id in session_info.child_minion_ids:
            child_info = await self.session_manager.get_session_info(child_id)
            if child_info:
                descendants.append({
                    "session_id": child_id,
                    "name": child_info.name,
                    "role": child_info.role,
                    "state": child_info.state.value if hasattr(child_info.state, 'value') else str(child_info.state),
                    "parent_id": session_id
                })
                # Recursively get grandchildren
                grandchildren = await self._get_all_descendants(child_id)
                descendants.extend(grandchildren)

        return descendants

    async def get_descendants(self, session_id: str, limit: int = 50, offset: int = 0) -> dict:
        """
        Get descendant sessions of a session, paginated.

        Returns paginated dict with descendants, total, limit, offset, has_more.
        """
        all_descendants = await self._get_all_descendants(session_id)
        total = len(all_descendants)
        sliced = all_descendants[offset : offset + limit]
        return {
            "descendants": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def count_descendants(self, session_id: str) -> int:
        """
        Count total number of descendants (children, grandchildren, etc.).

        Returns count of all descendant sessions.
        """
        descendants = await self._get_all_descendants(session_id)
        return len(descendants)

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

    async def _build_effective_config_payload(
        self, session_info
    ) -> tuple[dict | None, dict | None]:
        """Return (effective_config_dict, template_meta) for template-linked sessions.

        Returns (None, None) for standalone sessions or on resolution failure.
        """
        if not session_info.template_id:
            return None, None
        try:
            eff = await resolve_effective_config(
                session_info, self.template_manager, self.profile_manager
            )
            effective_config_dict = eff.model_dump()
            tmpl = await self.template_manager.get_template(session_info.template_id)
            template_meta = None
            if tmpl:
                template_meta = {
                    "template_id": tmpl.template_id,
                    "name": tmpl.name,
                    "profile_ids": tmpl.profile_ids or {},
                }
            return effective_config_dict, template_meta
        except Exception:
            logger.warning(
                f"Failed to build effective_config for session {session_info.session_id}"
            )
            return None, None

    async def get_session_info(self, session_id: str) -> dict[str, Any] | None:
        """Get comprehensive session information"""
        try:
            # Get session info from manager
            session_info = await self.session_manager.get_session_info(session_id)
            if not session_info:
                return None

            # Get SDK status
            sdk_info = {}
            sdk = self._active_sdks.get(session_id)
            if sdk:
                sdk_info = sdk.get_info()
                sdk_info.update({
                    "queue_size": sdk.get_queue_size(),
                })

            # Get storage stats
            storage_info = {}
            storage = self._storage_managers.get(session_id)
            if storage:
                storage_info = {
                    "message_count": await storage.get_message_count()
                }

            # Augment with SDK session metadata if claude_code_session_id is available
            sdk_session_info = None
            if session_info.claude_code_session_id:
                try:
                    from claude_agent_sdk import get_session_info as sdk_get_session_info
                    result = await asyncio.to_thread(
                        sdk_get_session_info,
                        session_info.claude_code_session_id,
                        session_info.working_directory,
                    )
                    if result:
                        sdk_session_info = {
                            "summary": result.summary,
                            "custom_title": result.custom_title,
                            "git_branch": result.git_branch,
                            "first_prompt": result.first_prompt,
                            "tag": result.tag,
                            "created_at": str(result.created_at) if result.created_at else None,
                            "last_modified": str(result.last_modified) if result.last_modified else None,
                        }
                except Exception:
                    logger.warning(f"SDK get_session_info failed for {session_id}")

            effective_config_dict, template_meta = await self._build_effective_config_payload(
                session_info
            )

            return {
                "session": session_info.to_dict(),
                "sdk": sdk_info,
                "storage": storage_info,
                "sdk_session_info": sdk_session_info,
                "effective_config": effective_config_dict,
                "template": template_meta,
            }

        except Exception:
            logger.exception(f"Failed to get session info for {session_id}")
            return None

    async def list_sessions(self, limit: int = 500, offset: int = 0) -> dict[str, Any]:
        """List all sessions with their current status, paginated."""
        try:
            sessions = await self.session_manager.list_sessions()
            all_sessions = []
            for session_info in sessions:
                entry = session_info.to_dict()
                if session_info.template_id:
                    eff, _tmpl = await self._build_effective_config_payload(session_info)
                    entry["effective_config"] = eff
                all_sessions.append(entry)
            total = len(all_sessions)
            sliced = all_sessions[offset : offset + limit]
            return {
                "sessions": sliced,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(sliced) < total,
            }
        except Exception:
            logger.exception("Failed to list sessions")
            return {"sessions": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}

    @staticmethod
    def _extract_agent_name(description: str | None) -> str:
        """Extract agent name from task description (e.g., 'alpha: You are...' -> 'alpha')."""
        if not description:
            return ""
        colon_idx = description.find(":")
        if 0 < colon_idx < 50:
            return description[:colon_idx].strip()
        return description[:40] + "..." if len(description) > 40 else description

    # Issue #1676: CLI phrasing for background subagent Notification hook messages.
    _AGENT_NOTIFICATION_MESSAGE_SUFFIXES = (
        " needs your input",
        " finished",
        " failed",
    )

    @staticmethod
    def _parse_agent_notification_label(message: str | None) -> str | None:
        """Best-effort label parse from a Notification hook's free-text message (issue #1676).

        Isolated here as a single edit point if the CLI's message phrasing changes.
        """
        if not message:
            return None
        for suffix in SessionCoordinator._AGENT_NOTIFICATION_MESSAGE_SUFFIXES:
            if message.endswith(suffix):
                return message[: -len(suffix)]
        return None


    # ==================== TASK LEG REGISTRY (Issue #1746) ====================

    async def _get_task_leg_registry(self, session_id: str) -> TaskLegRegistry:
        """Get (lazily hydrating) the per-session TaskLegRegistry.

        Hydration replays every stored Task lifecycle message once — so a
        page refresh and a live-streamed session converge on identical
        state. Only cached once a storage manager is available; if the
        session hasn't been started yet in this process, an ephemeral empty
        registry is returned so a later call (once storage exists) can
        still hydrate properly.
        """
        registry = self._task_leg_registries.get(session_id)
        if registry is not None:
            return registry

        registry = TaskLegRegistry()
        storage = self._storage_managers.get(session_id)
        if storage:
            raw_messages = await storage.read_messages()
            for raw_message in raw_messages:
                if raw_message.get("type") != "system":
                    continue
                metadata = raw_message.get("metadata") or {}
                subtype = metadata.get("subtype")
                if subtype:
                    registry.apply_frame(subtype, metadata, raw_message.get("timestamp"))
            self._task_leg_registries[session_id] = registry

        return registry

    async def get_background_agents(self, session_id: str) -> dict[str, Any]:
        """Snapshot of background-agent (Task) leg history for reload/reconnect."""
        registry = await self._get_task_leg_registry(session_id)
        return {"session_id": session_id, "agents": registry.snapshot()}

    # ==================== ARCHIVE METHODS ====================

    async def get_archives(self, session_id: str, limit: int = 50, offset: int = 0) -> dict:
        """List archives for a session, paginated."""
        if not self.legion_system:
            return {"archives": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}
        all_archives = await self.legion_system.archive_manager.get_archives(session_id)
        total = len(all_archives)
        sliced = all_archives[offset : offset + limit]
        return {
            "archives": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def get_archive_messages(
        self, session_id: str, archive_id: str, offset: int = 0, limit: int | None = 50
    ) -> dict:
        """Read paginated messages from an archive, converted to websocket format."""
        if not self.legion_system:
            return {"messages": [], "total_count": 0, "offset": offset, "has_more": False}
        result = await self.legion_system.archive_manager.get_archive_messages(
            session_id, archive_id, offset=offset, limit=limit
        )

        result["messages"] = [msg for msg in result.get("messages", []) if msg]
        return result

    async def get_archive_state(self, session_id: str, archive_id: str) -> dict | None:
        """Read state and metadata from an archive."""
        if not self.legion_system:
            return None
        return await self.legion_system.archive_manager.get_archive_state(session_id, archive_id)

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
        """List resource metadata from an archive, paginated with optional filter/sort."""
        if not self.legion_system:
            return {"resources": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}
        all_resources = await self.legion_system.archive_manager.get_archive_resources(
            session_id, archive_id
        )

        all_resources = _group_resources_by_filename(all_resources)
        all_resources = _apply_resource_filters(all_resources, search, format_filter, sort)

        total = len(all_resources)
        sliced = all_resources[offset : offset + limit]
        return {
            "resources": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def get_archive_resource_by_id(
        self, session_id: str, archive_id: str, resource_id: str
    ) -> dict | None:
        """Look up a single archived resource's metadata by ID against the raw (ungrouped) log.

        Issue #1680: mirrors get_session_resource_by_id() for archive byte-serving.
        """
        if not self.legion_system:
            return None
        all_resources = await self.legion_system.archive_manager.get_archive_resources(
            session_id, archive_id
        )
        return next((r for r in all_resources if r.get("resource_id") == resource_id), None)

    async def get_archive_resource_file(
        self, session_id: str, archive_id: str, resource_id: str
    ) -> bytes | None:
        """Get raw file bytes for a resource in an archive."""
        if not self.legion_system:
            return None
        return await self.legion_system.archive_manager.get_archive_resource_file(
            session_id, archive_id, resource_id
        )

    async def list_project_deleted_agents(self, project_id: str, limit: int = 50, offset: int = 0) -> dict:
        """List deleted agents with archives for a project, paginated."""
        if not self.legion_system:
            return {"agents": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}
        all_agents = await self.legion_system.archive_manager.list_project_deleted_agents(project_id)
        total = len(all_agents)
        sliced = all_agents[offset : offset + limit]
        return {
            "agents": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def erase_history(self, session_id: str) -> bool:
        """Erase distilled history for a session."""
        if not self.legion_system:
            return False
        return await self.legion_system.archive_manager.erase_history(session_id)

    async def erase_archives(self, session_id: str) -> bool:
        """Erase archives for a session."""
        if not self.legion_system:
            return False
        return await self.legion_system.archive_manager.erase_archives(session_id)

    async def check_history_archives(self, session_id: str) -> dict:
        """Check existence of history and archives for a session."""
        if not self.legion_system:
            return {"has_history": False, "has_archives": False}
        return await self.legion_system.archive_manager.check_history_archives_exist(session_id)

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

    async def _on_rate_limits(self, rate_limit_info: Any) -> None:
        """Issue #899: Normalize a RateLimitInfo and broadcast updated state to UI poll queue."""
        try:
            window = getattr(rate_limit_info, 'rate_limit_type', None)
            utilization = getattr(rate_limit_info, 'utilization', None)
            resets_at_ts = getattr(rate_limit_info, 'resets_at', None)
            status = getattr(rate_limit_info, 'status', None)

            if window is None:
                return

            entry: dict = {}
            if utilization is not None:
                entry['used_percentage'] = round(utilization * 100, 1)
            if resets_at_ts is not None:
                entry['resets_at'] = datetime.fromtimestamp(resets_at_ts, tz=UTC).isoformat()
            # Issue #1572: surfaced so the auto-resume detection hook in
            # _create_error_callback can tell "account-wide limit hit" (status ==
            # "rejected") apart from the merely-approaching-limit warning state.
            if status is not None:
                entry['status'] = status

            self._rate_limits_state[window] = entry

            if self._rate_limit_broadcast_callback:
                self._rate_limit_broadcast_callback(dict(self._rate_limits_state))
        except Exception:
            logger.exception("Error processing rate_limits")

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

    # ============================================================
    # Issue #494: ToolCallUpdate Storage
    # ============================================================

    def _schedule_tool_call_update_storage(
        self,
        session_id: str,
        tool_call: ToolCall,
        triggering_message: dict[str, Any] | None = None,
    ) -> None:
        """
        Snapshot and schedule storage of a ToolCallUpdate entry (Issue #494).

        The MessageRecord is built eagerly (synchronously) to capture the
        current tool_call state before the object is mutated by subsequent
        lifecycle transitions.  The actual I/O is deferred via ensure_future.
        """
        storage = self._storage_managers.get(session_id)
        if not storage:
            coord_logger.warning(
                f"No storage manager for session {session_id}, skipping ToolCallUpdate"
            )
            return
        try:
            stored_dict = MessageRecord.from_tool_call(
                tool_call, triggering_message
            ).to_dict()
        except Exception:
            coord_logger.exception(
                f"Failed to build ToolCallUpdate for {tool_call.tool_use_id}"
            )
            return
        task = asyncio.ensure_future(
            self._write_tool_call_update(storage, stored_dict, tool_call.tool_use_id)
        )
        task.add_done_callback(task_done_log_exception)

    async def _write_tool_call_update(
        self,
        storage,
        stored_dict: dict[str, Any],
        tool_use_id: str,
    ) -> None:
        """Write a pre-built ToolCallUpdate dict to storage (Issue #494)."""
        try:
            await storage.append_message(stored_dict)
        except Exception:
            coord_logger.exception(
                f"Failed to store ToolCallUpdate for {tool_use_id}"
            )

    # ============================================================
    # Issue #707: Auto-Approval Callback
    # ============================================================

    def _create_auto_approval_callback(self, session_id: str) -> Callable:
        """
        Create a callback that can_use_tool calls when it auto-approves a tool.

        The ToolCall already exists (created from the AssistantMessage tool_use block
        before can_use_tool fires), so we find it by tool_use_id via the existing O(1)
        get_tool_call_by_id() lookup, set the reason, store a new ToolCallUpdate, and
        broadcast via the existing tool_call broadcast mechanism.
        """
        def callback(
            tool_name: str, input_params: dict[str, Any], reason: str, tool_use_id: str | None
        ) -> None:
            # Auto-approval fires before any permission request exists (it short-circuits
            # _can_use_tool_callback before the branch that mints a request_id), so there is
            # no request_id to fall back to here — tool_use_id is the only linkage (#2126).
            tool_call = self.get_tool_call_by_id(session_id, tool_use_id) if tool_use_id else None
            if (
                tool_call is None
                or tool_call.auto_approved_reason is not None
                or tool_call.status != ToolState.PENDING
            ):
                return
            tool_call.auto_approved_reason = reason
            coord_logger.info(
                f"[707] Auto-approved {tool_call.tool_use_id} ({tool_name}): {reason}"
            )
            # Store a new ToolCallUpdate with the reason attached
            self._schedule_tool_call_update_storage(session_id, tool_call)
            # Broadcast via existing tool_call broadcast mechanism
            tool_call_data = MessageRecord.from_tool_call(tool_call).to_dict()
            for cb in self._tool_call_broadcast_callbacks:
                try:
                    cb(session_id, tool_call_data)
                except Exception:
                    coord_logger.exception("Error in auto-approval broadcast callback")
        return callback

    # ============================================================
    # Issue #324: Unified ToolCall Lifecycle Management
    # ============================================================

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
        """
        Create a new ToolCall when tool_use is detected (Issue #324).

        Called when an AssistantMessage contains a tool_use block.
        Returns the created ToolCall for WebSocket broadcast.
        """
        import time

        tool_call = ToolCall(
            tool_use_id=tool_use_id,
            session_id=session_id,
            name=name,
            input=input_params,
            status=ToolState.PENDING,
            created_at=time.time(),
            requires_permission=requires_permission,
            parent_tool_use_id=parent_tool_use_id,
            turn_id=turn_id,
            display=ToolDisplayInfo(
                state=ToolState.PENDING,
                visible=True,
                collapsed=False,
                style="default",
            ),
        )

        # Track in active tool calls
        if session_id not in self._active_tool_calls:
            self._active_tool_calls[session_id] = {}
        self._active_tool_calls[session_id][tool_use_id] = tool_call

        # Issue #858: Notify any permission callbacks waiting for this tool call.
        if session_id in self._tool_call_events:
            self._tool_call_events[session_id].set()

        coord_logger.debug(
            f"Created ToolCall {tool_use_id} for {name} in session {session_id}"
        )

        # Issue #494: Store PENDING ToolCallUpdate
        self._schedule_tool_call_update_storage(session_id, tool_call)

        return tool_call

    def update_tool_call_permission_request(
        self,
        session_id: str,
        tool_use_id: str,
        permission_info: PermissionInfo,
        request_id: str | None = None,
        triggering_message: dict[str, Any] | None = None,
    ) -> ToolCall | None:
        """
        Update ToolCall to awaiting_permission status (Issue #324).

        Called when a permission request is received for a tool.
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            coord_logger.warning(
                f"ToolCall {tool_use_id} not found for permission request in session {session_id}"
            )
            return None

        tool_call.status = ToolState.AWAITING_PERMISSION
        tool_call.requires_permission = True
        tool_call.permission = permission_info
        tool_call.request_id = request_id
        if tool_call.display:
            tool_call.display.state = ToolState.AWAITING_PERMISSION
            tool_call.display.style = "warning"

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} to awaiting_permission in session {session_id}"
        )

        # Issue #494: Store AWAITING_PERMISSION ToolCallUpdate
        self._schedule_tool_call_update_storage(session_id, tool_call, triggering_message)

        return tool_call

    def update_tool_call_permission_response(
        self,
        session_id: str,
        tool_use_id: str,
        granted: bool,
        triggering_message: dict[str, Any] | None = None,
        applied_updates: list[dict[str, Any]] | None = None,
    ) -> ToolCall | None:
        """
        Update ToolCall after permission response (Issue #324).

        Called when user grants or denies permission.
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        import time

        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            coord_logger.warning(
                f"ToolCall {tool_use_id} not found for permission response in session {session_id}"
            )
            return None

        tool_call.permission_granted = granted
        tool_call.permission_response_at = time.time()
        if applied_updates:
            tool_call.applied_updates = applied_updates

        if granted:
            tool_call.status = ToolState.RUNNING
            tool_call.started_at = time.time()
            if tool_call.display:
                tool_call.display.state = ToolState.RUNNING
                tool_call.display.style = "default"
        else:
            tool_call.status = ToolState.DENIED
            tool_call.completed_at = time.time()
            if tool_call.display:
                tool_call.display.state = ToolState.DENIED
                tool_call.display.style = "error"
            # Remove from active (denied tools are terminal)
            self._remove_active_tool_call(session_id, tool_use_id)

            # Issue #1131: Record DENIED outcome for error-rate watchdog
            if self._watchdog is not None:
                self._watchdog.record_tool_outcome(session_id, tool_use_id, "denied")

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} permission_granted={granted} in session {session_id}"
        )

        # Issue #494: Store RUNNING or DENIED ToolCallUpdate
        self._schedule_tool_call_update_storage(session_id, tool_call, triggering_message)

        return tool_call

    def update_tool_call_running(
        self,
        session_id: str,
        tool_use_id: str,
    ) -> ToolCall | None:
        """
        Update ToolCall to running status (Issue #324).

        Called when a tool starts executing (for tools that don't require permission).
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        import time

        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            # Tool may not exist yet if tool_use and execution happen quickly
            coord_logger.debug(
                f"ToolCall {tool_use_id} not found for running update in session {session_id}"
            )
            return None

        tool_call.status = ToolState.RUNNING
        tool_call.started_at = time.time()
        if tool_call.display:
            tool_call.display.state = ToolState.RUNNING
            tool_call.display.style = "default"

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} to running in session {session_id}"
        )

        # Issue #494: Store RUNNING ToolCallUpdate
        self._schedule_tool_call_update_storage(session_id, tool_call)

        return tool_call

    def update_tool_call_result(
        self,
        session_id: str,
        tool_use_id: str,
        result: Any,
        is_error: bool = False,
        triggering_message: dict[str, Any] | None = None,
        sender_attachments: list[dict] | None = None,
    ) -> ToolCall | None:
        """
        Update ToolCall with result (Issue #324).

        Called when a tool_result is received.
        Returns updated ToolCall for WebSocket broadcast, or None if not found.
        """
        import time

        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            coord_logger.warning(
                f"ToolCall {tool_use_id} not found for result in session {session_id}"
            )
            return None

        tool_call.completed_at = time.time()
        tool_call.result = result

        if is_error:
            tool_call.status = ToolState.FAILED
            tool_call.error = str(result) if result else "Tool execution failed"
            if tool_call.display:
                tool_call.display.state = ToolState.FAILED
                tool_call.display.style = "error"
        else:
            tool_call.status = ToolState.COMPLETED
            if tool_call.display:
                tool_call.display.state = ToolState.COMPLETED
                tool_call.display.style = "success"

        # Issue #1593: Attach sender attachment resource IDs for send_comm chips
        if sender_attachments is not None:
            tool_call.sender_attachments = sender_attachments

        # Remove from active (completed/failed tools are terminal)
        self._remove_active_tool_call(session_id, tool_use_id)

        coord_logger.debug(
            f"Updated ToolCall {tool_use_id} to {'failed' if is_error else 'completed'} in session {session_id}"
        )

        # Issue #494: Store COMPLETED or FAILED ToolCallUpdate
        self._schedule_tool_call_update_storage(session_id, tool_call, triggering_message)

        # Issue #1131: Record terminal tool outcome for error-rate watchdog
        if self._watchdog is not None:
            outcome = "failed" if is_error else "completed"
            self._watchdog.record_tool_outcome(session_id, tool_use_id, outcome)

        return tool_call

    def update_tool_call_input(
        self,
        session_id: str,
        tool_use_id: str,
        input_params: dict[str, Any],
    ) -> ToolCall | None:
        """
        Fill in the real input on a ToolCall created early, at content_block_start
        (Issue #2109, AC2). Returns the updated ToolCall, or None if no pending
        record exists yet for this tool_use_id (the non-streaming/fallback caller
        should create one fresh instead).
        """
        tool_call = self._get_active_tool_call(session_id, tool_use_id)
        if not tool_call:
            return None

        tool_call.input = input_params

        # Issue #858: the legacy signature-matching permission fallback hashes
        # tool_call.input — it just changed from {} to the real input, so any
        # waiter blocked on this event needs to re-check now, exactly like
        # create_tool_call() already does for a brand-new ToolCall.
        if session_id in self._tool_call_events:
            self._tool_call_events[session_id].set()

        coord_logger.debug(
            f"Filled in input for ToolCall {tool_use_id} in session {session_id}"
        )

        self._schedule_tool_call_update_storage(session_id, tool_call)

        return tool_call

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
        """
        Mark all active tools for a session as interrupted (Issue #324).

        Called when session is terminated or interrupted.
        Stores INTERRUPTED ToolCallUpdate entries and broadcasts via WebSocket.
        Returns list of interrupted ToolCalls.
        """
        import time

        interrupted = []
        session_tools = self._active_tool_calls.get(session_id, {})

        for _tool_use_id, tool_call in list(session_tools.items()):
            tool_call.status = ToolState.INTERRUPTED
            tool_call.completed_at = time.time()
            if tool_call.display:
                tool_call.display.state = ToolState.INTERRUPTED
                tool_call.display.style = "orphaned"
            interrupted.append(tool_call)

            # Issue #494: Store INTERRUPTED ToolCallUpdate for each tool
            self._schedule_tool_call_update_storage(session_id, tool_call)

        # Clear active tools for session
        if session_id in self._active_tool_calls:
            self._active_tool_calls[session_id].clear()

        if interrupted:
            coord_logger.info(
                f"Marked {len(interrupted)} tools as interrupted for session {session_id}"
            )
            # Issue #520: Broadcast interrupted tool_call messages via WebSocket
            for tool_call in interrupted:
                tool_call_data = MessageRecord.from_tool_call(tool_call).to_dict()
                for cb in self._tool_call_broadcast_callbacks:
                    try:
                        cb(session_id, tool_call_data)
                    except Exception:
                        logger.exception("Error in tool_call broadcast callback")

        return interrupted

    def get_tool_call_event(self, session_id: str) -> asyncio.Event:
        """Return (creating if necessary) the per-session tool-call notification event."""
        if session_id not in self._tool_call_events:
            self._tool_call_events[session_id] = asyncio.Event()
        return self._tool_call_events[session_id]

    def mark_assistant_message_emitted(self, session_id: str, turn_id: str) -> None:
        """Issue #1694/#1958: Record that the assistant message envelope for turn_id has
        been appended to the session queue, and wake any permission callbacks waiting
        on the barrier for it."""
        if session_id not in self._emitted_turn_ids:
            self._emitted_turn_ids[session_id] = set()
        self._emitted_turn_ids[session_id].add(turn_id)
        if session_id in self._message_emitted_events:
            self._message_emitted_events[session_id].set()

    def is_assistant_message_emitted(self, session_id: str, turn_id: str) -> bool:
        """Issue #1694/#1958: Check whether the assistant message envelope for turn_id has
        already been appended to the session queue."""
        return turn_id in self._emitted_turn_ids.get(session_id, set())

    def get_message_emitted_event(self, session_id: str) -> asyncio.Event:
        """Return (creating if necessary) the per-session message-emitted notification event."""
        if session_id not in self._message_emitted_events:
            self._message_emitted_events[session_id] = asyncio.Event()
        return self._message_emitted_events[session_id]

    def get_tool_call_by_id(self, session_id: str, tool_use_id: str) -> ToolCall | None:
        """O(1) lookup of an active tool call by its tool_use_id (Issue #953)."""
        return self._active_tool_calls.get(session_id, {}).get(tool_use_id)

    def _get_active_tool_call(self, session_id: str, tool_use_id: str) -> ToolCall | None:
        """Get a specific active tool call."""
        session_tools = self._active_tool_calls.get(session_id, {})
        return session_tools.get(tool_use_id)

    def apply_tool_call_snapshot(self, session_id: str, tool_call: ToolCall) -> ToolCall:
        """Issue #2109 (AC11): replay-only — installs an already-complete ToolCall
        snapshot (e.g. a regenerated fixture's reconstructed pending/awaiting_permission/
        running|denied record) as the session's tracked state for that tool_use_id
        verbatim, rather than deriving a transition from partial fields. Used by
        BackendApp._emit_tool_call_updates()'s flat `tool_call` replay-mirror branch.

        A terminal snapshot (denied/completed/failed/interrupted) is removed from
        active tracking and recorded on the watchdog — mirroring every other
        terminal-state transition method (update_tool_call_permission_response's
        deny path, update_tool_call_result's completed/failed path) — so it can't
        linger in _active_tool_calls and later get silently clobbered back to
        INTERRUPTED by a mark_session_tools_interrupted() bulk sweep.
        """
        if session_id not in self._active_tool_calls:
            self._active_tool_calls[session_id] = {}
        self._active_tool_calls[session_id][tool_call.tool_use_id] = tool_call

        if tool_call.status in (
            ToolState.DENIED, ToolState.COMPLETED, ToolState.FAILED, ToolState.INTERRUPTED,
        ):
            self._remove_active_tool_call(session_id, tool_call.tool_use_id)
            if tool_call.status != ToolState.INTERRUPTED and self._watchdog is not None:
                self._watchdog.record_tool_outcome(session_id, tool_call.tool_use_id, tool_call.status.value)

        return tool_call

    def _remove_active_tool_call(self, session_id: str, tool_use_id: str) -> None:
        """Remove a tool call from active tracking."""
        session_tools = self._active_tool_calls.get(session_id, {})
        if tool_use_id in session_tools:
            del session_tools[tool_use_id]

    def get_tool_call_by_request_id(self, session_id: str, request_id: str) -> ToolCall | None:
        """Lookup of an active tool call awaiting permission by its request_id
        (Issue #2109, AC6). Scoped to AWAITING_PERMISSION so a duplicate/replayed
        permission_response sharing a stale request_id can't re-resolve a tool call
        that already moved on to running/denied — request_id is never cleared off
        the ToolCall once set, so an unscoped match would still find it."""
        for tool_call in self._active_tool_calls.get(session_id, {}).values():
            if tool_call.request_id == request_id and tool_call.status == ToolState.AWAITING_PERMISSION:
                return tool_call
        return None

    async def _store_processed_message(self, session_id: str, message_data: dict[str, Any]):
        """Store a synthetic system/lifecycle message via the canonical MessageRecord
        bridge for plain dicts (Issue #2084 stage 3-B, §3).

        Stamps `message_data` in place to become the exact canonical record dict
        written to storage, so lifecycle senders' live-callback dict and stored record
        always share one identity and one shape (issue #1972, issue #2084 AC2) — never
        two independently-minted/derived copies of the same message.
        """
        try:
            record = _message_dict_to_record(message_data, session_id, display=None)
            record_dict = record.to_dict()
        except Exception:
            # Issue #2109 (AC11): a write-time path (synthetic system/lifecycle
            # messages) — fail loudly rather than falling back to storing the
            # original, uncanonicalized dict. A canonicalization failure here is a
            # genuine bug that should surface immediately, not silently write a
            # non-canonical record to disk that AC10's read-time guard would later
            # choke on.
            logger.exception(f"Failed to canonicalize processed message for session {session_id}")
            raise

        # Issue #2084 AC2: stamp message_data in place to become the canonical dict
        # BEFORE attempting storage, so every caller's subsequent `callback(message_data)`
        # call forwards the identical canonical shape downstream even if the storage
        # write below fails — a storage hiccup must not silently revert the live
        # envelope back to the pre-canonical shape.
        message_data.clear()
        message_data.update(record_dict)

        storage = self._storage_managers.get(session_id)
        if storage:
            try:
                await storage.append_message(record_dict)
            except Exception:
                logger.exception(f"Failed to store processed message for session {session_id}")
        else:
            logger.warning(f"No storage manager found for session {session_id}")

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

    def _create_message_callback(self, session_id: str) -> Callable:
        """Create message callback for a session using unified MessageProcessor"""
        async def callback(message_data: dict[str, Any]):
            try:
                # Issue #1486: assistant_delta bypasses parser, projection, and activity tracking
                # Issue #2132: thinking_tokens_delta is the same shape of live-only progress
                # frame — bypasses the parser for the same reason (no MessageRecord was ever
                # built for it, so MessageProcessor.process_message() would just misclassify
                # it as an unknown type and log a warning on every ~10/minute occurrence).
                if isinstance(message_data, dict) and message_data.get("type") in (
                    "assistant_delta", "thinking_tokens_delta",
                ):
                    for cb in self._message_callbacks.get(session_id, []):
                        try:
                            await cb(session_id, message_data)
                        except Exception:
                            coord_logger.exception(
                                f"Error forwarding {message_data.get('type')} to subscriber"
                            )
                    return

                # Issue #1130: Track activity for idle watchdog
                self.session_manager.record_activity(session_id)

                # Process message using unified MessageProcessor
                parsed_message = self.message_processor.process_message(message_data, source="sdk")

                # Track latest meaningful message (issue #291, issue #1497)
                # Only track user and assistant messages — system messages are SDK runtime
                # events (e.g. "Claude Code Launched"), not conversation content.
                if parsed_message.type.value in ['user', 'assistant']:
                    content = parsed_message.content or ""

                    # Skip messages that are just internal SDK placeholders or tool execution artifacts
                    has_tool_results = parsed_message.metadata.get('has_tool_results', False) if parsed_message.metadata else False
                    has_tool_uses = parsed_message.metadata.get('has_tool_uses', False) if parsed_message.metadata else False

                    skip_message = (
                        # User messages with only tool_results (no actual user text)
                        (parsed_message.type.value == 'user' and has_tool_results and content.startswith('Tool results:')) or
                        # Assistant messages with only tool_uses (no actual text response)
                        (parsed_message.type.value == 'assistant' and has_tool_uses and content == 'Assistant response')
                    )

                    if not skip_message:
                        # Truncate to 200 chars (stored), will be truncated further in frontend (100 chars displayed)
                        truncated_content = content[:200] if len(content) > 200 else content

                        # Replace newlines with spaces for single-line display
                        truncated_content = truncated_content.replace('\n', ' ').strip()

                        # Update session's latest message tracking
                        if truncated_content:  # Only update if there's actual content
                            await self.session_manager.update_latest_message(
                                session_id,
                                truncated_content,
                                parsed_message.type.value,
                                datetime.now(UTC)
                            )

                # Issue #1027: Detect SDK status events reporting permission mode changes.
                # The SDK emits SystemMessage(subtype="status") with permissionMode when a mode
                # transition occurs (ExitPlanMode, setMode suggestion applied, user-initiated).
                # This is the authoritative source of truth for permission mode state.
                if parsed_message.type.value == 'system' and parsed_message.metadata:
                    if parsed_message.metadata.get('subtype') == 'permission_mode_change':
                        new_mode = parsed_message.metadata.get('permission_mode')
                        if new_mode:
                            try:
                                await self.session_manager.update_permission_mode(
                                    session_id, new_mode, template_manager=self.template_manager
                                )
                                coord_logger.info(f"Permission mode updated to '{new_mode}' for session {session_id} via SDK status event")
                            except Exception:
                                logger.exception(f"Failed to update permission mode from SDK status event for session {session_id}")

                    # Issue #1746: Feed Task lifecycle frames into the per-session
                    # TaskLegRegistry — a task_id-keyed source of truth for
                    # background-agent status, independent of the Agent tool
                    # call's own dispatch-ack status.
                    task_subtype = parsed_message.metadata.get('subtype')
                    if task_subtype in TASK_LIFECYCLE_SUBTYPES:
                        try:
                            # Storage always persists this message before this
                            # callback fires (see ClaudeSDK._process_sdk_message),
                            # so a *cold* hydration below already replays this
                            # exact frame from messages.jsonl. Only apply it here
                            # when the registry was already warm — otherwise this
                            # frame would be double-counted (e.g. two legs for one
                            # task_started).
                            registry_already_cached = session_id in self._task_leg_registries
                            registry = await self._get_task_leg_registry(session_id)
                            if registry_already_cached:
                                registry.apply_frame(task_subtype, parsed_message.metadata, parsed_message.timestamp)
                        except Exception:
                            logger.exception(f"Failed to update task leg registry for session {session_id}")

                # Track permission responses with applied updates (for historical/display purposes)
                if parsed_message.type.value == 'permission_response' and parsed_message.metadata:
                    applied_updates = parsed_message.metadata.get('applied_updates', [])

                    if applied_updates:
                        # Track the applied updates for display/history
                        if session_id not in self._permission_updates:
                            self._permission_updates[session_id] = []
                        self._permission_updates[session_id].extend(applied_updates)
                        coord_logger.debug(f"Tracked {len(applied_updates)} applied permission updates for session {session_id}")

                # Log turn-level errors from ResultMessage.errors
                if parsed_message.type.value == 'result' and parsed_message.metadata:
                    errors = parsed_message.metadata.get('errors')
                    if errors:
                        coord_logger.warning(
                            f"Turn completed with {len(errors)} error(s) for session {session_id}: {errors}"
                        )

                    # Issue #2190 (AC1): the real production usage-limit signal is an
                    # ordinary ResultMessage (no exception raised, no ERROR transition)
                    # with is_error + api_error_status == 429 — _create_error_callback's
                    # critical-error branch (_maybe_schedule_auto_resume) never sees this,
                    # since nothing failed from the SDK's own perspective. Reads from
                    # message_data's own nested metadata (via _nested_metadata), not
                    # parsed_message.metadata — see that helper's docstring.
                    #
                    # Pop the synthetic-text stash unconditionally here, regardless of
                    # whether this particular result turns out to be the 429 case — a
                    # stashed synthetic notice is only ever paired with the ONE result
                    # that immediately follows it; leaving it cached past a non-matching
                    # or non-error result would let a later, unrelated detection for this
                    # session consult a stale reset-time text.
                    _synthetic_entry = self._last_synthetic_text_by_session.pop(session_id, None)
                    _result_meta = _nested_metadata(message_data, parsed_message)
                    if _result_meta.get('is_error'):
                        if _result_meta.get('api_error_status') == 429:
                            _result_ts = parsed_message.timestamp
                            if isinstance(_result_ts, str):
                                _result_ts = datetime.fromisoformat(_result_ts).timestamp()
                            elif not isinstance(_result_ts, (int, float)):
                                _result_ts = datetime.now(UTC).timestamp()
                            await self._handle_rate_limited_result(
                                session_id, _result_ts, _synthetic_entry
                            )
                        else:
                            # Issue #2190 (AC3): WARNING so an unrecognized is_error shape
                            # (the primary detection risk) is visible even with INFO off.
                            coord_logger.warning(
                                f"Result for session {session_id} flagged is_error but is "
                                f"not a recognized rate-limit signal — "
                                f"api_error_status={_result_meta.get('api_error_status')}, "
                                f"subtype={_result_meta.get('subtype')}"
                            )

                # Issue #1029: Reactive is_processing tracking based on message type.
                # Instead of counting pending ResultMessages (which breaks when the SDK
                # folds injected messages into the active turn), we react to message types:
                # - 'result' → turn done, set is_processing = False
                # - 'assistant' → SDK actively responding, set is_processing = True
                # This is self-correcting: if a result briefly sets False, the next
                # assistant message from a folded query immediately re-sets True.

                if parsed_message.type.value == 'result':
                    try:
                        await self.session_manager.end_turn(session_id)
                    except Exception:
                        logger.exception(f"Failed to reset processing state for session {session_id}")

                    # Issue #1513: Track unread state — a result message is new work the user hasn't seen.
                    try:
                        _ts = parsed_message.timestamp
                        if isinstance(_ts, str):
                            _completion_ts = datetime.fromisoformat(_ts)
                        elif isinstance(_ts, (int, float)):
                            _completion_ts = datetime.fromtimestamp(_ts, tz=UTC)
                        else:
                            _completion_ts = datetime.now(UTC)
                        await self.session_manager.mark_completion(session_id, _completion_ts)
                    except Exception:
                        logger.exception(f"Failed to mark completion for session {session_id}")

                    # Issue #1125: Record turn usage in analytics DB and broadcast update
                    if self.analytics_store:
                        try:
                            meta = parsed_message.metadata or {}
                            usage, model_usage_cost = _normalize_result_usage(
                                meta.get("usage"), meta.get("model_usage")
                            )
                            sdk_cost = meta.get("total_cost_usd")
                            if sdk_cost is None:
                                sdk_cost = model_usage_cost
                            # Resolve model via effective config (supports template-linked sessions)
                            # Issue #1929: pop unconditionally so the cache never leaks into
                            # the next turn, regardless of which branch resolves the label.
                            _sdk_model = self._last_turn_model_by_session.pop(session_id, None)
                            _sinfo = await self.session_manager.get_session_info(session_id)
                            if _sinfo:
                                _eff = await resolve_effective_config(
                                    _sinfo, self.template_manager, self.profile_manager
                                )
                                _model = _resolve_analytics_model_label(_eff, _sdk_model)
                            else:
                                _model = _sdk_model
                            turn_seq = await self._next_turn_seq(session_id)
                            # Issue #1838: usage/total_cost_usd are cumulative snapshots
                            # since subprocess start, not per-turn values — convert to
                            # true per-turn deltas before recording.
                            baseline = self._usage_baseline_by_session.get(session_id)
                            usage_delta, cost_delta, new_baseline = _delta_from_baseline(
                                usage, sdk_cost, baseline
                            )
                            # Issue #1840: fold in any subagent (Task/Agent tool) usage
                            # accumulated during this turn — structurally disjoint from
                            # the top-level ResultMessage's own usage above, so this is
                            # pure addition, never double-counted against it.
                            _sub_accum = self._subagent_usage_by_session.pop(session_id, None)
                            if _sub_accum:
                                for _k, _v in _sub_accum.items():
                                    usage_delta[_k] = usage_delta.get(_k, 0) + _v
                                # The subagent's flat cache-write total must also fold
                                # into the pre-existing cache_creation_input_tokens field
                                # that record_turn()/compute_cost() key off of first —
                                # otherwise, whenever the main thread's own turn already
                                # has a non-zero cache_creation_input_tokens, record_turn's
                                # `usage.get("cache_creation_input_tokens") or usage.get
                                # ("cache_write_tokens")` fallback would never reach the
                                # generic per-key copy above and the subagent's flat total
                                # would be silently dropped from cost estimation.
                                _sub_cache_write = _sub_accum.get("cache_write_tokens", 0)
                                if _sub_cache_write:
                                    usage_delta["cache_creation_input_tokens"] = (
                                        usage_delta.get("cache_creation_input_tokens", 0)
                                        + _sub_cache_write
                                    )
                            turn_recorded = await self.analytics_store.record_turn(
                                session_id, turn_seq, _model, usage_delta, cost_delta
                            )
                            if turn_recorded:
                                self._usage_baseline_by_session[session_id] = new_baseline
                            elif _sub_accum:
                                # Don't lose the subagent's contribution on a failed
                                # write — restore it (merged with anything that may have
                                # accumulated during the await above) so the next
                                # successful turn absorbs it instead of dropping it.
                                _pending = self._subagent_usage_by_session.get(session_id)
                                if _pending:
                                    for _k, _v in _sub_accum.items():
                                        _pending[_k] = _pending.get(_k, 0) + _v
                                else:
                                    self._subagent_usage_by_session[session_id] = _sub_accum
                            # else: leave the baseline un-advanced — the next successful
                            # write computes its delta against the stale baseline, so this
                            # turn's un-recorded usage/cost is naturally absorbed into that
                            # turn's delta rather than lost.
                            aggregate = await self.analytics_store.get_session_usage(session_id)
                            if aggregate and self._usage_broadcast_callback:
                                try:
                                    await self._usage_broadcast_callback(session_id, aggregate)
                                except Exception:
                                    logger.exception("Failed to broadcast usage_updated for %s", session_id)
                        except Exception:
                            logger.exception("Failed to record analytics for session %s", session_id)

                    # Issue #904: Poll for SDK-generated session title after each turn
                    task = asyncio.create_task(self._check_sdk_generated_name(session_id))
                    task.add_done_callback(
                        lambda t: logger.exception("SDK title check task failed") if t.exception() else None
                    )

                elif parsed_message.type.value == 'assistant':
                    try:
                        await self.session_manager.start_turn(session_id)
                    except Exception:
                        logger.exception(f"Failed to set processing state for session {session_id}")

                    # Issue #1831: Track the SDK-reported model for this in-flight turn as
                    # a fallback for when no configured override resolves a label. Skip
                    # sub-agent (Task tool) messages — they carry parent_tool_use_id and
                    # would otherwise clobber the top-level turn's model with whatever
                    # model the sub-agent happened to use.
                    _msg_meta = parsed_message.metadata or {}
                    _msg_model = _msg_meta.get('model')

                    # Issue #2190 (AC2 tier 2): a usage-limit notice arrives as a
                    # synthetic AssistantMessage (model == "<synthetic>") immediately
                    # before its ResultMessage. Stash it for the result branch's
                    # fire_at resolver — popped there, so a stale value from an
                    # earlier turn never leaks into an unrelated later result. Reads
                    # from message_data's own nested metadata (via _nested_metadata),
                    # not _msg_meta/parsed_message.metadata — see that helper's
                    # docstring for why the latter can't be trusted for a real
                    # SDK-origin message.
                    _synth_meta = _nested_metadata(message_data, parsed_message)
                    if _synth_meta.get('model') == '<synthetic>':
                        _synth_ts = parsed_message.timestamp
                        if isinstance(_synth_ts, str):
                            _synth_ts = datetime.fromisoformat(_synth_ts).timestamp()
                        elif not isinstance(_synth_ts, (int, float)):
                            _synth_ts = datetime.now(UTC).timestamp()
                        self._last_synthetic_text_by_session[session_id] = (
                            parsed_message.content or '', _synth_ts
                        )

                    if _msg_model and not _msg_meta.get('parent_tool_use_id'):
                        self._last_turn_model_by_session[session_id] = _msg_model
                    elif _msg_meta.get('parent_tool_use_id'):
                        # Issue #1840: subagent (Task/Agent tool) message — accumulate its
                        # usage instead of using it as a top-level model fallback.
                        _sub_usage = _msg_meta.get('usage')
                        if _sub_usage:
                            self._subagent_usage_by_session[session_id] = _accumulate_subagent_usage(
                                self._subagent_usage_by_session.get(session_id), _sub_usage
                            )

                # Issue #2007: interrupt_success was removed (redundant with the already-
                # stored 'interrupt' message sent by _send_interrupt_message()); its
                # is_processing reset is relocated onto this subtype instead, since
                # _send_interrupt_message() fires under the exact same condition
                # interrupt_success used to (sdk.interrupt_session() returned True).
                # Issue #2109 (AC11): _send_interrupt_message() (and every other
                # _store_processed_message()-then-callback sender in this file —
                # client_launched, mcp_server_degraded, session_failed) now catches a
                # _store_processed_message() failure separately from the callback call,
                # so this branch still runs (and is_processing still resets) even if
                # storage/canonicalization fails for that message.
                elif parsed_message.type.value == 'system' and parsed_message.metadata.get('subtype') == 'interrupt':
                    try:
                        await self.session_manager.end_turn(session_id)
                        coord_logger.info(f"Reset processing state for session {session_id} after interrupt")
                    except Exception:
                        logger.exception(f"Failed to reset processing state after interrupt for session {session_id}")

                # Issue #1628: Distill pre-compaction history on compact_boundary.
                # microcompact_boundary is explicitly skipped — it does not represent a full
                # context wipe and does not warrant a dedicated summary.
                elif parsed_message.type.value == 'system' and parsed_message.metadata.get('subtype') == 'compact_boundary':
                    await self._handle_compact_boundary(session_id, parsed_message)

                # Call registered callbacks with processed message (maintain backward compatibility)
                callbacks = self._message_callbacks.get(session_id, [])
                # logger.info(f"Processing message for session {session_id}, found {len(callbacks)} callbacks")

                # Issue #894: Inject stable retry_message_id for api_retry sequences.
                # Mutates message_data's own `metadata` dict directly — not
                # parsed_message's — since message_data (not parsed_message) is what
                # gets forwarded to subscriber callbacks below (Issue #2084 AC2).
                msg_subtype = (
                    message_data.get('metadata', {}).get('subtype')
                    if isinstance(message_data, dict) else None
                )
                if msg_subtype == 'api_retry':
                    if session_id not in self._retry_sequences:
                        self._retry_sequences[session_id] = str(uuid4())
                    if isinstance(message_data, dict):
                        message_data.setdefault('metadata', {})['retry_message_id'] = (
                            self._retry_sequences[session_id]
                        )
                else:
                    self._retry_sequences.pop(session_id, None)

                # Issue #2084 AC2: forward the canonical message_data dict itself to
                # subscriber callbacks (e.g. web_server.py's), not the ParsedMessage
                # this method derived above purely for its own internal bookkeeping
                # (task registry, analytics, latest-message tracking, ...). Letting a
                # second, independently-derived shape reach the live wire is exactly
                # the bug class AC2 exists to kill.
                for cb in callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(session_id, message_data)
                        else:
                            cb(session_id, message_data)
                    except Exception:
                        logger.exception("Error in message callback")

            except Exception:
                logger.exception(f"Error processing message callback for {session_id}")

        return callback

    def _create_phase_callback(self, session_id: str) -> Callable:
        """Create the phase callback for a session's ClaudeSDK instance (issue #2159).

        ClaudeSDK's own internal lifecycle phase ("connecting"/"connected"/
        "disconnected"/"stopped") replaced its deleted local SessionState enum; only
        "connected" drives an actual backend transition here (mark_session_active(),
        same as the direct reach-in this replaces) — "disconnected" is already handled
        richer/separately via error_callback (ERROR with a real message), and "stopped"
        via terminate_session()'s own call into sdk.terminate(). "connecting" has no
        corresponding backend transition: session_manager.start_session() already moved
        the session to STARTING before this SDK instance was even constructed.
        """
        async def callback(phase: str) -> None:
            if phase == "connected":
                await self.session_manager.mark_session_active(session_id)

        return callback

    async def _transition_to_error(
        self, session_id: str, error_message: str, **structured_error_kwargs: Any
    ) -> None:
        """Collapse the state-transition half of the ERROR path into one call
        (issue #2159) — the caller still owns its own _send_session_failure_message()
        and _active_sdks cleanup/terminate around this; those weren't identical
        across the 3 original call sites and aren't touched here.

        Replaces 3 separately-maintained copies of the same sequence
        (update_session_state(ERROR) + update_processing_state(False) +
        an explicit _notify_state_change(ERROR)) that had drifted into a real
        redundant-emission bug: update_session_state() already fires exactly one
        state_change event on its own (via session_manager's callback chain), so the
        explicit _notify_state_change() call each copy also made was a second,
        redundant emission for the same transition. Also closes a Future-leak this
        stage is explicitly about eliminating: ERROR_OCCURRED denies/resolves this
        session's own dangling pending permission Future(s) — the SDK that would
        have answered them is gone, so nothing else ever will.

        Issue #2184: now delegates to SessionManager.transition_to_error(), which
        collapses the state + processing-reset into one state_change emission itself
        (the double-emission half of the AC1/AC5 bug) — this method no longer makes
        its own redundant update_processing_state() call.
        """
        try:
            await self.session_manager.transition_to_error(
                session_id, error_message, **structured_error_kwargs
            )
            if self.permission_service is not None:
                await self.permission_service.deny_for_session(session_id)
        except Exception:
            logger.exception("Failed to update session state to ERROR")

    def _resolve_auto_resume_fire_at(
        self,
        rejected_entry: dict | None,
        synthetic_entry: tuple[str, float] | None,
        fallback_ts: float,
    ) -> tuple[float, str]:
        """Issue #2190 (AC2): shared 3-tier fire_at resolver for both the
        critical-error path (_maybe_schedule_auto_resume) and the ordinary-result
        429 path (_handle_rate_limited_result).

        Tiers, in priority order:
        1. A `rejected` _rate_limits_state entry's resets_at (issue #899).
        2. The reset time parsed out of a preceding synthetic assistant message's
           text (parse_rate_limit_reset_time) — silently falls through to tier 3 on
           any parse failure (unknown timezone, format drift, no match).
        3. A fixed delay from `fallback_ts`.

        Returns (fire_at, source) where source is one of "rate_limit_state",
        "synthetic_text", "fallback" — recorded on the schedule for diagnosis.
        """
        if rejected_entry is not None and rejected_entry.get("resets_at"):
            try:
                resets_at_ts = datetime.fromisoformat(rejected_entry["resets_at"]).timestamp()
                return resets_at_ts + AUTO_RESUME_GRACE_PERIOD_SECONDS, "rate_limit_state"
            except (ValueError, TypeError):
                pass

        if synthetic_entry is not None:
            text, text_ts = synthetic_entry
            parsed = parse_rate_limit_reset_time(text, text_ts)
            if parsed is not None:
                return parsed + AUTO_RESUME_GRACE_PERIOD_SECONDS, "synthetic_text"

        return fallback_ts + AUTO_RESUME_FALLBACK_DELAY_SECONDS, "fallback"

    async def _maybe_schedule_auto_resume(
        self, session_id: str, structured_error_kwargs: dict
    ) -> None:
        """Issue #1572: schedule a one-time auto-resume if this ERROR transition looks
        like a usage/rate limit, not some other critical failure.

        Gated on either the ResultError's own `api_error_status == 429`, or the
        account-wide `_rate_limits_state` (issue #899) currently holding a `rejected`
        entry — the latter catches limit-shaped errors that don't surface a structured
        429 (e.g. a non-ResultError failure that happens to coincide with an
        already-known account-wide rejection). Non-matching ERROR transitions are
        logged with their structured fields so the heuristic can be tightened once
        real-world CLI failure shapes are observed (explicitly flagged as the primary
        detection risk for this feature).
        """
        if self.legion_system is None:
            return

        api_error_status = structured_error_kwargs.get("error_api_error_status")
        rejected_entry = next(
            (v for v in self._rate_limits_state.values() if v.get("status") == "rejected"),
            None,
        )
        if api_error_status != 429 and rejected_entry is None:
            # Issue #2190 (AC3): bumped from INFO to WARNING for consistency with the
            # new ordinary-result path's equivalent diagnostic — production runs with
            # INFO logging off, so this must be visible there too.
            logger.warning(
                f"ERROR transition for session {session_id} not rate-limit-shaped — "
                f"error_subtype={structured_error_kwargs.get('error_subtype')}, "
                f"error_terminal_reason={structured_error_kwargs.get('error_terminal_reason')}, "
                f"error_api_error_status={api_error_status}"
            )
            return

        synthetic_entry = self._last_synthetic_text_by_session.pop(session_id, None)
        fire_at, fire_at_source = self._resolve_auto_resume_fire_at(
            rejected_entry, synthetic_entry, datetime.now(UTC).timestamp()
        )

        try:
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info is None:
                logger.warning(
                    f"Rate-limit-shaped ERROR for session {session_id} but session info missing"
                )
                return
            await self.legion_system.scheduler_service.upsert_auto_resume_schedule(
                legion_id=session_info.project_id,
                session_id=session_id,
                session_name=session_info.name,
                fire_at=fire_at,
                fire_at_source=fire_at_source,
            )
            logger.warning(
                f"Scheduled auto-resume for session {session_id} from ERROR-path "
                f"detection: fire_at={fire_at}, source={fire_at_source}"
            )
        except Exception:
            logger.exception(f"Failed to schedule auto-resume for session {session_id}")

    async def _handle_rate_limited_result(
        self,
        session_id: str,
        record_ts: float,
        synthetic_entry: tuple[str, float] | None,
    ) -> None:
        """Issue #2190 (AC1): schedule a one-time auto-resume for the real production
        usage-limit signal — an ordinary ResultMessage (no exception, no ERROR
        transition) carrying `is_error` + `api_error_status == 429`, delivered
        through the normal result path `_maybe_schedule_auto_resume` never sees.

        `synthetic_entry` is passed in already popped from
        `_last_synthetic_text_by_session` by the caller (the 'result' branch of
        `_create_message_callback`), which pops it unconditionally for every result
        — not just this 429 path — since a stashed synthetic notice is only ever
        paired with the one result immediately following it.

        Design decision (Option B): the session is genuinely ACTIVE here (clean CLI
        completion, live subprocess, is_processing already reset) — forcing an ERROR
        transition would be false and would needlessly tear down a healthy SDK
        subprocess. Instead of reusing the ERROR pipeline, this pauses the queue
        directly via pause_queue() so queued sends hold until the schedule fires
        (scheduler_service._fire_auto_resume_schedule unpauses it) or a manual send
        cancels the schedule (routers/sessions.py unpauses it there).

        pause_queue(True) is deliberately the LAST step, only after the schedule is
        confirmed created — pausing first and then failing to create a schedule
        (session deleted mid-flight, scheduler/storage error) would leave the queue
        paused with nothing ever able to unpause it again (no schedule to fire or
        cancel). A schedule with no queue hold is recoverable (the nudge still gets
        enqueued on fire); a queue hold with no schedule is not.
        """
        if self.legion_system is None:
            return

        rejected_entry = next(
            (v for v in self._rate_limits_state.values() if v.get("status") == "rejected"),
            None,
        )
        fire_at, fire_at_source = self._resolve_auto_resume_fire_at(
            rejected_entry, synthetic_entry, record_ts
        )

        try:
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info is None:
                logger.warning(
                    f"Rate-limit-shaped result for session {session_id} but session "
                    f"info missing"
                )
                return
            await self.legion_system.scheduler_service.upsert_auto_resume_schedule(
                legion_id=session_info.project_id,
                session_id=session_id,
                session_name=session_info.name,
                fire_at=fire_at,
                fire_at_source=fire_at_source,
            )
            await self.pause_queue(session_id, True)
            logger.warning(
                f"Scheduled auto-resume for session {session_id} from ordinary-result "
                f"429 detection: fire_at={fire_at}, source={fire_at_source}"
            )
        except Exception:
            logger.exception(f"Failed to schedule auto-resume for session {session_id}")

    def _create_error_callback(self, session_id: str) -> Callable:
        """Create error callback for a session"""
        async def callback(error_type: str, error: Exception):
            try:
                error_data = {
                    "session_id": session_id,
                    "error_type": error_type,
                    "error": str(error),
                    "timestamp": get_unix_timestamp()
                }

                logger.error(f"SDK error in session {session_id}: {error_type} - {error}")

                # Reset processing state on any error
                try:
                    await self.session_manager.end_turn(session_id)
                except Exception:
                    logger.exception(f"Failed to reset processing state for session {session_id}")

                # Handle critical errors that require session state updates
                if error_type in [
                    "startup_failed",
                    "message_processing_loop_error",
                    "immediate_cli_failure",
                    "consumer_task_died",          # Issue #1503
                ]:
                    # logger.info(f"Handling critical SDK error: {error_type}")

                    # Extract user-friendly error message, preserve raw for details
                    raw_error_str = str(error)
                    # Issue #1902: branch on the SDK's structured ResultError before
                    # falling back to substring-matching — existing generic-error
                    # handling (below) still applies unchanged for non-ResultError failures.
                    result_error = error if isinstance(error, ResultError) else None
                    structured_error_kwargs = (
                        self._result_error_structured_kwargs(result_error)
                        if result_error is not None
                        else {}
                    )
                    user_error_message = self._extract_claude_cli_error(raw_error_str, result_error)

                    # Issue #2109 (stage 4a-C follow-up): mark open tools interrupted on a
                    # critical SDK error, same as terminate_session/interrupt_session/
                    # restart_session already do — a tool left open across an error-induced
                    # ERROR transition previously had no stored terminal record.
                    self.mark_session_tools_interrupted(session_id)

                    # Update session state to ERROR and reset processing state
                    await self._transition_to_error(
                        session_id, user_error_message, **structured_error_kwargs
                    )

                    # Issue #1572: detect a rate-limit-shaped critical error and schedule
                    # an automatic resume. Detection is intentionally session-scoped (this
                    # callback is a per-session closure) even though the reset timestamp it
                    # consults comes from the account-wide _rate_limits_state (issue #899) —
                    # that state tells us *when* the account-wide limit resets, not *which*
                    # session just stalled, so the gate here is what keeps this scoped to
                    # the session that actually failed.
                    await self._maybe_schedule_auto_resume(session_id, structured_error_kwargs)

                    # Send system message explaining the runtime failure (with raw details)
                    await self._send_session_failure_message(
                        session_id, user_error_message, raw_error_str, **structured_error_kwargs
                    )

                    # Clean up the failed SDK
                    if session_id in self._active_sdks:
                        del self._active_sdks[session_id]
                        # logger.info(f"Cleaned up failed SDK for session {session_id}")

                # Call registered callbacks
                callbacks = self._error_callbacks.get(session_id, [])
                for cb in callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(session_id, error_data)
                        else:
                            cb(session_id, error_data)
                    except Exception:
                        logger.exception("Error in error callback")

            except Exception:
                logger.exception(f"Error processing error callback for {session_id}")

        return callback

    def _create_stderr_callback(self, session_id: str) -> Callable:
        """Create stderr callback that routes SDK stderr output through the message pipeline.

        Issue #517: Each stderr line becomes a system message with subtype 'stderr',
        persisted to messages.jsonl and broadcast to the frontend via WebSocket.
        Issue #871/#1837: Routine Docker/BuildKit progress output (both this app's own
        wrapper informational lines and raw, non-wrapper-prefixed `docker build`/`pull` CLI
        output) is logged at low severity and never forwarded to the frontend, so it can't
        produce spurious warning-styled pills for a successful build/run/pull. Only output
        classified as an actual failure is logged loud and still reaches the frontend.
        """
        message_callback = self._create_message_callback(session_id)

        async def callback(output: str):
            try:
                classification = classify_docker_output(output)

                if classification == "failure":
                    coord_logger.warning(f"[STDERR] Session {session_id}: {output}")
                elif classification == "routine":
                    coord_logger.debug(f"[STDERR] Session {session_id}: {output}")
                    return  # Issue #871/#1837: known-safe progress noise — do not forward
                else:  # ambiguous
                    coord_logger.info(f"[STDERR] Session {session_id}: {output}")

                stderr_message = {
                    "type": "system",
                    "subtype": "stderr",
                    "content": output,
                    "session_id": session_id,
                    "timestamp": get_unix_timestamp()
                }
                # Issue #2109 (AC11): _store_processed_message now raises on a
                # canonicalization failure (logged there) instead of silently storing a
                # non-canonical fallback — but that must not also suppress this live
                # delivery to the frontend, so it's caught here and the callback still
                # fires with whatever shape message_data is left in.
                try:
                    await self._store_processed_message(session_id, stderr_message)
                except Exception:
                    pass
                await message_callback(stderr_message)
            except Exception:
                logger.exception(f"Error in stderr callback for session {session_id}")

        return callback

    async def _send_mcp_degraded_message(
        self, session_id: str, unavailable: list[tuple[str, str]]
    ) -> None:
        """Send a non-fatal system message naming shared MCP servers unavailable at start.

        Issue #1982: sent once per start_session() call (initial start, resume, restart,
        reset) when unavailable is non-empty — never per-turn. Goes through the same
        storage/callback path as client_launched, so it lands in the persisted transcript
        the agent reads as context (AC2/US2) and is visible to the user without reading
        backend tracebacks (US3), with no new UI surface required.
        """
        try:
            lines = "\n".join(f"- {name}: {reason}" for name, reason in unavailable)
            content = (
                "Some configured MCP servers are unavailable:\n"
                f"{lines}\n\n"
                "Other tools and servers are unaffected. These will be retried automatically."
            )
            message_data = {
                "type": "system",
                "subtype": "mcp_server_degraded",
                "is_error": False,
                "content": content,
                "session_id": session_id,
                "timestamp": get_unix_timestamp(),
                "sdk_message_type": "SystemMessage",
            }

            # Issue #2109 (AC11): a canonicalization-failure raise must not also
            # suppress this live delivery to the frontend (see stderr callback above).
            try:
                await self._store_processed_message(session_id, message_data)
            except Exception:
                pass

            callback = self._create_message_callback(session_id)
            await callback(message_data)

            coord_logger.info(
                f"MCP degraded message sent for session {session_id}: "
                f"{[name for name, _ in unavailable]}"
            )
        except Exception:
            logger.exception(f"Failed to send MCP degraded message for {session_id}")

    async def _send_client_launched_message(self, session_id: str):
        """Send a system message indicating the Claude SDK client was launched/resumed"""
        try:
            # logger.info(f"DEBUG: _send_client_launched_message called for session {session_id}")

            # Create system message for client launch
            message_data = {
                "type": "system",
                "subtype": "client_launched",
                "content": "Claude Code Launched",
                "session_id": session_id,
                "timestamp": get_unix_timestamp(),
                "sdk_message_type": "SystemMessage"
            }

            # logger.info(f"DEBUG: About to store processed message: {message_data}")

            # Process and store message using unified MessageProcessor. Issue #2109
            # (AC11): a canonicalization-failure raise must not also suppress this
            # live delivery to the frontend (see stderr callback above).
            try:
                await self._store_processed_message(session_id, message_data)
            except Exception:
                pass

            # logger.info(f"DEBUG: Message stored, about to send via callback")

            # Send through message callback system for real-time display
            callback = self._create_message_callback(session_id)
            await callback(message_data)

            # logger.info(f"SUCCESS: Sent client launched message for session {session_id}")

        except Exception:
            logger.exception(f"Failed to send client launched message for {session_id}")

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
        """Send a system message indicating the session failed to start.

        error_subtype/error_terminal_reason/error_api_error_status/error_list are
        optional structured ResultError fields (issue #1902) — additive keys on
        the stored message, added to metadata only when present. Old callers that
        don't pass them produce a message identical to today's.
        """
        try:
            short_reason = self._format_failure_content(error_message, raw_error)
            raw_dump = raw_error or error_message

            # Create system message for session failure
            message_data = {
                "type": "system",
                "subtype": "session_failed",
                "is_error": True,
                "content": short_reason,
                "error_details": raw_dump,
                "session_id": session_id,
                "timestamp": get_unix_timestamp(),
                "sdk_message_type": "SystemMessage",
            }
            if error_subtype is not None:
                message_data["error_subtype"] = error_subtype
            if error_terminal_reason is not None:
                message_data["error_terminal_reason"] = error_terminal_reason
            if error_api_error_status is not None:
                message_data["error_api_error_status"] = error_api_error_status
            if error_list:
                message_data["errors"] = error_list

            # Process and store message using unified MessageProcessor. Issue #2109
            # (AC11): a canonicalization-failure raise must not also suppress this
            # live delivery to the frontend (see stderr callback above) — this message
            # in particular is the user's only explanation for why their session failed.
            try:
                await self._store_processed_message(session_id, message_data)
            except Exception:
                pass

            # Send through message callback system for real-time display
            callback = self._create_message_callback(session_id)
            await callback(message_data)

            coord_logger.info(f"Session failure message sent for session {session_id}")
        except Exception:
            logger.exception(f"Failed to send session failure message for {session_id}")

    async def _send_interrupt_message(self, session_id: str):
        """Send interrupt system message via callback system (following client_launched pattern)"""
        try:
            # Create interrupt system message
            message_data = {
                "type": "system",
                "subtype": "interrupt",
                "content": "User Interrupted Processing",
                "session_id": session_id,
                "timestamp": get_unix_timestamp(),
                "sdk_message_type": "SystemMessage"
            }

            # Process and store message using unified MessageProcessor. Issue #2109
            # (AC11): a canonicalization-failure raise must not also suppress this
            # live delivery to the frontend (see stderr callback above).
            try:
                await self._store_processed_message(session_id, message_data)
            except Exception:
                pass

            # Send through message callback system for real-time display
            callback = self._create_message_callback(session_id)
            await callback(message_data)

            coord_logger.info(f"Interrupt message sent for session {session_id}")

        except Exception:
            logger.exception(f"Failed to send interrupt message for {session_id}")

    async def resolve_orphaned_ephemeral_session(self, session_id: str) -> None:
        """Resolve a scheduled/ephemeral session found PAUSED at boot (issue #2165).

        A PAUSED ephemeral session has no live SDK/Future that could ever answer its
        pending permission decision — unlike an interactive session (#2159), nothing
        will ever revisit it, so it would otherwise stay wedged in PAUSED forever.
        Transitions it to ERROR via the existing _transition_to_error() helper
        (ERROR_OCCURRED semantics: state update, processing-state reset, and denying
        the dangling permission Future) and emits a synthetic system message with a
        distinct subtype so the auto-resolution is independently auditable, mirroring
        _send_session_failure_message()'s store-then-callback pattern.
        """
        error_message = (
            "This scheduled agent was paused awaiting a permission decision when the "
            "server restarted. Since no process remains to resolve that decision, the "
            "session has been marked as errored so its schedule can start a fresh agent."
        )
        try:
            await self._transition_to_error(session_id, error_message)

            message_data = {
                "type": "system",
                "subtype": "ephemeral_orphan_resolved",
                "is_error": True,
                "content": error_message,
                "session_id": session_id,
                "timestamp": get_unix_timestamp(),
                "sdk_message_type": "SystemMessage",
            }
            try:
                await self._store_processed_message(session_id, message_data)
            except Exception:
                pass

            callback = self._create_message_callback(session_id)
            await callback(message_data)

            coord_logger.info(f"Resolved orphaned ephemeral session {session_id} to ERROR")
        except Exception:
            logger.exception(f"Failed to resolve orphaned ephemeral session {session_id}")

    def _format_failure_content(self, friendly_error: str, raw_error: str | None) -> str:
        """Extract a short, meaningful headline from raw stderr for the session_failed pill.

        Full stderr is stored in error_details; this returns only the summary line.
        """
        if not raw_error:
            return f"Session failed: {friendly_error}" if friendly_error else "Session process exited with error"

        # Pattern-match well-known error conditions for a clean label.
        # More-specific patterns come first (proxy names before generic DNS).
        known_patterns = [
            (r"cc-webui\.internal|host\.docker\.internal", "Proxy startup failed: cc-webui.internal not reachable"),
            (r"permission denied", "Permission denied"),
            (r"name or service not known|name resolution fail|no such host", "DNS name resolution failed"),
            (r"connection refused", "Connection refused"),
            (r"no such file or directory", "File or directory not found"),
            (r"address already in use", "Port already in use"),
        ]
        for pattern, label in known_patterns:
            if re.search(pattern, raw_error, re.IGNORECASE):
                return f"Session failed: {label}"

        # Extract from 'Stderr output:\n' marker if present, otherwise use full raw_error
        stderr_marker = "Stderr output:\n"
        idx = raw_error.find(stderr_marker)
        stderr_content = raw_error[idx + len(stderr_marker):].strip() if idx >= 0 else raw_error.strip()

        # Use last non-empty line as the most specific signal
        lines = [line.strip() for line in stderr_content.splitlines() if line.strip()]
        if lines:
            last_line = lines[-1]
            if last_line and last_line != friendly_error:
                return f"Session failed: {last_line[:120]}"

        return f"Session failed: {friendly_error}" if friendly_error else "Session process exited with error"

    def _result_error_structured_kwargs(self, e: Any) -> dict:
        """Build the error_subtype/error_terminal_reason/error_api_error_status/error_list
        kwargs shared by update_session_state() and _send_session_failure_message().

        Accepts a real ResultError or any duck-typed object exposing the same
        subtype/terminal_reason/api_error_status/errors attributes (the
        sdk.info-mirrored shim used by start_session()'s startup-failure fallback
        also satisfies this). Filters out the SDK's subtype=="success" sentinel
        (set when the agent loop completed but the last turn was an API error) —
        surfacing "success" as an error-subtype badge would be misleading.
        """
        subtype = getattr(e, "subtype", None)
        errors = getattr(e, "errors", None)
        return {
            "error_subtype": subtype if subtype and subtype != "success" else None,
            "error_terminal_reason": getattr(e, "terminal_reason", None),
            "error_api_error_status": getattr(e, "api_error_status", None),
            "error_list": list(errors) if errors else None,
        }

    def _format_result_error_friendly_message(self, e: Any, fallback: str | None = None) -> str:
        """Build a friendly one-line message from a ResultError's structured fields.

        Mirrors the SDK's own precedence in
        claude_agent_sdk._internal.query._error_result_text: errors[] (joined),
        then result text, then a non-success subtype, then the HTTP status.
        Accepts a real ResultError or a duck-typed sdk.info-mirrored shim (see
        _result_error_structured_kwargs).
        """
        errors = getattr(e, "errors", None)
        if errors:
            return "; ".join(errors)
        result = getattr(e, "result", None)
        if result and result.strip():
            return result.strip()
        subtype = getattr(e, "subtype", None)
        if subtype and subtype != "success":
            return subtype.replace("_", " ")
        api_error_status = getattr(e, "api_error_status", None)
        if api_error_status is not None:
            return f"API error (HTTP {api_error_status})"
        return fallback if fallback is not None else str(e)

    def _extract_claude_cli_error(self, error_message: str, result_error: ResultError | None = None) -> str:
        """Extract and format user-friendly error messages from Claude CLI output.

        Issue #1902: when result_error is provided, prefer its structured
        terminal_reason/subtype/errors over the substring patterns below — those
        remain the unchanged fallback for non-ResultError failures (container
        crashes, generic process errors, etc.).
        """
        if result_error is not None:
            return self._format_result_error_friendly_message(result_error, fallback=error_message)
        try:
            error_str = str(error_message).strip()

            # Common Claude CLI error patterns and their user-friendly versions
            error_patterns = {
                "Command failed with exit code 1": "Claude Code command failed",
                "Fatal error in message reader": "Claude Code CLI failed during startup",
                "not a valid UUID": "Invalid session ID format",
                "--resume requires a valid session ID": "Session resume failed - invalid session ID",
                "Check stderr output for details": "See error details above",
                "Claude Code command failed - see details above": "Claude Code CLI failed - check error details",
                # Issue #781: Container crash patterns (passthrough — already parsed by ClaudeSDK)
                # Issue #893: All classification prefixes must be listed here
                "Container crash detected": None,
                "Process exited with error": None,
                "Process terminated": None,
                "Container setup error": None,
                "SDK response exceeded maximum buffer size": None,
                "Docker image not found": None,
                "Docker daemon is not running": None,
                "Docker socket permission denied": None,
                "Nested Claude Code session detected": None,
                # Issue #958: Sandbox unavailable with failIfUnavailable=True — passthrough.
                # Full error: "Error: sandbox required but unavailable: ... sandbox.failIfUnavailable is set"
                "sandbox.failIfUnavailable is set": None,
            }

            # Check for known patterns and provide clearer descriptions
            for pattern, friendly_msg in error_patterns.items():
                if pattern in error_str:
                    # Issue #781: None means passthrough (already parsed by ClaudeSDK)
                    if friendly_msg is None:
                        return error_str

                    # If it's a UUID error, try to extract the actual UUID from the message
                    if "not a valid UUID" in error_str and "Provided value" in error_str:
                        # Try to extract the invalid UUID value
                        uuid_match = re.search(r'Provided value "([^"]+)"', error_str)
                        if uuid_match:
                            invalid_uuid = uuid_match.group(1)
                            return f"Invalid session ID format: '{invalid_uuid}' is not a valid UUID"

                    # For resume errors, provide more context
                    if "--resume requires a valid session ID" in error_str:
                        return "Session resume failed: Invalid or missing Claude Code session ID. This may indicate the session was corrupted or manually modified."

                    return friendly_msg

            # If no patterns match, return the original message but clean it up
            cleaned_msg = error_str.replace("Error output: Check stderr output for details", "").strip()
            if cleaned_msg:
                return cleaned_msg
            else:
                return "Unknown Claude Code error occurred"

        except Exception as e:
            logger.warning(f"Failed to extract CLI error message: {e}")
            return str(error_message)

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
        """
        Register an uploaded file path for auto-approve Read permissions.

        When a user uploads a file, the path is registered so that subsequent
        Read tool requests for that path can be auto-approved without prompting.

        Args:
            session_id: Session ID that owns the file
            file_path: Absolute path to the uploaded file
        """
        if session_id not in self._uploaded_file_paths:
            self._uploaded_file_paths[session_id] = set()
        self._uploaded_file_paths[session_id].add(file_path)
        coord_logger.debug(f"Registered uploaded file for session {session_id}: {file_path}")

    async def unregister_uploaded_file(self, session_id: str, file_path: str) -> None:
        """
        Unregister an uploaded file path from auto-approve tracking.

        Called when a file is deleted by the user.

        Args:
            session_id: Session ID that owns the file
            file_path: Absolute path to the uploaded file
        """
        if session_id in self._uploaded_file_paths:
            self._uploaded_file_paths[session_id].discard(file_path)
            coord_logger.debug(f"Unregistered uploaded file for session {session_id}: {file_path}")

    def is_uploaded_file(self, session_id: str, file_path: str) -> bool:
        """
        Check if a file path is an uploaded file for a session.

        Used by permission callbacks to auto-approve Read requests for
        user-uploaded files.

        Args:
            session_id: Session ID to check
            file_path: Absolute path to check

        Returns:
            True if the file was uploaded by the user for this session
        """
        return file_path in self._uploaded_file_paths.get(session_id, set())

    def get_uploaded_files(self, session_id: str) -> set[str]:
        """
        Get all uploaded file paths for a session.

        Args:
            session_id: Session ID

        Returns:
            Set of absolute file paths uploaded to this session
        """
        return self._uploaded_file_paths.get(session_id, set()).copy()

    def clear_uploaded_files(self, session_id: str) -> None:
        """
        Clear all uploaded file tracking for a session.

        Called when a session is deleted or reset.

        Args:
            session_id: Session ID to clear
        """
        if session_id in self._uploaded_file_paths:
            del self._uploaded_file_paths[session_id]
            coord_logger.debug(f"Cleared uploaded files tracking for session {session_id}")

    # ==================== COMPACTION-TIME DISTILLATION (Issue #1628) ====================

    async def _handle_compact_boundary(self, session_id: str, parsed_message) -> None:
        """Handle a compact_boundary system message by scheduling history distillation.

        Only fires on exact 'compact_boundary' subtype. 'microcompact_boundary' is
        explicitly excluded — it represents a smaller in-context compaction (not a full
        context wipe) and does not warrant a dedicated summary. If a future product
        decision wants microcompactions summarized, extend the detection condition in
        _create_message_callback.

        No-ops when history_distillation_enabled is False in the session's effective
        config, matching the existing archive-time distillation gate.
        """
        try:
            session_info = await self.session_manager.get_session_info(session_id)
            if session_info is None:
                return
            eff = await resolve_effective_config(
                session_info, self.template_manager, self.profile_manager
            )
            if not eff.history_distillation_enabled:
                return
            boundary_ts = parsed_message.timestamp
            t = asyncio.create_task(self._distill_compaction(session_id, boundary_ts))
            t.add_done_callback(task_done_log_exception)
            coord_logger.debug(
                f"Launched compaction-time distillation for session {session_id} at ts={boundary_ts}"
            )
        except Exception:
            logger.exception(
                f"Failed to schedule compaction distillation for session {session_id}"
            )

    async def _distill_compaction(self, session_id: str, boundary_ts: float) -> None:
        """Distill messages from the previous compaction boundary to this one.

        Scans messages.jsonl for prior compact_boundary entries to determine the
        slice window, writes a temp jsonl for that slice, calls distill_session_history,
        then registers the result as a session resource so the frontend can display it.
        """
        from datetime import UTC, datetime

        from backend.history_distiller import distill_session_history

        session_dir = self.session_manager.sessions_dir / session_id
        messages_file = session_dir / "messages.jsonl"

        if not messages_file.exists():
            return

        # Scan for all compact_boundary timestamps to determine slice window.
        import json as _json
        boundary_timestamps: list[float] = []
        try:
            with open(messages_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    stored_type = row.get("_type")
                    if stored_type:
                        raise LegacyMessageFormatError(session_id, stored_type)
                    if row.get("type") == "system":
                        meta = row.get("metadata") or {}
                        if meta.get("subtype") == "compact_boundary":
                            ts = row.get("timestamp")
                            if ts is not None:
                                boundary_timestamps.append(float(ts))
        except OSError:
            logger.exception(f"Failed to scan messages.jsonl for {session_id}")
            return

        # Determine previous boundary (slice starts at prev_ts, exclusive).
        boundary_timestamps_sorted = sorted(boundary_timestamps)
        prev_ts: float | None = None
        for ts in boundary_timestamps_sorted:
            if ts < boundary_ts:
                prev_ts = ts

        # Write slice to temp file.
        history_dir = session_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        safe_ts = str(int(boundary_ts))
        tmp_path = history_dir / f".compaction_{safe_ts}.tmp.jsonl"
        output_path = history_dir / f"{safe_ts}.md"

        try:
            with open(messages_file, encoding="utf-8") as src, \
                    open(tmp_path, "w", encoding="utf-8") as dst:
                for line in src:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        row = _json.loads(stripped)
                    except _json.JSONDecodeError:
                        continue
                    ts = row.get("timestamp")
                    if ts is None:
                        continue
                    ts_f = float(ts)
                    # Include messages after prev boundary (exclusive) up to and
                    # including this boundary (inclusive).
                    if prev_ts is not None and ts_f <= prev_ts:
                        continue
                    if ts_f > boundary_ts:
                        continue
                    dst.write(stripped + "\n")

            boundary_iso = datetime.fromtimestamp(boundary_ts, tz=UTC).isoformat()
            success = await distill_session_history(
                tmp_path, output_path, session_id, boundary_iso
            )
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        if not success:
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            return

        # Register the markdown file as a session resource for frontend correlation.
        try:
            from datetime import datetime as _dt
            time_label = _dt.fromtimestamp(boundary_ts, tz=UTC).strftime("%H:%M")
            title = f"Compaction Summary — {time_label}"
            description = f"compaction:{int(boundary_ts)}"
            await self.register_uploaded_resource(
                session_id,
                str(output_path),
                title=title,
                description=description,
            )
            coord_logger.info(
                f"Registered compaction summary resource for session {session_id} at ts={boundary_ts}"
            )
        except Exception:
            logger.exception(
                f"Failed to register compaction summary resource for session {session_id}"
            )

    # ==================== RESOURCE GALLERY INTEGRATION (Issue #404) ====================

    async def register_uploaded_resource(
        self,
        session_id: str,
        file_path: str,
        title: str | None = None,
        description: str | None = None
    ) -> dict | None:
        """
        Register an uploaded file to the resource gallery.

        When a user uploads a file through the attachment system,
        this automatically adds it to the task panel resource gallery.

        Args:
            session_id: Session ID that owns the resource
            file_path: Absolute path to the uploaded file
            title: Title for the resource (required — the underlying MCP handler
                rejects an empty title, issue #1912)
            description: Optional description

        Returns:
            Dict with resource_id and markdown URL, or None if registration failed
        """
        if not self.resource_mcp_tools:
            coord_logger.warning("Resource MCP tools not available")
            return None

        # Build args matching the MCP tool interface
        args = {
            "file_path": file_path,
            "title": title or "",
            "description": description or ""
        }

        # Use the MCP tool's internal handler directly
        result = await self.resource_mcp_tools._handle_register_resource(session_id, args)

        if result.get("is_error"):
            error_text = result.get("content", [{}])[0].get("text", "Unknown error")
            raise ValueError(f"Failed to register resource: {error_text}")

        coord_logger.info(f"Registered uploaded resource to gallery for session {session_id}: {title}")

        # Extract resource_id from result text and build metadata for caller
        result_text = result.get("content", [{}])[0].get("text", "")
        resource_id = None
        for line in result_text.splitlines():
            if line.startswith("- ID: "):
                resource_id = line[len("- ID: "):].strip()
                break

        if resource_id:
            from backend.mcp.resource_mcp_tools import ResourceMCPTools
            is_image = any(
                file_path.lower().endswith(ext)
                for ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico', '.tiff', '.tif')
            )
            return {
                "resource_id": resource_id,
                "markdown": ResourceMCPTools._get_resource_url(
                    session_id, resource_id, title or "", is_image
                ),
            }
        return None

    # Backward compatibility alias
    async def register_uploaded_image(
        self,
        session_id: str,
        file_path: str,
        title: str | None = None,
        description: str | None = None
    ) -> None:
        """Deprecated: Use register_uploaded_resource instead."""
        await self.register_uploaded_resource(session_id, file_path, title, description)

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
            from backend.config_resolution import resolve_template_config

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
