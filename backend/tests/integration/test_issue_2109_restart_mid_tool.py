"""Regression test for issue #2109 (AC8/4a-F).

A historical recording's restart_session()/interrupt_session()/terminate_session()
lifecycle event is captured in raw_log.jsonl as a "lifecycle"/action in ("restart",
"terminate") or bare "interrupt" kind record — never as an sdk_message, since those
are WebUI-internal events, not real claude_agent_sdk traffic (see backend/mock_sdk.py's
RawFixtureReplay.interrupt_mark_indices). Before this stage, mock-SDK raw-layer replay
(_start_raw_replay) silently dropped these records, so a tool left open across a
historical restart/interrupt/termination never got a terminal record on replay —
reproducing AC1's production fix (mark_session_tools_interrupted()) only for sessions
created after that fix shipped, never for fixtures recorded before it. This test proves
the gap is closed for all three lifecycle kinds that actually call
mark_session_tools_interrupted() in production and have a corresponding raw_log.jsonl
capture point.
"""

import asyncio
import shutil

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, ToolUseBlock

from backend.session_recorder import SessionRecorder

# api_integration_env is auto-discovered from this directory's own conftest.py —
# no import needed (unlike backend/tests/test_equivalence_replay_generation.py,
# which lives outside backend/tests/integration/ and needs the module-attribute
# workaround its own docstring explains).

_TOOL_USE_ID = "toolu_restart_mid_tool_test"


def _build_fixture(fixture_dir, *, kind: str, action: str | None) -> None:
    """A Bash tool_use left open when the recording's restart_session()/
    interrupt_session()/terminate_session() fired — the same historical shape
    2026-09-23-primary's real capture has, at a fraction of the size."""
    recorder = SessionRecorder("sess-1", fixture_dir)
    recorder.record_lifecycle("start")
    recorder.record_sdk_message(SystemMessage(subtype="init", data={"session_id": "claude-sess-1"}))
    recorder.record_sdk_message(
        AssistantMessage(
            content=[ToolUseBlock(id=_TOOL_USE_ID, name="Bash", input={"command": "sleep 30"})],
            model="claude-sonnet-4-5",
            session_id="claude-sess-1",
        )
    )
    # The real restart_session()/interrupt_session()/terminate_session() call that left
    # the Bash tool above open — never resolved by any further sdk_message in this
    # recording.
    if kind == "lifecycle":
        recorder.record_lifecycle(action)
    else:
        recorder.record_interrupt()
    recorder.record_sdk_message(SystemMessage(subtype="init", data={"session_id": "claude-sess-1b"}))
    recorder.record_sdk_message(
        ResultMessage(
            subtype="success", duration_ms=10, duration_api_ms=8,
            is_error=False, num_turns=1, session_id="claude-sess-1b",
        )
    )


async def _poll_for_tool_call_records(client, session_id, tool_use_id, timeout=5.0):
    """ToolCallUpdate storage writes (SessionCoordinator._schedule_tool_call_update_
    storage()) are fire-and-forget (asyncio.ensure_future(), never awaited by the
    caller — see its own docstring), so a stored terminal record isn't guaranteed to
    be flushed the instant /start's response returns. Poll briefly instead of reading
    once, mirroring test_api_sessions_lifecycle.py's _wait_for_state() pattern for the
    same class of eventual-consistency wait."""
    deadline = asyncio.get_event_loop().time() + timeout
    records = []
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(
            f"/api/sessions/{session_id}/messages", params={"limit": 1000, "offset": 0}
        )
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        records = [
            m for m in messages
            if m.get("type") == "tool_call" and m.get("tool_use_id") == tool_use_id
        ]
        if records and records[-1]["status"] == "interrupted":
            return records
        await asyncio.sleep(0.05)
    return records


@pytest.mark.parametrize(
    "fixture_name,kind,action",
    [
        ("_issue_2109_restart_mid_tool", "lifecycle", "restart"),
        ("_issue_2109_terminate_mid_tool", "lifecycle", "terminate"),
        ("_issue_2109_interrupt_mid_tool", "interrupt", None),
    ],
)
async def test_mid_tool_lifecycle_event_produces_interrupted_record(
    api_integration_env, fixture_name, kind, action
):
    fixtures_dir = api_integration_env["fixtures_dir"]
    fixture_dir = fixtures_dir / fixture_name
    fixture_dir.mkdir(parents=True, exist_ok=True)
    try:
        _build_fixture(fixture_dir, kind=kind, action=action)

        client = api_integration_env["client"]
        create_test_project = api_integration_env["create_test_project"]
        create_test_session = api_integration_env["create_test_session"]

        project = await create_test_project(name=f"Issue 2109 {fixture_name}")
        session = await create_test_session(project["project_id"], name=fixture_name)
        session_id = session["session_id"]

        resp = await client.post(f"/api/sessions/{session_id}/start")
        assert resp.status_code == 200, f"start session failed: {resp.text}"

        tool_call_records = await _poll_for_tool_call_records(client, session_id, _TOOL_USE_ID)
        assert tool_call_records, (
            f"Expected at least one stored tool_call record for {_TOOL_USE_ID!r} — got none."
        )
        assert tool_call_records[-1]["status"] == "interrupted", (
            f"Tool {_TOOL_USE_ID!r} left open across the recorded {kind}/{action} event "
            f"should have a terminal 'interrupted' record; final stored status was "
            f"{tool_call_records[-1]['status']!r}."
        )
    finally:
        shutil.rmtree(fixture_dir, ignore_errors=True)


