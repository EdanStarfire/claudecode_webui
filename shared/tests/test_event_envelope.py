"""Tests for shared/event_envelope.py (issue #2063)."""

from shared.event_envelope import DEFAULT_BACKEND_ID, QUEUE_SESSION, EventEnvelope
from shared.event_registry import TOP_LEVEL_EVENT_TYPES

# One today's-actual-shape sample per registered type — a bare dict exactly like what the
# real append call sites construct, with no envelope-only fields (queue/sequence/backend_id/
# event_id). Most types put their payload at the top level (sibling to "type"), not nested
# under a "data" key — from_dict() folds those top-level keys into `.data` (see
# test_from_dict_preserves_every_top_level_payload_field_in_data below). `shared/
# poll_protocol.py` is NOT wired to EventEnvelope yet (deferred to the stage that migrates
# `poll_consumer.py`/`fault_harness.py`'s consumers alongside real producers emitting
# envelopes) — this only exercises `from_dict()` in isolation.
SAMPLE_EVENTS_BY_TYPE = {
    "audit_event": {"type": "audit_event", "data": {"alert": "x"}},
    "audit_event_flush": {"type": "audit_event_flush"},
    "notification": {"type": "notification", "data": {"event_type": "minion_comm"}},
    "schedule_monitor_error": {
        "type": "schedule_monitor_error",
        "legion_id": "l1",
        "schedule_id": "s1",
        "error": "boom",
    },
    "schedule_updated": {"type": "schedule_updated", "schedule": {}, "deleted": False},
    "schedule_execution": {"type": "schedule_execution", "execution": {}, "schedule_id": "s1"},
    "project_updated": {"type": "project_updated", "data": {"project": {}}},
    "project_deleted": {"type": "project_deleted", "data": {"project_id": "p1"}},
    "session_deleted": {"type": "session_deleted", "data": {"session_id": "s1"}},
    "state_change": {"type": "state_change", "data": {"session_id": "s1"}},
    "server_restarting": {"type": "server_restarting", "message": "restarting"},
    "mcp_oauth_complete": {"type": "mcp_oauth_complete", "server_id": "srv1"},
    "secret_oauth_complete": {"type": "secret_oauth_complete", "flow_id": "f1", "success": True},
    "secret_refreshed": {"type": "secret_refreshed", "secret_name": "n1"},
    "secret_refresh_failed": {
        "type": "secret_refresh_failed",
        "secret_name": "n1",
        "error": "boom",
    },
    "mcp_oauth_refreshed": {"type": "mcp_oauth_refreshed", "server_id": "srv1"},
    "rate_limits_update": {"type": "rate_limits_update", "data": {}},
    "session_reset": {"type": "session_reset", "data": {"session_id": "s1"}},
    "session_watchdog_alert": {
        "type": "session_watchdog_alert",
        "session_id": "s1",
        "watchdog": "idle",
        "details": {},
    },
    "session_self_restart": {
        "type": "session_self_restart",
        "data": {"session_id": "s1", "restart_id": "r1"},
    },
    "session_restart_error": {
        "type": "session_restart_error",
        "data": {"session_id": "s1", "restart_id": "r1", "error": "boom"},
    },
    "resource_registered": {"type": "resource_registered", "resource": {}},
    "link_registered": {"type": "link_registered", "link": {}},
    "queue_update": {"type": "queue_update", "action": "added", "item": {}},
    "usage_updated": {"type": "usage_updated", "session_id": "s1", "usage": {}},
    "assistant_delta": {"type": "assistant_delta", "session_id": "s1", "data": {}},
    "message": {"type": "message", "session_id": "s1", "data": {"type": "assistant"}},
    "context_update": {
        "type": "context_update",
        "input_tokens": 10,
        "context_window": 200000,
        "context_pct": 0.1,
    },
    "tool_call": {"type": "tool_call", "session_id": "s1", "data": {}},
    "resource_removed": {"type": "resource_removed", "resource_id": "r1"},
    "proxy_event": {"type": "proxy_event", "data": {}},
    "migration_notice": {"type": "migration_notice", "session_id": "s1", "message": "upgraded"},
}


