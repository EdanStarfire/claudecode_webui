"""
Message Pipeline for Claude Code WebUI

Extracted from SessionCoordinator (issue #2189, epic #1990 stage 7
`coordinator-split`, PR 2): owns the message-pipeline cluster — the SDK
message callback (parsing, activity/latest-message tracking, task-registry
feed, analytics recording, is_processing reactivity), the error/stderr/phase
callback factories, auto-resume scheduling on rate-limit detection, and the
lifecycle system-message senders (client_launched, session_failed,
mcp_server_degraded, interrupt, ephemeral-orphan resolution). Mechanical
extraction, following the exact pattern PR 1 established for SessionLauncher:
SessionCoordinator keeps every method as a one-line delegate, and every
collaborator/shared per-session dict stays constructed on the coordinator;
MessagePipeline reaches them via live properties, never copies.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.analytics.usage import (
    _accumulate_subagent_usage,
    _delta_from_baseline,
    _normalize_result_usage,
)
from backend.docker_utils import classify_docker_output
from shared.logging_config import get_logger

from .background_tasks import (
    BACKGROUND_TASKS_SUBTYPE,
    PROCESS_BOUNDARY_SUBTYPES,
    parse_background_tasks,
)
from .claude_sdk import ResultError
from .config_resolution import resolve_effective_config
from .models.messages import _message_dict_to_record
from .task_registry import TASK_LIFECYCLE_SUBTYPES
from .timestamp_utils import get_unix_timestamp, parse_rate_limit_reset_time

# Same logging categories as the coordinator — this is still "coordinator
# actions" from an operator's perspective, just physically relocated.
coord_logger = get_logger('coordinator', category='COORDINATOR')
logger = logging.getLogger(__name__)

# Issue #1572: auto-resume timing — fire 5 minutes after a known rate-limit reset,
# or fall back to a flat 1 hour out when no fresh reset timestamp is available (AC4).
AUTO_RESUME_GRACE_PERIOD_SECONDS = 5 * 60
AUTO_RESUME_FALLBACK_DELAY_SECONDS = 60 * 60


@dataclass
class MessagePipelineCallbacks:
    """Narrow reverse-seam into not-yet-extracted coordinator logic.

    Separate from SessionLauncher's own LauncherCallbacks (even though both
    currently point at the same underlying `mark_session_tools_interrupted`
    coordinator method) — two independent consumer-specific bundles so a
    future tool-call-tracker extraction (issue #2189 PR 3) updates each
    consumer's wiring independently, with no shared type both modules would
    need to agree on.
    """

    mark_session_tools_interrupted: Callable[[str], Any]


class MessagePipeline:
    """Owns SDK message/error/stderr/phase callback creation, auto-resume
    scheduling on rate-limit detection, and lifecycle system-message senders.
    """

    def __init__(self, *, coordinator: Any, callbacks: MessagePipelineCallbacks):
        # Issue #2189: every collaborator manager and shared per-session dict is a
        # LIVE property lookup against the coordinator (below), never a value
        # snapshotted here at construction time — see SessionLauncher's own
        # __init__ docstring (session_launcher.py) for the full rationale; the
        # same staleness risks apply here (analytics_store and permission_service
        # in particular are None at SessionCoordinator.__init__ time, set later).
        self._coordinator = coordinator

        # Narrow callback bundle into not-yet-extracted coordinator logic.
        self._callbacks = callbacks

    # --- Collaborator managers: live passthrough to the coordinator ----------
    session_manager = property(lambda self: self._coordinator.session_manager)
    template_manager = property(lambda self: self._coordinator.template_manager)
    profile_manager = property(lambda self: self._coordinator.profile_manager)
    analytics_store = property(lambda self: self._coordinator.analytics_store)
    message_processor = property(lambda self: self._coordinator.message_processor)
    legion_system = property(lambda self: self._coordinator.legion_system)
    permission_service = property(lambda self: self._coordinator.permission_service)

    # --- Shared per-session state: live passthrough to the coordinator -------
    _storage_managers = property(lambda self: self._coordinator._storage_managers)
    _active_sdks = property(lambda self: self._coordinator._active_sdks)
    _last_synthetic_text_by_session = property(lambda self: self._coordinator._last_synthetic_text_by_session)
    _last_turn_model_by_session = property(lambda self: self._coordinator._last_turn_model_by_session)
    _subagent_usage_by_session = property(lambda self: self._coordinator._subagent_usage_by_session)
    _usage_baseline_by_session = property(lambda self: self._coordinator._usage_baseline_by_session)
    _permission_updates = property(lambda self: self._coordinator._permission_updates)
    _retry_sequences = property(lambda self: self._coordinator._retry_sequences)
    _message_callbacks = property(lambda self: self._coordinator._message_callbacks)
    _error_callbacks = property(lambda self: self._coordinator._error_callbacks)
    _rate_limits_state = property(lambda self: self._coordinator._rate_limits_state)
    _rate_limit_broadcast_callback = property(lambda self: self._coordinator._rate_limit_broadcast_callback)
    _usage_broadcast_callback = property(lambda self: self._coordinator._usage_broadcast_callback)
    _task_leg_registries = property(lambda self: self._coordinator._task_leg_registries)
    _background_tasks = property(lambda self: self._coordinator._background_tasks)
    _tool_call_events = property(lambda self: self._coordinator._tool_call_events)
    _message_emitted_events = property(lambda self: self._coordinator._message_emitted_events)
    _emitted_turn_ids = property(lambda self: self._coordinator._emitted_turn_ids)

    # ============================================================
    # Issue #1694/#1958: message-emitted barrier
    # ============================================================

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

    # ============================================================
    # SDK message/error/stderr/phase callback factories
    # ============================================================

    async def on_rate_limits(self, rate_limit_info: Any) -> None:
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
            # create_error_callback can tell "account-wide limit hit" (status ==
            # "rejected") apart from the merely-approaching-limit warning state.
            if status is not None:
                entry['status'] = status

            self._rate_limits_state[window] = entry

            if self._rate_limit_broadcast_callback:
                self._rate_limit_broadcast_callback(dict(self._rate_limits_state))
        except Exception:
            logger.exception("Error processing rate_limits")

    def create_message_callback(self, session_id: str) -> Callable:
        """Create message callback for a session using unified MessageProcessor"""
        # Issue #2189 (coordinator-split PR 2): deferred import avoids the circular
        # top-level import (session_coordinator.py imports MessagePipeline at module
        # scope) — same pattern SessionLauncher.start_session() uses for its own
        # coordinator-module helper imports. Issue #2267: the usage/cost helpers
        # moved to backend.analytics.usage (top-level import above) — only
        # _nested_metadata/_resolve_analytics_model_label still live on
        # session_coordinator.py, so only those two still need deferring here.
        from .session_coordinator import (
            _nested_metadata,
            _resolve_analytics_model_label,
        )

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
                            registry = await self._coordinator._get_task_leg_registry(session_id)
                            if registry_already_cached:
                                registry.apply_frame(task_subtype, parsed_message.metadata, parsed_message.timestamp)
                        except Exception:
                            logger.exception(f"Failed to update task leg registry for session {session_id}")

                    # Issue #2145: keep the latest-snapshot background-task cache warm.
                    # client_launched/session_failed mean any earlier snapshot belonged
                    # to a dead process (the CLI emits no empty list on death).
                    if task_subtype in PROCESS_BOUNDARY_SUBTYPES:
                        self._background_tasks[session_id] = []
                    elif task_subtype == BACKGROUND_TASKS_SUBTYPE:
                        # Each frame carries the FULL list, so it simply replaces the
                        # cache (no cold/warm guard needed, unlike the leg registry).
                        # Prefer the record's own nested metadata: the parser's dict
                        # path drops init_data (same trap as issue #2190).
                        try:
                            self._background_tasks[session_id] = parse_background_tasks(
                                _nested_metadata(message_data, parsed_message)
                            )
                        except Exception:
                            logger.exception(f"Failed to update background tasks for session {session_id}")

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
                    # with is_error + api_error_status == 429 — create_error_callback's
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
                            turn_seq = await self._coordinator._next_turn_seq(session_id)
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
                    task = asyncio.create_task(self._coordinator._check_sdk_generated_name(session_id))
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
                    await self._coordinator._handle_compact_boundary(session_id, parsed_message)

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

    def create_phase_callback(self, session_id: str) -> Callable:
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

    async def transition_to_error(
        self, session_id: str, error_message: str, **structured_error_kwargs: Any
    ) -> None:
        """Collapse the state-transition half of the ERROR path into one call
        (issue #2159) — the caller still owns its own send_session_failure_message()
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
        # Issue #2145: the process is gone; its background tasks died with it.
        self._background_tasks.pop(session_id, None)
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
        `create_message_callback`), which pops it unconditionally for every result
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
            await self._coordinator.pause_queue(session_id, True)
            logger.warning(
                f"Scheduled auto-resume for session {session_id} from ordinary-result "
                f"429 detection: fire_at={fire_at}, source={fire_at_source}"
            )
        except Exception:
            logger.exception(f"Failed to schedule auto-resume for session {session_id}")

    def create_error_callback(self, session_id: str) -> Callable:
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
                        self.result_error_structured_kwargs(result_error)
                        if result_error is not None
                        else {}
                    )
                    user_error_message = self.extract_claude_cli_error(raw_error_str, result_error)

                    # Issue #2109 (stage 4a-C follow-up): mark open tools interrupted on a
                    # critical SDK error, same as terminate_session/interrupt_session/
                    # restart_session already do — a tool left open across an error-induced
                    # ERROR transition previously had no stored terminal record.
                    self._callbacks.mark_session_tools_interrupted(session_id)

                    # Update session state to ERROR and reset processing state
                    await self.transition_to_error(
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
                    await self.send_session_failure_message(
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

    def create_stderr_callback(self, session_id: str) -> Callable:
        """Create stderr callback that routes SDK stderr output through the message pipeline.

        Issue #517: Each stderr line becomes a system message with subtype 'stderr',
        persisted to messages.jsonl and broadcast to the frontend via WebSocket.
        Issue #871/#1837: Routine Docker/BuildKit progress output (both this app's own
        wrapper informational lines and raw, non-wrapper-prefixed `docker build`/`pull` CLI
        output) is logged at low severity and never forwarded to the frontend, so it can't
        produce spurious warning-styled pills for a successful build/run/pull. Only output
        classified as an actual failure is logged loud and still reaches the frontend.
        """
        message_callback = self.create_message_callback(session_id)

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

    async def send_mcp_degraded_message(
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

            callback = self.create_message_callback(session_id)
            await callback(message_data)

            coord_logger.info(
                f"MCP degraded message sent for session {session_id}: "
                f"{[name for name, _ in unavailable]}"
            )
        except Exception:
            logger.exception(f"Failed to send MCP degraded message for {session_id}")

    async def send_client_launched_message(self, session_id: str):
        """Send a system message indicating the Claude SDK client was launched/resumed"""
        try:
            # Create system message for client launch
            message_data = {
                "type": "system",
                "subtype": "client_launched",
                "content": "Claude Code Launched",
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
            callback = self.create_message_callback(session_id)
            await callback(message_data)

        except Exception:
            logger.exception(f"Failed to send client launched message for {session_id}")

    async def send_session_failure_message(
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
            callback = self.create_message_callback(session_id)
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
            callback = self.create_message_callback(session_id)
            await callback(message_data)

            coord_logger.info(f"Interrupt message sent for session {session_id}")

        except Exception:
            logger.exception(f"Failed to send interrupt message for {session_id}")

    async def resolve_orphaned_ephemeral_session(self, session_id: str) -> None:
        """Resolve a scheduled/ephemeral session found PAUSED at boot (issue #2165).

        A PAUSED ephemeral session has no live SDK/Future that could ever answer its
        pending permission decision — unlike an interactive session (#2159), nothing
        will ever revisit it, so it would otherwise stay wedged in PAUSED forever.
        Transitions it to ERROR via the existing transition_to_error() helper
        (ERROR_OCCURRED semantics: state update, processing-state reset, and denying
        the dangling permission Future) and emits a synthetic system message with a
        distinct subtype so the auto-resolution is independently auditable, mirroring
        send_session_failure_message()'s store-then-callback pattern.
        """
        error_message = (
            "This scheduled agent was paused awaiting a permission decision when the "
            "server restarted. Since no process remains to resolve that decision, the "
            "session has been marked as errored so its schedule can start a fresh agent."
        )
        try:
            await self.transition_to_error(session_id, error_message)

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

            callback = self.create_message_callback(session_id)
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

    def result_error_structured_kwargs(self, e: Any) -> dict:
        """Build the error_subtype/error_terminal_reason/error_api_error_status/error_list
        kwargs shared by update_session_state() and send_session_failure_message().

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
        result_error_structured_kwargs).
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

    def extract_claude_cli_error(self, error_message: str, result_error: ResultError | None = None) -> str:
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
