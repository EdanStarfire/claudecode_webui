"""Issue #2293: Restart Agent works from ERROR and TERMINATED (not just ACTIVE)."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from backend.session_lifecycle import RESTARTABLE_STATES, is_restartable
from backend.session_manager import SessionState

pytestmark = pytest.mark.asyncio


async def _new_session(env):
    project = await env["create_test_project"]("Restart Project")
    session = await env["create_test_session"](project["project_id"], "single_turn")
    return session["session_id"]


async def _wait_for_state(client, sid, target, timeout=10):
    for _ in range(int(timeout / 0.1)):
        resp = await client.get(f"/api/sessions/{sid}")
        if resp.json()["session"]["state"] == target:
            return True
        await asyncio.sleep(0.1)
    return False


def test_issue_2293_restartable_states():
    assert {s for s in SessionState if is_restartable(s)} == {
        SessionState.ACTIVE,
        SessionState.ERROR,
        SessionState.TERMINATED,
    }
    assert RESTARTABLE_STATES == frozenset(
        {SessionState.ACTIVE, SessionState.ERROR, SessionState.TERMINATED}
    )


@pytest.mark.parametrize(
    "state",
    [SessionState.CREATED, SessionState.PAUSED, SessionState.STARTING, SessionState.TERMINATING],
)
async def test_issue_2293_non_restartable_states_rejected(api_integration_env, state):
    env = api_integration_env
    sid = await _new_session(env)
    sm = env["coordinator"].session_manager
    await sm._update_session_state(sid, state)
    launcher = env["coordinator"]._session_launcher
    launcher.start_session = AsyncMock(return_value=True)

    resp = await env["client"].post(f"/api/sessions/{sid}/restart")

    assert resp.status_code == 409
    assert state.value in resp.json()["detail"]
    launcher.start_session.assert_not_called()
    # The launcher guard holds independent of the endpoint
    assert await env["coordinator"].restart_session(sid) is False
    launcher.start_session.assert_not_called()


async def test_issue_2293_restart_from_error_clears_error_fields(api_integration_env):
    env = api_integration_env
    client = env["client"]
    sid = await _new_session(env)
    sm = env["coordinator"].session_manager
    await sm._update_session_state(
        sid,
        SessionState.ERROR,
        "old failure",
        error_subtype="sub",
        error_terminal_reason="term",
        error_api_error_status=500,
        error_list=["a"],
    )

    resp = await client.post(f"/api/sessions/{sid}/restart")

    assert resp.status_code == 200
    assert resp.json() == {"success": True}
    assert await _wait_for_state(client, sid, "active")
    info = await sm.get_session_info(sid)
    assert info.error_message is None
    assert info.error_subtype is None
    assert info.error_terminal_reason is None
    assert info.error_api_error_status is None
    assert info.error_list is None
    persisted = json.loads((sm.sessions_dir / sid / "state.json").read_text())
    assert persisted["error_message"] is None
    assert persisted["error_list"] is None


async def test_issue_2293_restart_from_terminated_preserves_resume_id(api_integration_env):
    env = api_integration_env
    client = env["client"]
    sid = await _new_session(env)
    sm = env["coordinator"].session_manager
    await sm.update_session(sid, claude_code_session_id="resume-me")
    await sm._update_session_state(sid, SessionState.TERMINATED)

    resp = await client.post(f"/api/sessions/{sid}/restart")

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert await _wait_for_state(client, sid, "active")
    assert (await sm.get_session_info(sid)).claude_code_session_id == "resume-me"


async def test_issue_2293_failed_restart_reports_new_error(api_integration_env):
    env = api_integration_env
    sid = await _new_session(env)
    coordinator = env["coordinator"]
    sm = coordinator.session_manager
    await sm._update_session_state(sid, SessionState.ERROR, "old failure")

    async def failing_start(session_id, *args, **kwargs):
        await sm.start_session(session_id, "restart")
        assert (await sm.get_session_info(session_id)).error_message is None
        await sm.transition_to_error(session_id, "new failure")
        return False

    coordinator._session_launcher.start_session = failing_start

    resp = await env["client"].post(f"/api/sessions/{sid}/restart")

    assert resp.status_code == 200
    assert resp.json() == {"success": False, "error": "new failure"}
    info = await sm.get_session_info(sid)
    assert info.state == SessionState.ERROR
    assert info.error_message == "new failure"


async def test_issue_2293_concurrent_restart_runs_once(api_integration_env):
    env = api_integration_env
    sid = await _new_session(env)
    coordinator = env["coordinator"]
    await coordinator.session_manager._update_session_state(sid, SessionState.ERROR, "boom")
    gate = asyncio.Event()
    calls = []

    async def slow_start(session_id, *args, **kwargs):
        calls.append(session_id)
        await gate.wait()
        return True

    coordinator._session_launcher.start_session = slow_start

    first = asyncio.create_task(coordinator.restart_session(sid))
    while not calls:
        await asyncio.sleep(0.01)
    assert await coordinator.restart_session(sid) is False
    gate.set()
    assert await first is True
    assert calls == [sid]
    # Guard released afterwards
    assert sid not in coordinator._session_launcher._restarting