def _make_envelope(**overrides) -> EventEnvelope:
    defaults = dict(
        type="state_change",
        queue=QUEUE_SESSION,
        sequence=5,
        timestamp="2026-10-02T00:00:00+00:00",
        data={"session_id": "abc"},
    )
    defaults.update(overrides)
    return EventEnvelope(**defaults)


def test_to_dict_from_dict_round_trip():
    envelope = _make_envelope(scope="session-123")
    restored = EventEnvelope.from_dict(envelope.to_dict())

    assert restored == envelope


def test_event_id_is_deterministic_for_identical_inputs():
    envelope_a = _make_envelope(scope="session-123")
    envelope_b = _make_envelope(scope="session-123")

    assert envelope_a.event_id == envelope_b.event_id


def test_event_id_varies_with_sequence():
    envelope_a = _make_envelope(sequence=1)
    envelope_b = _make_envelope(sequence=2)

    assert envelope_a.event_id != envelope_b.event_id


def test_event_id_uses_placeholder_when_scope_absent():
    envelope = _make_envelope(scope=None)

    assert envelope.event_id == f"{DEFAULT_BACKEND_ID}:{QUEUE_SESSION}:-:5"


def test_to_dict_omits_scope_key_when_none():
    envelope = _make_envelope(scope=None)

    assert "scope" not in envelope.to_dict()


def test_to_dict_includes_scope_key_when_present():
    envelope = _make_envelope(scope="session-123")

    assert envelope.to_dict()["scope"] == "session-123"


def test_to_dict_includes_computed_event_id():
    envelope = _make_envelope()

    assert envelope.to_dict()["event_id"] == envelope.event_id


def test_from_dict_tolerates_legacy_dict_missing_new_fields():
    # Today's actual append-site shape — no queue/backend_id/sequence/event_id keys.
    legacy = {"type": "state_change", "data": {"session_id": "abc"}}

    envelope = EventEnvelope.from_dict(legacy)

    assert envelope.type == "state_change"
    assert envelope.data == {"session_id": "abc"}
    assert envelope.queue == ""
    assert envelope.sequence == 0
    assert envelope.backend_id == DEFAULT_BACKEND_ID
    assert envelope.scope is None


def test_from_dict_tolerates_empty_dict():
    envelope = EventEnvelope.from_dict({})

    assert envelope.type == ""
    assert envelope.data == {}


def test_from_dict_defaults_missing_data_to_empty_dict():
    envelope = EventEnvelope.from_dict({"type": "audit_event_flush"})

    assert envelope.data == {}


def test_sample_fixtures_cover_every_registered_type():
    assert set(SAMPLE_EVENTS_BY_TYPE.keys()) == set(TOP_LEVEL_EVENT_TYPES.keys())


def test_from_dict_parses_every_registered_type_without_raising():
    for event_type, sample in SAMPLE_EVENTS_BY_TYPE.items():
        envelope = EventEnvelope.from_dict(sample)

        assert envelope.type == event_type


def test_from_dict_preserves_every_top_level_payload_field_in_data():
    # Most registered types (e.g. schedule_monitor_error, mcp_oauth_complete) carry their
    # payload as flat top-level keys, not nested under "data" — from_dict() must fold those
    # into envelope.data rather than silently dropping them.
    for sample in SAMPLE_EVENTS_BY_TYPE.values():
        envelope = EventEnvelope.from_dict(sample)

        expected_data = {k: v for k, v in sample.items() if k != "type"}
        if "data" in expected_data:
            expected_data.update(expected_data.pop("data"))

        assert envelope.data == expected_data


def test_from_dict_does_not_leak_envelope_fields_into_data():
    raw = {
        "type": "state_change",
        "queue": "ui",
        "sequence": 3,
        "timestamp": "2026-10-02T00:00:00+00:00",
        "backend_id": "local",
        "scope": "session-123",
        "data": {"session_id": "abc"},
    }

    envelope = EventEnvelope.from_dict(raw)

    assert envelope.data == {"session_id": "abc"}
