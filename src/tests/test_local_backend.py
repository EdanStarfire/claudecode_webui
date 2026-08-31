"""Unit tests for LocalBackend (issue #498)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ..local_backend import LocalBackend


def _mock_sdk(**overrides):
    sdk = MagicMock()
    sdk.send_message = AsyncMock(return_value=True)
    sdk.interrupt_session = AsyncMock(return_value=True)
    sdk.set_permission_mode = AsyncMock(return_value=True)
    sdk.set_model = AsyncMock(return_value=True)
    sdk.get_mcp_status = AsyncMock(return_value={"servers": ["a"]})
    sdk.get_context_usage = AsyncMock(return_value={"totalTokens": 100})
    sdk.toggle_mcp_server = AsyncMock(return_value=None)
    sdk.reconnect_mcp_server = AsyncMock(return_value=None)
    sdk.register_repo_root = AsyncMock(return_value={"directory": "/tmp/x"})
    sdk.disconnect = AsyncMock(return_value=True)
    sdk.terminate = AsyncMock(return_value=True)
    sdk.get_info = MagicMock(return_value={"state": "running"})
    sdk.get_queue_size = MagicMock(return_value=0)
    for key, value in overrides.items():
        setattr(sdk, key, value)
    return sdk


@pytest.mark.asyncio
async def test_no_active_sdk_returns_safe_defaults():
    backend = LocalBackend()
    assert await backend.send_message("s1", "hi") is False
    assert await backend.interrupt_session("s1") is False
    assert await backend.set_permission_mode("s1", "plan") is False
    assert await backend.set_model("s1", "sonnet") is False
    assert await backend.get_mcp_status("s1") == {"servers": []}
    assert await backend.get_context_usage("s1") == {}
    assert await backend.is_session_active("s1") is False
    assert await backend.get_session_runtime_info("s1") is None
    assert backend.active_session_ids() == []
    assert await backend.disconnect_session("s1") is True  # already disconnected
    assert await backend.terminate_session("s1") is True


@pytest.mark.asyncio
async def test_no_active_sdk_raises_for_mcp_toggle_and_add_directory():
    backend = LocalBackend()
    with pytest.raises(RuntimeError):
        await backend.toggle_mcp_server("s1", "srv", True)
    with pytest.raises(RuntimeError):
        await backend.reconnect_mcp_server("s1", "srv")
    with pytest.raises(ValueError):
        await backend.add_directory("s1", "/tmp/new")


@pytest.mark.asyncio
async def test_registered_sdk_drives_business_methods():
    backend = LocalBackend()
    sdk = _mock_sdk()
    backend.register_sdk("s1", sdk)

    assert await backend.is_session_active("s1") is True
    assert backend.active_session_ids() == ["s1"]

    assert await backend.send_message("s1", "hello") is True
    sdk.send_message.assert_awaited_once_with("hello", metadata=None)

    assert await backend.interrupt_session("s1") is True
    assert await backend.set_permission_mode("s1", "plan") is True
    assert await backend.set_model("s1", "opus") is True
    assert await backend.get_mcp_status("s1") == {"servers": ["a"]}
    assert await backend.get_context_usage("s1") == {"totalTokens": 100}

    await backend.toggle_mcp_server("s1", "srv", False)
    sdk.toggle_mcp_server.assert_awaited_once_with("srv", False)

    await backend.reconnect_mcp_server("s1", "srv")
    sdk.reconnect_mcp_server.assert_awaited_once_with("srv")

    result = await backend.add_directory("s1", "/tmp/new")
    assert result == {"directory": "/tmp/x"}

    info = await backend.get_session_runtime_info("s1")
    assert info["state"] == "running"
    assert info["queue_size"] == 0


@pytest.mark.asyncio
async def test_terminate_and_disconnect_remove_sdk():
    backend = LocalBackend()
    backend.register_sdk("s1", _mock_sdk())
    assert await backend.terminate_session("s1") is True
    assert await backend.is_session_active("s1") is False

    backend.register_sdk("s2", _mock_sdk())
    assert await backend.disconnect_session("s2") is True
    assert await backend.is_session_active("s2") is False


@pytest.mark.asyncio
async def test_start_session_requires_sdk_kwargs():
    backend = LocalBackend()
    with pytest.raises(ValueError):
        await backend.start_session("s1")


@pytest.mark.asyncio
async def test_start_session_success_registers_sdk_and_sets_auto_approval():
    backend = LocalBackend()
    sdk = _mock_sdk()
    sdk.start = AsyncMock(return_value=True)
    backend.set_sdk_factory(lambda session_id, **kwargs: sdk)
    auto_approval_cb = MagicMock()

    started, error = await backend.start_session(
        "s1", sdk_kwargs={"working_directory": "/tmp"}, auto_approval_callback=auto_approval_cb
    )

    assert started is True
    assert error is None
    assert backend.get_sdk("s1") is sdk
    assert sdk.auto_approval_callback is auto_approval_cb


@pytest.mark.asyncio
async def test_start_session_failure_removes_sdk_and_returns_error():
    backend = LocalBackend()
    sdk = _mock_sdk()
    sdk.start = AsyncMock(return_value=False)
    sdk.info = MagicMock(error_message="boom")
    backend.set_sdk_factory(lambda session_id, **kwargs: sdk)

    started, error = await backend.start_session("s1", sdk_kwargs={"working_directory": "/tmp"})

    assert started is False
    assert error == "boom"
    assert backend.get_sdk("s1") is None


@pytest.mark.asyncio
async def test_start_session_exception_removes_sdk():
    backend = LocalBackend()
    sdk = _mock_sdk()
    sdk.start = AsyncMock(side_effect=RuntimeError("kaboom"))
    backend.set_sdk_factory(lambda session_id, **kwargs: sdk)

    started, error = await backend.start_session("s1", sdk_kwargs={"working_directory": "/tmp"})

    assert started is False
    assert error == "kaboom"
    assert backend.get_sdk("s1") is None