# ============================================================
# Issue #2111: genuine process-crash repair (no lifecycle marker at all)
# ============================================================
#
# None of restart_session()/interrupt_session()/terminate_session()/the critical-error
# callback ever runs for a genuine Backend process crash — unlike every case above,
# there is no lifecycle/interrupt marker to replay, because the real process never got
# the chance to emit one. This fixture reproduces that: the raw_log.jsonl just stops
# right after the tool_use, with nothing else recorded. The repair happens on the next
# real SessionCoordinator.initialize() boot, not during fixture replay.

_CRASH_TOOL_USE_ID = "toolu_crash_mid_tool_test"


def _build_crash_fixture(fixture_dir) -> None:
    """A Bash tool_use left open with no further recording at all — the raw_log.jsonl
    just stops, exactly like a real Backend process crash mid-tool."""
    recorder = SessionRecorder("sess-1", fixture_dir)
    recorder.record_lifecycle("start")
    recorder.record_sdk_message(SystemMessage(subtype="init", data={"session_id": "claude-sess-1"}))
    recorder.record_sdk_message(
        AssistantMessage(
            content=[ToolUseBlock(id=_CRASH_TOOL_USE_ID, name="Bash", input={"command": "sleep 30"})],
            model="claude-sonnet-4-5",
            session_id="claude-sess-1",
        )
    )


async def _poll_for_any_tool_call_record(client, session_id, tool_use_id, timeout=5.0):
    """Poll until the (fire-and-forget) ToolCallUpdate storage write for the open tool
    lands on disk — same eventual-consistency wait as _poll_for_tool_call_records above,
    but here we're waiting for the initial pending/running record, not a resolution."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(
            f"/api/sessions/{session_id}/messages", params={"limit": 1000, "offset": 0}
        )
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        records = [
            m for m in messages
            if m.get("type") == "tool_call" and m.get("tool_use_id") == tool_use_id
        ]
        if records:
            return records
        await asyncio.sleep(0.05)
    return []


async def test_crash_with_no_lifecycle_marker_repaired_on_next_boot(api_integration_env):
    """A tool left open by a real process crash (no lifecycle/interrupt marker anywhere
    in the recording) has no terminal record after replay — it only gets repaired on
    the next real SessionCoordinator.initialize() boot against the same on-disk data."""
    from backend.session_coordinator import SessionCoordinator

    fixtures_dir = api_integration_env["fixtures_dir"]
    fixture_name = "_issue_2111_crash_mid_tool"
    fixture_dir = fixtures_dir / fixture_name
    fixture_dir.mkdir(parents=True, exist_ok=True)
    try:
        _build_crash_fixture(fixture_dir)

        client = api_integration_env["client"]
        create_test_project = api_integration_env["create_test_project"]
        create_test_session = api_integration_env["create_test_session"]
        data_dir = api_integration_env["data_dir"]

        project = await create_test_project(name=f"Issue 2111 {fixture_name}")
        session = await create_test_session(project["project_id"], name=fixture_name)
        session_id = session["session_id"]

        resp = await client.post(f"/api/sessions/{session_id}/start")
        assert resp.status_code == 200, f"start session failed: {resp.text}"

        pre_boot_records = await _poll_for_any_tool_call_record(
            client, session_id, _CRASH_TOOL_USE_ID
        )
        assert pre_boot_records, (
            f"Expected a stored (non-terminal) tool_call record for "
            f"{_CRASH_TOOL_USE_ID!r} after replay — got none."
        )
        assert pre_boot_records[-1]["status"] in ("pending", "awaiting_permission", "running"), (
            "Sanity check: a crash fixture with no lifecycle marker must leave the tool "
            f"non-terminal, not {pre_boot_records[-1]['status']!r}."
        )

        # No graceful terminate_session()/interrupt_session()/restart_session() — the
        # process just "crashes" here. A fresh SessionCoordinator boots against the
        # exact same on-disk data, the only thing a real restart can do.
        coordinator2 = SessionCoordinator(data_dir)
        await coordinator2.initialize()
        try:
            result = await coordinator2.get_session_messages(session_id)
            post_boot_records = [
                m for m in result["messages"]
                if m.get("type") == "tool_call" and m.get("tool_use_id") == _CRASH_TOOL_USE_ID
            ]
            assert post_boot_records[-1]["status"] == "interrupted", (
                f"Tool {_CRASH_TOOL_USE_ID!r} left open across a genuine crash (no "
                f"lifecycle marker) should be repaired to a terminal 'interrupted' "
                f"record on the next boot; final stored status was "
                f"{post_boot_records[-1]['status']!r}."
            )
        finally:
            await coordinator2.cleanup()
    finally:
        shutil.rmtree(fixture_dir, ignore_errors=True)
