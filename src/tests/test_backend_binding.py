"""Unit tests for BackendBinding (issue #2268 AC5)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.backend_binding import BackendBinding


def test_is_embedded_true_when_supervisor_given():
    binding = BackendBinding(client=MagicMock(), token="t", supervisor=MagicMock())
    assert binding.is_embedded is True


def test_is_embedded_false_when_supervisor_none():
    binding = BackendBinding(client=MagicMock(), token="t", supervisor=None)
    assert binding.is_embedded is False


def test_degraded_false_in_remote_mode():
    binding = BackendBinding(client=MagicMock(), token="t", supervisor=None)
    assert binding.degraded is False


def test_degraded_reflects_supervisor_in_embedded_mode():
    supervisor = MagicMock(degraded=True)
    binding = BackendBinding(client=MagicMock(), token="t", supervisor=supervisor)
    assert binding.degraded is True


def test_degraded_false_when_supervisor_not_degraded():
    supervisor = MagicMock(degraded=False)
    binding = BackendBinding(client=MagicMock(), token="t", supervisor=supervisor)
    assert binding.degraded is False


@pytest.mark.asyncio
async def test_stop_supervisor_is_noop_in_remote_mode():
    binding = BackendBinding(client=MagicMock(), token="t", supervisor=None)
    await binding.stop_supervisor()  # must not raise


@pytest.mark.asyncio
async def test_stop_supervisor_calls_supervisor_stop_in_embedded_mode():
    supervisor = MagicMock(stop=AsyncMock())
    binding = BackendBinding(client=MagicMock(), token="t", supervisor=supervisor)
    await binding.stop_supervisor()
    supervisor.stop.assert_awaited_once()


def test_oauth_callback_paths_defaults_to_empty_set_and_is_mutable():
    binding = BackendBinding(client=MagicMock(), token="t")
    assert binding.oauth_callback_paths == set()
    binding.oauth_callback_paths.add("/custom/callback")
    assert "/custom/callback" in binding.oauth_callback_paths


def test_oauth_callback_paths_not_shared_across_instances():
    """Regression guard: field(default_factory=set) must give each instance
    its own set — a bare mutable default would leak across instances."""
    binding_a = BackendBinding(client=MagicMock(), token="t")
    binding_b = BackendBinding(client=MagicMock(), token="t")
    binding_a.oauth_callback_paths.add("/only-on-a")
    assert "/only-on-a" not in binding_b.oauth_callback_paths
