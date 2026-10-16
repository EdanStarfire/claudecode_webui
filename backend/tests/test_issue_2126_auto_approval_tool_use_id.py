"""
Regression tests for issue #2126.

AC1: `_create_auto_approval_callback`'s body resolves the ToolCall by tool_use_id
only — no name+status signature-matching fallback remains.
AC2: two concurrent PENDING tool calls sharing the same name resolve to the
correct ToolCall by tool_use_id (not the first name+status match in dict
iteration order).
Defensive: a missing tool_use_id is a no-op, not a crash.
"""

from __future__ import annotations

import inspect

import pytest

from backend.models.messages import ToolState


def test_auto_approval_callback_has_no_name_based_matching():
    """AC1: no `tool_call.name ==` (or equivalent name-based) comparison remains
    in the auto-approval callback's source.

    Issue #2189 (coordinator-split PR 3): _create_auto_approval_callback's real
    body now lives on ToolCallTracker — SessionCoordinator keeps only a one-line
    delegate. Asserting against the delegate's source would trivially pass
    without exercising any real logic, silently erasing this regression test's
    coverage with no visible failure signal. Retargeted to the class that
    actually owns the logic under test.
    """
    from backend.tool_call_tracker import ToolCallTracker

    source = inspect.getsource(ToolCallTracker._create_auto_approval_callback)
    assert "tool_call.name ==" not in source
    assert ".name == tool_name" not in source


@pytest.mark.asyncio
async def test_auto_approval_resolves_concurrent_same_name_tools_by_tool_use_id(tmp_path):
    """AC2: the actual bug scenario — two concurrent PENDING tools with the same
    name must each be auto-approved independently, keyed by tool_use_id."""
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "sess-2126-concurrent"

    coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-001",
        name="Bash",
        input_params={"command": "echo one"},
    )
    coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-002",
        name="Bash",
        input_params={"command": "echo two"},
    )

    callback = coord._create_auto_approval_callback(session_id)
    callback("Bash", {"command": "echo two"}, "matched internal rule", "tu-002")

    tu_001 = coord.get_tool_call_by_id(session_id, "tu-001")
    tu_002 = coord.get_tool_call_by_id(session_id, "tu-002")

    assert tu_002.auto_approved_reason == "matched internal rule"
    assert tu_001.auto_approved_reason is None

    # Reverse mapping: approving tu-001 afterwards must not touch tu-002 again.
    callback("Bash", {"command": "echo one"}, "matched internal rule", "tu-001")
    assert coord.get_tool_call_by_id(session_id, "tu-001").auto_approved_reason == (
        "matched internal rule"
    )
    assert coord.get_tool_call_by_id(session_id, "tu-002").status == ToolState.PENDING


@pytest.mark.asyncio
async def test_auto_approval_callback_none_tool_use_id_is_noop(tmp_path):
    """Defensive: the SDK contract guarantees tool_use_id is always a non-empty
    string when delivered, but the field is Optional — a None must not raise
    and must not mutate any tracked ToolCall."""
    from backend.session_coordinator import SessionCoordinator

    coord = SessionCoordinator(data_dir=tmp_path)
    session_id = "sess-2126-none-tool-use-id"

    coord.create_tool_call(
        session_id=session_id,
        tool_use_id="tu-001",
        name="Bash",
        input_params={"command": "echo one"},
    )

    callback = coord._create_auto_approval_callback(session_id)
    callback("Bash", {"command": "echo one"}, "matched internal rule", None)

    assert coord.get_tool_call_by_id(session_id, "tu-001").auto_approved_reason is None
