"""AST-scan guard (issue #2063 AC2, T2): every EventQueue write must route through
shared/event_emitter.py's emit(), mirroring src/tests/test_import_boundary.py's
AST-scan + self-test pattern."""

import ast
import logging
import tempfile
from pathlib import Path

import pytest

from shared.event_emitter import EventRegistryViolationError, emit, strict_mode
from shared.event_envelope import FRONTEND_LOCAL_BACKEND_ID, QUEUE_UI, EventEnvelope
from shared.event_queue import EventQueue

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = [REPO_ROOT / "backend", REPO_ROOT / "src"]
ALLOWED_DIRECT_APPEND_FILES = {
    REPO_ROOT / "shared" / "event_emitter.py",
    REPO_ROOT / "src" / "poll_relay.py",  # AC2's documented opaque-relay exception
    # backend/queue_manager.py's `queue.append(item)` operates on a plain
    # list[QueueItem] (its own in-memory FIFO), not an EventQueue — matches the
    # QUEUE_NAME_HINT heuristic by name coincidence only.
    REPO_ROOT / "backend" / "queue_manager.py",
}
QUEUE_NAME_HINT = "queue"  # matches ui_queue/_ui_queue/audit_queue/session_queues[...]/queue_for(...)


def _find_direct_queue_appends(root: Path) -> list[str]:
    violations = []
    for path in root.rglob("*.py"):
        if path in ALLOWED_DIRECT_APPEND_FILES or "/tests/" in str(path):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
            ):
                receiver_name = ast.unparse(node.func.value).lower()
                if QUEUE_NAME_HINT in receiver_name:
                    violations.append(f"{path}:{node.lineno}: {ast.unparse(node)}")
    return violations


def test_no_direct_queue_appends_outside_the_emitter():
    violations = [v for root in SCAN_ROOTS for v in _find_direct_queue_appends(root)]
    assert not violations, (
        "Found .append( call(s) on an EventQueue-like reference outside "
        "shared/event_emitter.py. Route through emit() instead:\n" + "\n".join(violations)
    )


def test_scanner_detects_a_real_violation():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        offender = tmp_root / "offender.py"
        offender.write_text(
            'def handler(ui_queue):\n'
            '    ui_queue.append({"type": "whatever"})\n'
        )
        violations = _find_direct_queue_appends(tmp_root)
        assert len(violations) == 1
        assert "offender.py" in violations[0]


def test_session_self_restart_and_restart_error_payloads_validate_cleanly():
    """Regression guard: this migration's registry validation caught
    legion_mcp_tools.py's session_self_restart/session_restart_error broadcasts
    (routed through legion_system.py's broadcast_ui_event -> emit()) nesting
    their fields under "data", which didn't match the registry's required_keys
    until that was found and fixed (issue #2063). Exercises the actual payload
    shape each producer sends, in strict mode, so a future regression raises
    immediately instead of silently logging."""
    queue = EventQueue()
    with strict_mode():
        emit(queue, QUEUE_UI, "session_self_restart", {
            "data": {"session_id": "s", "restart_id": "r", "reason": None, "timestamp": "t"},
        })
        emit(queue, QUEUE_UI, "session_restart_error", {
            "data": {"session_id": "s", "restart_id": "r", "error": "e", "timestamp": "t"},
        })


def test_emit_produces_distinct_event_ids_for_successive_calls():
    """AC3: two emit() calls on the same queue must never collide on event_id."""
    queue = EventQueue()
    emit(queue, QUEUE_UI, "state_change", {"data": {"session_id": "s"}})
    emit(queue, QUEUE_UI, "state_change", {"data": {"session_id": "s"}})

    events, _, _ = queue.events_since(0)
    assert events[0]["event_id"] != events[1]["event_id"]


def test_redelivery_of_the_same_envelope_reconstructs_the_same_event_id():
    """Simulates redelivery: parsing the same already-emitted dict twice via
    EventEnvelope.from_dict() must produce the same event_id both times."""
    queue = EventQueue()
    emit(queue, QUEUE_UI, "state_change", {"data": {"session_id": "s"}})
    events, _, _ = queue.events_since(0)
    delivered = events[0]

    first = EventEnvelope.from_dict(delivered)
    second = EventEnvelope.from_dict(delivered)
    assert first.event_id == second.event_id


def test_emit_returned_cursor_equals_the_precomputed_sequence():
    """Design decision #1: emit() precomputes `sequence` from current_cursor before
    append() runs, rather than patching it on afterward. The cursor append() hands
    back must always equal that precomputed value for the auto-increment discipline."""
    queue = EventQueue()
    cursor = emit(queue, QUEUE_UI, "state_change", {"data": {"session_id": "s"}})

    events, _, _ = queue.events_since(0)
    assert events[0]["sequence"] == cursor


def test_on_append_hook_receives_sequence_already_populated():
    """Regression guard for the ordering bug precomputation avoids: the #1998 session
    recorder's on_append hook fires synchronously inside append() — if sequence were
    patched onto the event dict after append() returns, the hook would see it missing."""
    seen = []
    queue = EventQueue(on_append=lambda event: seen.append(dict(event)))
    emit(queue, QUEUE_UI, "state_change", {"data": {"session_id": "s"}})

    assert seen[0]["sequence"] == 1


def test_frontend_local_backend_id_prevents_event_id_collision():
    """A Frontend-local write (src/routers/system.py's server_restarting) and a
    Backend-originated write relayed onto the same local ui_queue can land on
    colliding sequence numbers across their two distinct source queues — distinct
    backend_id values keep their event_ids distinct even then."""
    frontend_local_queue = EventQueue()
    backend_queue = EventQueue()

    emit(
        frontend_local_queue, QUEUE_UI, "server_restarting", {"message": "restarting"},
        backend_id=FRONTEND_LOCAL_BACKEND_ID,
    )
    emit(backend_queue, QUEUE_UI, "server_restarting", {"message": "restarting"})

    frontend_events, _, _ = frontend_local_queue.events_since(0)
    backend_events, _, _ = backend_queue.events_since(0)
    assert frontend_events[0]["sequence"] == backend_events[0]["sequence"]
    assert frontend_events[0]["event_id"] != backend_events[0]["event_id"]


def test_strict_mode_raises_on_unregistered_event_type():
    queue = EventQueue()
    with strict_mode():
        with pytest.raises(EventRegistryViolationError, match="unregistered event type"):
            emit(queue, QUEUE_UI, "totally_made_up_type", {})


def test_strict_mode_raises_on_missing_required_key():
    queue = EventQueue()
    with strict_mode():
        # state_change is registered for QUEUE_UI with required_keys={"data"}.
        with pytest.raises(EventRegistryViolationError, match="missing required key"):
            emit(queue, QUEUE_UI, "state_change", {})


def test_lenient_mode_logs_and_still_appends_on_registry_violation(caplog):
    queue = EventQueue()
    with caplog.at_level(logging.ERROR):
        emit(queue, QUEUE_UI, "totally_made_up_type", {"foo": "bar"})

    assert "unregistered event type" in caplog.text
    events, _, _ = queue.events_since(0)
    assert len(events) == 1
    assert events[0]["type"] == "totally_made_up_type"
