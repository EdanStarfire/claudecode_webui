"""AC1 (issue #2189, epic #1990 stage 7 coordinator-split, PR 11 FINAL): an
allowlist-with-per-entry-ceiling AST test over backend/session_coordinator.py.

session_coordinator.py is not purely construction-and-delegation by design — this
stage's own earlier PRs deliberately kept a real-logic residual in place (session
lifecycle operations, the state-change notify/relay cluster, PR 7's template-
override cluster). A bare whole-file line-count ceiling would be too brittle (any
future one-line delegation addition anywhere nudges the total), and a zero-
tolerance scan (AC4's router-boundary pattern, test_issue_2189_router_boundary.py)
doesn't fit a file that legitimately still carries real logic. Instead, modeled
directly on test_issue_2159_predicate_consistency.py's ALLOWLIST-with-inverse-
check pattern: every method/module-level function gets a per-name ceiling pinned
to its CURRENT size. Anything NOT in ALLOWLIST whose body exceeds DELEGATE_CEILING
(10 lines — comfortably enough for a thin pass-through to one of the 9 extracted
modules) fails, forcing a reviewed allowlist addition for any genuinely new
non-delegating logic. An inverse check confirms every allowlisted name still
exists (catches a stale entry left behind by a rename/removal).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COORDINATOR_PATH = REPO_ROOT / "backend" / "session_coordinator.py"

# A thin delegating method/function (pass args through to an extracted module,
# maybe with a one-line docstring) fits comfortably within this many lines.
# Anything longer either belongs in ALLOWLIST (reviewed, pinned to its current
# size) or should be delegated to one of the 9 extracted modules instead.
DELEGATE_CEILING = 10

# name -> ceiling (body lines, lineno..end_lineno inclusive), pinned to the
# CURRENT size confirmed by direct file read during the issue #2189 PR 11
# investigation. Do NOT raise or lower these as a drive-by "improvement" —
# they're deliberately permissive of this stage's own acknowledged residual
# (see PR 11's plan, "Current State" section). A reviewer bumping a ceiling
# should be reacting to a real, reviewed change to that function, not
# smoothing over an unrelated refactor.
ALLOWLIST: dict[str, int] = {
    # --- Construction ---
    # Bumped 375->378 (issue #2193 PR 3): added self.pending_store = PendingStore(),
    # pure instantiation alongside the existing queue_manager/delivery_gate wiring.
    "__init__": 378,
    # --- Orchestration ---
    "initialize": 69,
    "cleanup": 23,
    # --- Acknowledged "real logic" residual kept on the coordinator by
    # design: session lifecycle operations, the state-change notify/relay
    # cluster, callback-factory setters, tool-call tracking entry points. ---
    "create_session": 76,
    "create_ephemeral_session": 66,
    "delete_session": 208,
    "send_message": 64,
    "interrupt_session": 63,
    "set_permission_mode": 55,
    "set_model": 55,
    "add_directory": 46,
    "get_session_messages": 47,
    "get_or_create_storage_manager": 33,
    "_validate_and_cleanup_projects": 74,
    "_initialize_existing_session_storage": 20,
    "set_audit_writer": 17,
    "set_permission_callback_factory": 17,
    "set_message_callback_registrar": 17,
    "update_session_name": 13,
    # Bumped 13->56 (issue #2193 PR 6): enqueue_message() stopped delegating to
    # queue_processor.py and now builds+delivers a Comm through pending_store/
    # delivery_gate directly (the regression-fix site for the inject_timestamp
    # heuristic — see delivery_gate.py's _should_inject_timestamp() docstring).
    "enqueue_message": 56,
    "get_session_resources": 12,
    "get_mcp_status": 12,
    "get_archive_resources": 13,
    # Added (issue #2193 PR 6): pause_queue() now re-starts delivery_gate's
    # when_idle loop itself when unpausing a session with a WHEN_IDLE item
    # pending, instead of delegating that decision to queue_processor.py.
    "pause_queue": 11,
    "create_tool_call": 19,
    "update_tool_call_permission_request": 11,
    "update_tool_call_permission_response": 11,
    "update_tool_call_result": 17,
    "_build_permission_handler": 21,
    "_send_session_failure_message": 20,
    "_check_sdk_generated_name": 24,
    "_notify_state_change": 20,
    "_notify_metadata_change": 13,
    "_notify_processing_change": 13,
    "_on_session_manager_lifecycle_record": 18,
    # --- PR 7's template-override cluster (deliberately kept here rather
    # than relocated to a 4th extracted module this late in the stage) ---
    "_init_session_overrides": 40,
    "_prune_inherited_mcp_snapshot": 31,
    "_migrate_stale_mcp_snapshots": 16,
    # --- Module-level helper functions: real logic that sits outside the
    # class but is late-imported back by message_pipeline.py/session_launcher.py ---
    "_normalize_result_usage": 38,
    "_delta_from_baseline": 47,
    "_accumulate_subagent_usage": 28,
    "_render_inject_file": 38,
    "_all_tier_fields_set": 8,
    "_build_model_map": 8,
    "_resolve_analytics_model_label": 19,
    "_nested_metadata": 18,
    "_extract_send_comm_sender_attachments": 19,
}


def _parse_coordinator() -> ast.Module:
    return ast.parse(COORDINATOR_PATH.read_text())


def _function_defs(tree: ast.Module):
    """Yield (name, body_len) for every module-level function and every
    SessionCoordinator method. body_len is end_lineno - lineno + 1 (the AST
    span of the def, including its own signature/docstring/body lines)."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node.name, node.end_lineno - node.lineno + 1
        if isinstance(node, ast.ClassDef) and node.name == "SessionCoordinator":
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    yield item.name, item.end_lineno - item.lineno + 1


class TestAC1CoordinatorResponsibility:
    """Generic regression guard: any method/module-level function in
    session_coordinator.py exceeding the thin-delegate ceiling must be an
    explicit, reviewed ALLOWLIST entry — not silently-grown non-delegating
    logic."""

    def test_no_unallowlisted_function_exceeds_delegate_ceiling(self):
        violations: list[str] = []
        for name, length in _function_defs(_parse_coordinator()):
            ceiling = ALLOWLIST.get(name, DELEGATE_CEILING)
            if length > ceiling:
                violations.append(f"{name}: {length} lines (ceiling {ceiling})")
        assert not violations, (
            "Non-delegating method/function exceeds its line ceiling in "
            "session_coordinator.py — either delegate it to one of the 9 "
            "extracted modules, or add a reviewed ALLOWLIST entry pinned to "
            "its current size:\n" + "\n".join(sorted(violations))
        )

    def test_allowlist_entries_still_exist(self):
        """The inverse check: every ALLOWLIST entry must correspond to a
        function/method that still exists — an entry left behind by a
        rename/removal would silently exempt nothing, hiding whatever it was
        meant to cover."""
        found = {name for name, _ in _function_defs(_parse_coordinator())}
        stale = sorted(set(ALLOWLIST) - found)
        assert not stale, (
            "Stale ALLOWLIST entries (no function/method with this name "
            "exists in session_coordinator.py anymore):\n" + "\n".join(stale)
        )
