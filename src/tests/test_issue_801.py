"""
Regression tests for issue #801.

a) CTRL-C / task cancellation does NOT set SessionState.FAILED
b) /restart endpoint registers an active message callback after restart
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Test (a): CTRL-C / task cancellation guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_801_ctrl_c_does_not_set_error_state():
    """Regression: task cancellation (CTRL-C) must not trigger the FAILED error path.

    Reproduces the scenario where:
    1. asyncio cancels the consumer task (CTRL-C / SIGINT)
    2. The SDK internally catches CancelledError and re-raises a different exception
    3. The fixed guard (also checking `asyncio.current_task().cancelling() > 0`)
       must prevent setting SessionState.FAILED.
    """
    error_path_entered = []

    async def simulated_sdk_layer():
        """Mimics SDK behaviour: catches CancelledError, raises a different error."""
        try:
            await asyncio.sleep(0)  # Cancellation is injected here
        except asyncio.CancelledError:
            # SDK wraps the cancellation as a generic connection error
            raise RuntimeError("Connection closed during cancellation") from None

    async def guarded_consumer():
        """Mirrors consume_all_responses() with the fixed exception guard."""
        try:
            await simulated_sdk_layer()
        except Exception:
            # Fixed guard: also checks task.cancelling() so CTRL-C is not treated as error
            if not asyncio.current_task().cancelling() > 0:
                error_path_entered.append(True)

    task = asyncio.create_task(guarded_consumer())
    await asyncio.sleep(0)  # Yield so task starts and reaches its first await point
    task.cancel()

    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert not error_path_entered, (
        "CTRL-C / task cancellation must not trigger the error path"
    )


@pytest.mark.asyncio
async def test_issue_801_non_cancellation_exception_still_sets_error_state():
    """Confirm normal (non-cancellation) exceptions DO still enter the error path."""
    error_path_entered = []

    async def guarded_consumer():
        """Mirrors consume_all_responses() with the fixed exception guard."""
        try:
            raise RuntimeError("Real SDK error")
        except Exception:
            if not asyncio.current_task().cancelling() > 0:
                error_path_entered.append(True)

    task = asyncio.create_task(guarded_consumer())
    await task

    assert error_path_entered, (
        "Real (non-cancellation) exceptions must still enter the error path"
    )


# ---------------------------------------------------------------------------
# Test (b): /restart endpoint registers message callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_801_restart_registers_message_callback():
    """Regression: /restart endpoint must register a message callback.

    Before the fix the /restart endpoint only passed a permission_callback but
    never cleared or re-registered the message streaming callback.  After restart
    messages went to storage but not to the WebSocket client.

    This test verifies that coordinator._message_callbacks[session_id] is
    non-empty after the /restart HTTP endpoint is called.
    """
    from httpx import ASGITransport, AsyncClient

    from ..web_server import ClaudeWebUI

    with tempfile.TemporaryDirectory() as tmp:
        webui = ClaudeWebUI(data_dir=Path(tmp))

        # Patch coordinator so restart_session doesn't actually start the SDK.
        session_id = "test-session-801"
        webui.coordinator._message_callbacks[session_id] = []

        # Mock session_manager.get_session_info to return a non-None session
        # (the restart_session endpoint now checks session existence first).
        mock_session = object()
        with patch.object(
            webui.coordinator, "restart_session", new_callable=AsyncMock
        ) as mock_restart, patch.object(
            webui.coordinator.session_manager, "get_session_info",
            new_callable=AsyncMock, return_value=mock_session
        ):
            mock_restart.return_value = True

            async with AsyncClient(
                transport=ASGITransport(app=webui.app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/sessions/{session_id}/restart"
                )

        assert response.status_code == 200
        assert response.json()["success"] is True

        # The endpoint must have registered at least one message callback.
        callbacks = webui.coordinator._message_callbacks.get(session_id, [])
        assert len(callbacks) > 0, (
            "restart endpoint must register a message callback "
            f"in coordinator._message_callbacks[{session_id!r}]"
        )
