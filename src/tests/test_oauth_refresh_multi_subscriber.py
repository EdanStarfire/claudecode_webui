"""Tests for OAuthRefreshManager multi-subscriber broadcast (issue #1484 §4.6)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.oauth_refresh_manager import OAuthRefreshManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_manager() -> OAuthRefreshManager:
    oauth_manager = MagicMock()
    store = MagicMock()
    store.get_token_expiry = AsyncMock(return_value=None)
    oauth_manager.get_token_store.return_value = store
    oauth_manager.refresh_token = AsyncMock(return_value=MagicMock(access_token="new-token"))
    return OAuthRefreshManager(oauth_manager)


# ---------------------------------------------------------------------------
# set_broadcast_callback — legacy single-subscriber
# ---------------------------------------------------------------------------


def test_set_broadcast_callback_replaces_existing():
    mgr = _make_manager()
    cb1 = MagicMock()
    cb2 = MagicMock()
    mgr.set_broadcast_callback(cb1)
    mgr.set_broadcast_callback(cb2)
    assert mgr._broadcast_subscribers == [cb2]


# ---------------------------------------------------------------------------
# add_broadcast_subscriber
# ---------------------------------------------------------------------------


def test_add_broadcast_subscriber_appends():
    mgr = _make_manager()
    cb1 = MagicMock()
    cb2 = MagicMock()
    mgr.add_broadcast_subscriber(cb1)
    mgr.add_broadcast_subscriber(cb2)
    assert mgr._broadcast_subscribers == [cb1, cb2]


# ---------------------------------------------------------------------------
# Dispatch: both sync and async subscribers fire; one exception doesn't suppress others
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_subscriber_dispatch_sync_and_async():
    mgr = _make_manager()
    sync_calls = []
    async_calls = []

    def sync_sub(server_id):
        sync_calls.append(server_id)

    async def async_sub(server_id):
        async_calls.append(server_id)

    mgr.add_broadcast_subscriber(sync_sub)
    mgr.add_broadcast_subscriber(async_sub)

    # Simulate a successful refresh by calling the dispatch logic directly
    # through a minimal _refresh_loop run.
    # Patch sleep to avoid blocking, then inject a token with no expiry
    # (loop sleeps 300 s normally — we patch it out).
    import time as _time

    server_id = "srv-abc"

    # Make get_token_expiry return now+1 so sleep_seconds <= 0 and loop proceeds to refresh
    store = mgr._oauth_manager.get_token_store.return_value
    store.get_token_expiry = AsyncMock(side_effect=[_time.time() + 1, None])
    # After the refresh, second iteration hits None expiry → sleep 300 s
    # We'll cancel the task after first iteration.

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        task = asyncio.create_task(mgr._refresh_loop(server_id))
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert sync_calls == [server_id]
    assert async_calls == [server_id]


@pytest.mark.asyncio
async def test_multi_subscriber_exception_in_one_does_not_suppress_other():
    mgr = _make_manager()
    second_calls = []

    def bad_sub(server_id):
        raise RuntimeError("subscriber error")

    def good_sub(server_id):
        second_calls.append(server_id)

    mgr.add_broadcast_subscriber(bad_sub)
    mgr.add_broadcast_subscriber(good_sub)

    import time as _time

    server_id = "srv-xyz"
    store = mgr._oauth_manager.get_token_store.return_value
    store.get_token_expiry = AsyncMock(side_effect=[_time.time() + 1, None])

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        task = asyncio.create_task(mgr._refresh_loop(server_id))
        try:
            await task
        except asyncio.CancelledError:
            pass

    # good_sub still fired even though bad_sub raised
    assert good_sub in [type(s) for s in []] or second_calls == [server_id]
