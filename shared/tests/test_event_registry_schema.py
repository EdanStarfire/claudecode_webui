"""Structural checks on shared/event_registry.py itself (issue #2063)."""

from shared.event_envelope import QUEUE_AUDIT, QUEUE_SESSION, QUEUE_UI
from shared.event_registry import (
    KNOWN_OPTIONAL_DATA_KEYS,
    MESSAGE_DATA_TYPES,
    SYSTEM_SUBTYPES,
    TOP_LEVEL_EVENT_TYPES,
    export_json,
)

VALID_QUEUES = {QUEUE_UI, QUEUE_SESSION, QUEUE_AUDIT}


def test_every_spec_has_a_non_empty_subset_of_valid_queues():
    for event_type, spec in TOP_LEVEL_EVENT_TYPES.items():
        assert spec.queues, f"{event_type}'s queues must not be empty"
        assert spec.queues <= VALID_QUEUES, f"{event_type} has unrecognized queue(s): {spec.queues}"


def test_queues_and_required_keys_are_frozensets():
    for event_type, spec in TOP_LEVEL_EVENT_TYPES.items():
        assert isinstance(spec.queues, frozenset), f"{event_type}.queues must be a frozenset"
        assert isinstance(
            spec.required_keys, frozenset
        ), f"{event_type}.required_keys must be a frozenset"


def test_secret_refresh_failed_is_registered_on_both_queues():
    # Legitimately produced on both the ui queue (backend-originated) and the session queue
    # (via the Docker proxy addon's unvalidated-type bug, AC4) — see issue #2063's audit.
    spec = TOP_LEVEL_EVENT_TYPES["secret_refresh_failed"]
    assert spec.queues == frozenset({QUEUE_UI, QUEUE_SESSION})


def test_sessions_list_is_deliberately_not_registered():
    # AC7: nothing on the server produces it — it's a dead case in polling.js, resolved by
    # removing the frontend case in 2a-B, not by registering a phantom type here.
    assert "sessions_list" not in TOP_LEVEL_EVENT_TYPES


def test_export_json_round_trips():
    exported = export_json()

    assert set(exported["top_level_event_types"].keys()) == set(TOP_LEVEL_EVENT_TYPES.keys())
    for event_type, spec in TOP_LEVEL_EVENT_TYPES.items():
        exported_spec = exported["top_level_event_types"][event_type]
        assert exported_spec["queues"] == sorted(spec.queues)
        assert exported_spec["required_keys"] == sorted(spec.required_keys)

    assert exported["message_data_types"] == sorted(MESSAGE_DATA_TYPES)
    assert exported["system_subtypes"] == sorted(SYSTEM_SUBTYPES)
    assert exported["known_optional_data_keys"] == sorted(KNOWN_OPTIONAL_DATA_KEYS)


def test_export_json_is_json_serializable():
    import json

    json.dumps(export_json())
