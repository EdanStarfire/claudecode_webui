"""Registry of every top-level EventQueue event type (issue #2063, epic #1990 stage 2a-A).

Enumerates the 31 distinct `type` values actually produced across the 35 direct + 8 indirect
queue-append call sites audited for #2063 (see the issue for the full call-site table). Kept
honest by `shared/tests/test_event_registry_completeness.py`, which source-scans the producer
modules and asserts every literal `type` it finds is a key here.
"""

from dataclasses import dataclass, field

from shared.event_envelope import QUEUE_AUDIT, QUEUE_SESSION, QUEUE_UI


@dataclass(frozen=True)
class EventTypeSpec:
    queues: frozenset[str]  # one or more of QUEUE_UI/QUEUE_SESSION/QUEUE_AUDIT
    required_keys: frozenset[str] = field(default_factory=frozenset)
    # compare=False keeps EventTypeSpec hashable under @dataclass(frozen=True) — a plain
    # dict field would otherwise make the auto-generated __hash__ raise TypeError for
    # every spec, not just the ones that populate this override.
    required_keys_by_queue: dict[str, frozenset[str]] = field(
        default_factory=dict, compare=False
    )

    def required_keys_for(self, family: str) -> frozenset[str]:
        return self.required_keys_by_queue.get(family, self.required_keys)


TOP_LEVEL_EVENT_TYPES: dict[str, EventTypeSpec] = {
    "audit_event": EventTypeSpec(frozenset({QUEUE_AUDIT}), frozenset({"data"})),
    "audit_event_flush": EventTypeSpec(frozenset({QUEUE_AUDIT})),
    "notification": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"data"})),
    "schedule_monitor_error": EventTypeSpec(
        frozenset({QUEUE_UI}), frozenset({"legion_id", "schedule_id", "error"})
    ),
    "schedule_updated": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"schedule", "deleted"})),
    "schedule_execution": EventTypeSpec(
        frozenset({QUEUE_UI}), frozenset({"execution", "schedule_id"})
    ),
    "project_updated": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"data"})),
    "project_deleted": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"data"})),
    "session_deleted": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"data"})),
    "state_change": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"data"})),
    "server_restarting": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"message"})),  # orphan, 2b
    "mcp_oauth_complete": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"server_id"})),
    "secret_oauth_complete": EventTypeSpec(
        frozenset({QUEUE_UI}), frozenset({"flow_id", "success"})
    ),
    "secret_refreshed": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"secret_name"})),
    "secret_refresh_failed": EventTypeSpec(
        frozenset({QUEUE_UI, QUEUE_SESSION}),
        required_keys=frozenset({"secret_name", "error"}),  # web_server.py's flat UI-queue shape
        required_keys_by_queue={QUEUE_SESSION: frozenset({"data"})},  # routers/secrets.py's wrapped shape
    ),
    "mcp_oauth_refreshed": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"server_id"})),
    "rate_limits_update": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"data"})),
    "session_reset": EventTypeSpec(frozenset({QUEUE_UI}), frozenset({"data"})),
    "session_watchdog_alert": EventTypeSpec(
        frozenset({QUEUE_UI}), frozenset({"session_id", "watchdog", "details"})
    ),
    # required_keys={"data"} matches these two orphan types' sole producer
    # (legion_mcp_tools.py's broadcast_ui_event() calls, which nest their fields
    # under "data") — found via emit()'s registry validation during 2a-B; previously
    # invisible since raw .append() never checked shape (issue #2063).
    "session_self_restart": EventTypeSpec(
        frozenset({QUEUE_UI}), frozenset({"data"})
    ),  # orphan, 2b
    "session_restart_error": EventTypeSpec(
        frozenset({QUEUE_UI}), frozenset({"data"})
    ),
    "resource_registered": EventTypeSpec(frozenset({QUEUE_SESSION}), frozenset({"resource"})),
    "link_registered": EventTypeSpec(frozenset({QUEUE_SESSION}), frozenset({"link"})),
    "queue_update": EventTypeSpec(frozenset({QUEUE_SESSION}), frozenset({"action", "item"})),
    "usage_updated": EventTypeSpec(frozenset({QUEUE_SESSION}), frozenset({"session_id", "usage"})),
    "assistant_delta": EventTypeSpec(
        frozenset({QUEUE_SESSION}), frozenset({"session_id", "data"})
    ),
    "message": EventTypeSpec(frozenset({QUEUE_SESSION}), frozenset({"session_id", "data"})),
    "context_update": EventTypeSpec(
        frozenset({QUEUE_SESSION}), frozenset({"input_tokens", "context_window", "context_pct"})
    ),
    "tool_call": EventTypeSpec(frozenset({QUEUE_SESSION}), frozenset({"session_id", "data"})),
    "resource_removed": EventTypeSpec(frozenset({QUEUE_SESSION}), frozenset({"resource_id"})),
    "proxy_event": EventTypeSpec(
        frozenset({QUEUE_SESSION}), frozenset({"data"})
    ),  # the arbitrary-type default; AC4 pins/validates this at the HTTP boundary in 2a-B
}

# Nested within `message`'s `data.type` (MessageType enum, backend/message_parser.py) + "tool_call"
MESSAGE_DATA_TYPES: frozenset[str] = frozenset(
    {
        "system",
        "assistant",
        "user",
        "result",
        "tool_use",
        "tool_result",
        "tool_error",
        "permission_request",
        "permission_response",
        "thinking",
        "session_start",
        "session_end",
        "status_update",
        "processing",
        "error",
        "warning",
        "exception",
        "unknown",
        "tool_call",
    }
)  # 18 MessageType values + tool_call = 19

# `system`-typed messages' `subtype` field
SYSTEM_SUBTYPES: frozenset[str] = frozenset(
    {
        "client_launched",
        "interrupt",
        "mcp_server_degraded",
        "session_failed",
        "stderr",
        "task_notification",
        "task_progress",
        "task_started",
        "task_updated",
        "unknown",
        "local_command_response",
        "agent_notification",
        "api_retry",
        "permission_mode_change",
        "replay_complete",  # mock-SDK fixture-replay-finished marker (backend/mock_sdk.py)
    }
)

# issue #310 DisplayProjection — present on `message`/`tool_call` data as an optional nested
# dict (`tool_states`, etc.); not deep-validated here, just recorded as a known optional key
# so the completeness test doesn't flag it as an unexpected field.
KNOWN_OPTIONAL_DATA_KEYS: frozenset[str] = frozenset({"display"})


def export_json() -> dict:
    """Flattened, JSON-serializable form of the registry for 2b's frontend test (AC8)."""
    return {
        "top_level_event_types": {
            event_type: {
                "queues": sorted(spec.queues),
                "required_keys": sorted(spec.required_keys),
            }
            for event_type, spec in TOP_LEVEL_EVENT_TYPES.items()
        },
        "message_data_types": sorted(MESSAGE_DATA_TYPES),
        "system_subtypes": sorted(SYSTEM_SUBTYPES),
        "known_optional_data_keys": sorted(KNOWN_OPTIONAL_DATA_KEYS),
    }
