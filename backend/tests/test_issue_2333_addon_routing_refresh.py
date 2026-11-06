"""Issue #2333: the Docker proxy sidecar addon re-reads its routing before rewriting the model,
so a live Provider Model switch (backend registry swap) reaches the next request.

Uses the mitmproxy flow stubs shared with test_docker_proxy_addon_1469.py (mitmproxy itself is
not installed in the dev environment).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from backend.tests.test_docker_proxy_addon_1469 import _flow, _make_addon

HOST_REWRITES = {"api.anthropic.com": "cc-webui.internal:4000"}


def routing(default_model, version, *, vkey="lc-vkey", passthrough=None, model_map=None):
    return {
        "hostname_rewrites": HOST_REWRITES, "virtual_key": vkey, "model_map": model_map or {},
        "default_model": default_model, "passthrough_models": passthrough or [], "version": version,
    }


class _Mod:
    """Attribute view over the addon module's globals (the module itself is evicted from
    sys.modules by every _flow() call of the shared stub helpers)."""

    def __init__(self, g):
        self.__dict__["_g"] = g

    def __getattr__(self, name):
        return self._g[name]


def addon_with_backend(initial, backend_state):
    """An addon whose routing fetch reads ``backend_state['routing']`` (the backend registry)."""
    addon = _make_addon(routing=initial)
    addon._fetch_routing = AsyncMock(side_effect=lambda timeout=None: backend_state["routing"])
    addon._routing_checked_at = -1e9  # TTL long expired
    return addon, _Mod(addon.request.__func__.__globals__)


async def send(addon, model="claude-sonnet-4-6"):
    f = _flow(body_dict={"model": model, "messages": []}, metadata={"routed_via_litellm": True})
    await addon.request(f)
    return json.loads(f.request.content)["model"], f


@pytest.mark.asyncio
async def test_issue_2333_fresh_addon_follows_live_switch():
    state = {"routing": routing("catA--m1", 1)}
    addon, _ = addon_with_backend(state["routing"], state)
    assert (await send(addon))[0] == "catA--m1"

    state["routing"] = routing("catB--m1", 2)  # backend: update_session_routing_model()
    addon._routing_checked_at = -1e9
    model, f = await send(addon)
    assert model == "catB--m1"
    assert f.metadata["model_rewrite"] == ("claude-sonnet-4-6", "catB--m1")


@pytest.mark.asyncio
async def test_issue_2333_stale_addon_negative(monkeypatch):
    """An addon that never re-fetches (pre-#2333 behaviour) keeps rewriting to the OLD model."""
    state = {"routing": routing("catA--m1", 1)}
    addon, addon_mod = addon_with_backend(state["routing"], state)
    monkeypatch.setitem(addon_mod._g, "ROUTING_REFRESH_TTL_SECONDS", float("inf"))
    addon._routing_checked_at = addon_mod.time.monotonic()
    state["routing"] = routing("catB--m1", 2)
    assert (await send(addon))[0] == "catA--m1"
    addon._fetch_routing.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_is_ttl_bounded_and_single_flight():
    state = {"routing": routing("catA--m1", 1)}
    addon, _ = addon_with_backend(state["routing"], state)
    await asyncio.gather(*(send(addon) for _ in range(8)))
    assert addon._fetch_routing.await_count == 1  # concurrent requests share one fetch
    await send(addon)
    assert addon._fetch_routing.await_count == 1  # within TTL: no extra fetch


@pytest.mark.asyncio
async def test_fetch_uses_tight_timeout():
    state = {"routing": routing("catA--m1", 1)}
    addon, addon_mod = addon_with_backend(state["routing"], state)
    await send(addon)
    addon._fetch_routing.assert_awaited_once_with(timeout=addon_mod.ROUTING_FETCH_TIMEOUT_SECONDS)
    assert addon_mod.ROUTING_FETCH_TIMEOUT_SECONDS <= 2.0


@pytest.mark.asyncio
async def test_fetch_failure_keeps_cached_routing_and_logs_once():
    state = {"routing": routing("catA--m1", 1)}
    addon, _ = addon_with_backend(state["routing"], state)
    addon._fetch_routing = AsyncMock(side_effect=TimeoutError("slow"))
    diag = []
    addon._write_diag = diag.append
    for _ in range(3):
        addon._routing_checked_at = -1e9
        assert (await send(addon))[0] == "catA--m1"  # last good routing still used
    assert len(diag) == 1  # logged once, not per request


@pytest.mark.asyncio
async def test_unusable_payloads_do_not_replace_good_routing():
    state = {"routing": routing("catA--m1", 5)}
    addon, _ = addon_with_backend(state["routing"], state)
    # backend lost the entry -> empty routing (no virtual key, version 0)
    state["routing"] = {"hostname_rewrites": {}, "virtual_key": None, "model_map": {},
                        "default_model": None, "version": 0}
    addon._routing_checked_at = -1e9
    assert (await send(addon))[0] == "catA--m1"
    # an older version is ignored
    state["routing"] = routing("catB--m1", 3)
    addon._routing_checked_at = -1e9
    assert (await send(addon))[0] == "catA--m1"
    # rollback publishes a NEWER version carrying the old model: adopted
    state["routing"] = routing("catA--m1", 7, model_map={"default": "catA--m1"})
    addon._routing_checked_at = -1e9
    await send(addon)
    assert addon._routing["version"] == 7


@pytest.mark.asyncio
async def test_switch_preserves_passthrough_and_tier_map_semantics():
    state = {"routing": routing("catA--m1", 1, passthrough=["catA--clf"])}
    addon, _ = addon_with_backend(state["routing"], state)
    state["routing"] = routing("catB--m1", 2, passthrough=["catA--clf"])
    addon._routing_checked_at = -1e9
    assert (await send(addon, model="catA--clf"))[0] == "catA--clf"  # classifier not rewritten
    assert (await send(addon))[0] == "catB--m1"


@pytest.mark.asyncio
async def test_request_sees_one_routing_snapshot():
    """_routing is replaced wholesale, never mutated: a snapshot taken before a swap is intact."""
    state = {"routing": routing("catA--m1", 1)}
    addon, _ = addon_with_backend(state["routing"], state)
    snapshot = addon._routing
    state["routing"] = routing("catB--m1", 2)
    addon._routing_checked_at = -1e9
    await send(addon)
    assert snapshot["default_model"] == "catA--m1" and snapshot["version"] == 1
    assert addon._routing is not snapshot
