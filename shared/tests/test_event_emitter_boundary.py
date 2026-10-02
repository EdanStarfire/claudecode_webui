"""AST-scan guard (issue #2063 AC2, T2): every EventQueue write must route through
shared/event_emitter.py's emit(), mirroring src/tests/test_import_boundary.py's
AST-scan + self-test pattern."""

import ast
import tempfile
from pathlib import Path

from shared.event_emitter import emit, strict_mode
from shared.event_envelope import QUEUE_UI
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
