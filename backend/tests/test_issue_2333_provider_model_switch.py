"""Issue #2333: live Provider Model switch for LiteLLM-catalog-routed sessions.

Covers LiteLLMProxyManager routing-registry updates, SessionCoordinator.set_provider_model
(stopped / active non-Docker / active Docker / failure rollback / not-live-capable), the
restart-required diff, the /model regression (AC9) and analytics attribution after a switch.
"""

import asyncio
import itertools
import json
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from backend.litellm_proxy_manager import LiteLLMProxyManager, make_model_alias
from backend.restart_required import (
    LaunchSnapshot,
    build_launch_snapshot,
    compute_restart_reasons,
    routing_kind,
)
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator, _resolve_analytics_model_label
from backend.session_manager import SessionState

ENTRIES = [
    {"id": "catA", "models": [{"id": "m1"}, {"id": "m2", "context_window": 200_000}, {"id": "m3"}]},
    {"id": "catB", "models": [{"id": "m1"}]},
]


def make_proxy(live: set[str] | None = None) -> LiteLLMProxyManager:
    mgr = LiteLLMProxyManager.__new__(LiteLLMProxyManager)
    catalog = AsyncMock()
    catalog.list_entries.return_value = ENTRIES
    mgr._catalog_manager = catalog
    mgr._vault = MagicMock()
    mgr._port = 4000
    mgr._backend_id = "local"
    mgr._key_registry = {}
    mgr._routing_registry = {}
    mgr._server_task = MagicMock(done=lambda: False)
    mgr._rebuild_lock = asyncio.Lock()
    mgr._running = True
    mgr._last_restart = None
    mgr._last_error = None
    mgr._model_count = 0
    mgr._config_file = None
    mgr._routing_versions = itertools.count(1)
    mgr._live_model_names = (
        live if live is not None
        else {"catA--m1", "catA--m2", "catA--m3", "catB--m1"}
    )
    return mgr


def make_sdk(set_model_result=True):
    sdk = AsyncMock()
    sdk.start.return_value = True
    sdk.is_running.return_value = False
    sdk.set_model.return_value = set_model_result
    return sdk


@pytest.fixture
async def coordinator():
    with tempfile.TemporaryDirectory() as tmp:
        coord = SessionCoordinator(Path(tmp))
        await coord.initialize()
        yield coord
        await coord.cleanup()


async def make_session(coord, config: SessionConfig, *, start=True, sdk=None):
    """Create (and optionally start, forced ACTIVE) a session; returns (session_id, sdk)."""
    project = await coord.project_manager.create_project(name="T", working_directory="/tmp")
    sid = str(uuid.uuid4())
    await coord.create_session(session_id=sid, project_id=project.project_id, config=config)
    sdk = sdk or make_sdk()
    if start:
        coord.set_sdk_factory(Mock(return_value=sdk))
        await coord.start_session(sid)
        coord.session_manager._active_sessions[sid].state = SessionState.ACTIVE
    return sid, sdk


def session(coord, sid):
    return coord.session_manager._active_sessions[sid]


def state_json(coord, sid) -> dict:
    return json.loads((coord.session_manager.sessions_dir / sid / "state.json").read_text())


SINGLE = dict(provider_catalog_id="catA", provider_model_id="m1")


# ── LiteLLMProxyManager registry ────────────────────────────────────────────


def test_update_routing_preserves_fields_and_bumps_version():
    mgr = make_proxy()
    mgr.register_session_routing(
        "s1", "vk", "http://x/", model_map={"default": "catA--m1"},
        default_model="catA--m1", passthrough_models=["catA--clf"],
    )
    before = mgr.get_session_routing("s1")
    old = mgr.update_session_routing_model("s1", "catB--m1")
    after = mgr.get_session_routing("s1")
    assert old is before and before["default_model"] == "catA--m1"  # old dict never mutated
    assert after is not before
    assert after["default_model"] == "catB--m1"
    assert (after["virtual_key"], after["base_url"]) == ("vk", "http://x/")
    assert after["passthrough_models"] == ["catA--clf"]  # #2285 preserved
    assert after["model_map"] == {"default": "catA--m1"}
    assert after["version"] > before["version"]


def test_routing_version_stays_monotonic_across_reregistration():
    mgr = make_proxy()
    mgr.register_session_routing("s1", "vk", "http://x/", default_model="catA--m1")
    mgr.update_session_routing_model("s1", "catB--m1")
    high = mgr.get_session_routing("s1")["version"]
    mgr.unregister_session_routing("s1")
    mgr.register_session_routing("s1", "vk2", "http://x/", default_model="catA--m1")  # restart
    assert mgr.get_session_routing("s1")["version"] > high


def test_update_routing_unregistered_returns_none():
    assert make_proxy().update_session_routing_model("nope", "catA--m1") is None


def test_restore_routing_gets_newer_version():
    mgr = make_proxy()
    mgr.register_session_routing("s1", "vk", "http://x/", default_model="catA--m1")
    old = mgr.update_session_routing_model("s1", "catB--m1")
    switched_version = mgr.get_session_routing("s1")["version"]
    mgr.restore_session_routing("s1", old)
    restored = mgr.get_session_routing("s1")
    assert restored["default_model"] == "catA--m1"
    assert restored["version"] > switched_version


@pytest.mark.asyncio
async def test_rebuild_keeps_routing_registry_and_switch_still_works():
    mgr = make_proxy()
    mgr.register_session_routing("s1", "vk", "http://x/", default_model="catA--m1")
    mgr.stop = AsyncMock()
    mgr.start = AsyncMock()
    await mgr.rebuild()
    assert mgr.get_session_routing("s1")["virtual_key"] == "vk"
    assert mgr.update_session_routing_model("s1", "catB--m1") is not None


def test_has_live_model_reflects_running_router_not_catalog():
    mgr = make_proxy(live={"catA--m1"})
    assert mgr.has_live_model("catA--m1")
    assert not mgr.has_live_model("catA--m3")


# ── set_provider_model: stopped session (AC8) ───────────────────────────────


@pytest.mark.asyncio
async def test_issue_2333_stopped_session_persists_only(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, sdk = await make_session(coordinator, SessionConfig(**SINGLE), start=False)
    before_model = session(coordinator, sid).current_model

    result = await coordinator.set_provider_model(sid, "catB", "m1")

    assert result == {"model": "catB--m1", "applied": False, "restart_required": []}
    cfg = session(coordinator, sid).config
    assert (cfg["provider_catalog_id"], cfg["provider_model_id"]) == ("catB", "m1")
    assert "model" not in cfg or cfg["model"] != "catB--m1"
    assert session(coordinator, sid).current_model == before_model
    assert state_json(coordinator, sid)["config"]["provider_model_id"] == "m1"
    sdk.set_model.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_alias_is_hard_error_and_changes_nothing(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, sdk = await make_session(coordinator, SessionConfig(**SINGLE))
    with pytest.raises(ValueError, match="not in the provider catalog"):
        await coordinator.set_provider_model(sid, "catZ", "m9")
    assert session(coordinator, sid).config["provider_catalog_id"] == "catA"
    sdk.set_model.assert_not_called()


# ── set_provider_model: active non-Docker (AC1/AC2/AC4) ─────────────────────


@pytest.mark.parametrize("target,alias", [(("catA", "m2"), "catA--m2"), (("catB", "m1"), "catB--m1")])
@pytest.mark.asyncio
async def test_issue_2333_active_non_docker_same_and_cross_catalog(coordinator, target, alias):
    proxy = make_proxy()
    coordinator.litellm_proxy_manager = proxy
    sid, sdk = await make_session(coordinator, SessionConfig(**SINGLE))
    notified = []
    coordinator.session_manager.add_metadata_change_callback(AsyncMock(side_effect=notified.append))

    result = await coordinator.set_provider_model(sid, *target)

    sdk.set_model.assert_awaited_once_with(alias)  # the proxy routes the alias with that entry's creds
    assert result["applied"] is True and result["model"] == alias
    info = session(coordinator, sid)
    assert (info.config["provider_catalog_id"], info.config["provider_model_id"]) == target
    assert info.current_model == alias
    assert info.config.get("model") in (None, "")  # not polluted like update_model() would
    persisted = state_json(coordinator, sid)
    assert persisted["current_model"] == alias
    assert persisted["config"]["provider_model_id"] == target[1]
    assert "restart_required" not in persisted  # runtime-only
    assert sid in notified  # broadcast so every display updates
    assert coordinator._launch_snapshots[sid].live_alias == alias


@pytest.mark.asyncio
async def test_launch_sets_current_model_to_alias(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, _ = await make_session(coordinator, SessionConfig(**SINGLE))
    assert session(coordinator, sid).current_model == "catA--m1"
    assert state_json(coordinator, sid)["current_model"] == "catA--m1"


@pytest.mark.asyncio
async def test_mid_turn_and_permission_paused_apply_without_touching_turn(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, sdk = await make_session(coordinator, SessionConfig(**SINGLE))
    info = session(coordinator, sid)
    info.is_processing = True
    info.state = SessionState.PAUSED  # permission pending
    pending = asyncio.get_running_loop().create_future()
    coordinator.permission_service = MagicMock(pending_permissions={"req-1": pending})

    result = await coordinator.set_provider_model(sid, "catA", "m2")

    assert result["applied"] is True
    sdk.set_model.assert_awaited_once_with("catA--m2")
    assert not pending.done()  # permission neither resolved nor cancelled
    assert info.is_processing is True and info.state == SessionState.PAUSED
    sdk.interrupt_session.assert_not_called()
    assert coordinator.permission_service.pending_permissions == {"req-1": pending}
    coordinator.permission_service = None


# ── Failure paths (AC6) ─────────────────────────────────────────────────────


@pytest.mark.parametrize("failure", ["raises", "false", "timeout"])
@pytest.mark.asyncio
async def test_issue_2333_sdk_failure_leaves_config_untouched(coordinator, failure, monkeypatch):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, sdk = await make_session(coordinator, SessionConfig(**SINGLE))
    if failure == "raises":
        sdk.set_model.side_effect = RuntimeError("upstream 500")
    elif failure == "false":
        sdk.set_model.return_value = False
    else:
        monkeypatch.setattr("backend.provider_model_switch.PROVIDER_SWITCH_TIMEOUT_SECONDS", 0.01)

        async def _hang(*_):
            await asyncio.sleep(5)

        sdk.set_model.side_effect = _hang
    with pytest.raises(ValueError):
        await coordinator.set_provider_model(sid, "catB", "m1")
    info = session(coordinator, sid)
    assert (info.config["provider_catalog_id"], info.config["provider_model_id"]) == ("catA", "m1")
    assert info.current_model == "catA--m1"
    assert coordinator._launch_snapshots[sid].live_alias == "catA--m1"
    assert state_json(coordinator, sid)["config"]["provider_catalog_id"] == "catA"


@pytest.mark.asyncio
async def test_persist_failure_rolls_back_live_switch_non_docker(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, sdk = await make_session(coordinator, SessionConfig(**SINGLE))
    coordinator.session_manager.update_provider_model = AsyncMock(return_value=False)
    with pytest.raises(ValueError, match="not applied"):
        await coordinator.set_provider_model(sid, "catB", "m1")
    assert [c.args[0] for c in sdk.set_model.await_args_list] == ["catB--m1", "catA--m1"]
    assert coordinator._launch_snapshots[sid].live_alias == "catA--m1"


# ── Docker (AC3): registry swap, addon re-reads ─────────────────────────────


DOCKER = dict(**SINGLE, docker_enabled=True, cli_path="/usr/bin/claude")


@pytest.mark.asyncio
async def test_issue_2333_docker_switch_updates_registry_not_sdk(coordinator):
    proxy = make_proxy()
    coordinator.litellm_proxy_manager = proxy
    sid, sdk = await make_session(coordinator, SessionConfig(**DOCKER))
    routing = proxy.get_session_routing(sid)
    assert routing["default_model"] == "catA--m1"
    key, base, v0 = routing["virtual_key"], routing["base_url"], routing["version"]

    result = await coordinator.set_provider_model(sid, "catB", "m1")

    assert result["applied"] is True
    sdk.set_model.assert_not_called()  # SDK model is unset on Docker; the sidecar rewrites
    new = proxy.get_session_routing(sid)
    assert new["default_model"] == "catB--m1"
    assert (new["virtual_key"], new["base_url"]) == (key, base)
    assert new["version"] > v0
    assert session(coordinator, sid).current_model == "catB--m1"


@pytest.mark.asyncio
async def test_docker_persist_failure_restores_registry(coordinator):
    proxy = make_proxy()
    coordinator.litellm_proxy_manager = proxy
    sid, _ = await make_session(coordinator, SessionConfig(**DOCKER))
    coordinator.session_manager.update_provider_model = AsyncMock(return_value=False)
    with pytest.raises(ValueError):
        await coordinator.set_provider_model(sid, "catB", "m1")
    restored = proxy.get_session_routing(sid)
    assert restored["default_model"] == "catA--m1"
    assert restored["version"] > 2  # newer than the failed swap so a caching addon re-adopts it


# ── Not live-capable → persisted + restart_required (never silent) ──────────


@pytest.mark.asyncio
async def test_alias_not_in_live_router_persists_with_catalog_pending_notice(coordinator):
    coordinator.litellm_proxy_manager = make_proxy(live={"catA--m1", "catB--m1"})
    sid, sdk = await make_session(coordinator, SessionConfig(**SINGLE))
    result = await coordinator.set_provider_model(sid, "catA", "m3")
    assert result["applied"] is False
    assert any("Catalog change pending" in r for r in result["restart_required"])
    sdk.set_model.assert_not_called()
    info = session(coordinator, sid)
    assert info.config["provider_model_id"] == "m3"
    assert info.current_model == "catA--m1"  # still what is actually served
    assert info.restart_required == result["restart_required"]


@pytest.mark.asyncio
async def test_direct_session_gets_provider_model_with_routing_notice(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, sdk = await make_session(coordinator, SessionConfig())
    result = await coordinator.set_provider_model(sid, "catA", "m1")
    assert result["applied"] is False
    assert any("routing changed" in r for r in result["restart_required"])
    sdk.set_model.assert_not_called()


@pytest.mark.asyncio
async def test_restart_clears_restart_required(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, _ = await make_session(coordinator, SessionConfig(**SINGLE), start=False)
    session(coordinator, sid).restart_required = ["stale"]
    coordinator.set_sdk_factory(Mock(return_value=make_sdk()))
    await coordinator.start_session(sid)
    assert session(coordinator, sid).restart_required == []


@pytest.mark.asyncio
async def test_terminate_drops_snapshot_and_reasons(coordinator):
    coordinator.litellm_proxy_manager = make_proxy(live={"catA--m1"})
    sid, _ = await make_session(coordinator, SessionConfig(**SINGLE))
    await coordinator.set_provider_model(sid, "catA", "m3")
    assert session(coordinator, sid).restart_required
    await coordinator.terminate_session(sid)
    assert sid not in coordinator._launch_snapshots
    assert session(coordinator, sid).restart_required == []


# ── Restart-required diff via config PATCH ──────────────────────────────────


async def patch_config(coord, sid, **changes):
    cfg = dict(session(coord, sid).config)
    cfg.update(changes)
    await coord.session_manager.update_session(sid, _replace_config=cfg)
    return await coord.refresh_restart_required(sid)


@pytest.mark.asyncio
async def test_config_patch_reasons_appear_and_clear_on_revert(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, _ = await make_session(coordinator, SessionConfig(**SINGLE))

    assert any("Docker" in r for r in await patch_config(coordinator, sid, docker_enabled=True))
    assert await patch_config(coordinator, sid, docker_enabled=False) == []
    assert any("classifier" in r for r in await patch_config(
        coordinator, sid, auto_mode_classifier_model="catA--m2"))
    assert await patch_config(coordinator, sid, auto_mode_classifier_model=None) == []
    cleared = await patch_config(coordinator, sid, provider_catalog_id=None, provider_model_id=None)
    assert any("routing changed" in r for r in cleared)
    assert await patch_config(coordinator, sid, **SINGLE) == []
    # model saved by PATCH only (no live switch) is reported, not silent
    reasons = await patch_config(coordinator, sid, provider_model_id="m2")
    assert any("saved but the session is still using" in r for r in reasons)
    assert session(coordinator, sid).restart_required == reasons


@pytest.mark.asyncio
async def test_live_switch_then_patch_with_same_values_has_no_reason(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, _ = await make_session(coordinator, SessionConfig(**SINGLE))
    await coordinator.set_provider_model(sid, "catB", "m1")  # UI order: live switch, then PATCH
    assert await patch_config(coordinator, sid, provider_catalog_id="catB", provider_model_id="m1") == []


@pytest.mark.asyncio
async def test_compaction_window_drift_is_a_reason_and_reverts(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    proxy = coordinator.litellm_proxy_manager
    proxy.get_model_context_window = AsyncMock(
        side_effect=lambda alias: {"catA--m1": 150_000, "catA--m2": 200_000}.get(alias)
    )
    sid, _ = await make_session(coordinator, SessionConfig(**SINGLE))
    assert coordinator._launch_snapshots[sid].compact_window == 150_000
    result = await coordinator.set_provider_model(sid, "catA", "m2")
    assert result["applied"] is True
    assert any("auto-compact window (150000 -> 200000)" in r for r in result["restart_required"])
    back = await coordinator.set_provider_model(sid, "catA", "m1")
    assert back["restart_required"] == []


def test_compute_reasons_tier_change_and_user_override_of_window():
    tier = dict(
        provider_haiku_catalog_id="c", provider_haiku_model_id="h",
        provider_sonnet_catalog_id="c", provider_sonnet_model_id="s",
        provider_opus_catalog_id="c", provider_opus_model_id="o", provider_default_tier="sonnet",
    )
    cfg = SessionConfig(**tier, docker_enabled=True)
    assert routing_kind(cfg) == "tier"
    snap = build_launch_snapshot(cfg, litellm_routed=True, live_alias=None,
                                 wants_compact_window=True, compact_window=None)
    assert compute_restart_reasons(snap, cfg, new_compact_window=None, alias_in_live_router=True) == []
    changed = SessionConfig(**{**tier, "provider_opus_model_id": "o2"}, docker_enabled=True)
    reasons = compute_restart_reasons(snap, changed, new_compact_window=None, alias_in_live_router=True)
    assert reasons == ["Per-tier provider models changed: restart the session to apply."]
    # a snapshot where the user's extra_env owns the window never reports drift
    single_cfg = SessionConfig(**SINGLE)
    s2 = LaunchSnapshot("single", False, None, build_launch_snapshot(
        single_cfg, litellm_routed=True, live_alias="catA--m1",
        wants_compact_window=False, compact_window=None).tier_values, "catA--m1", False, None)
    assert compute_restart_reasons(s2, single_cfg, new_compact_window=123_000,
                                   alias_in_live_router=True) == []


# ── AC9: the plain Model path (#1673) is unchanged ──────────────────────────


@pytest.mark.asyncio
async def test_plain_model_switch_unchanged_and_never_touches_provider_fields(coordinator):
    sid, sdk = await make_session(coordinator, SessionConfig(model="sonnet"))
    assert await coordinator.set_model(sid, "opus") is True
    sdk.set_model.assert_awaited_once_with("opus")
    info = session(coordinator, sid)
    assert info.config["model"] == "opus" and info.current_model == "opus"
    assert "provider_catalog_id" not in info.config or not info.config["provider_catalog_id"]
    assert info.restart_required == []


@pytest.mark.asyncio
async def test_plain_model_on_stopped_session_persists_only(coordinator):
    sid, sdk = await make_session(coordinator, SessionConfig(model="sonnet"), start=False)
    assert await coordinator.set_model(sid, "opus") is True
    assert session(coordinator, sid).config["model"] == "opus"
    sdk.set_model.assert_not_called()


# ── Analytics attribution follows the switch ────────────────────────────────


def _provider_cfg(info) -> SessionConfig:
    return SessionConfig(**{k: v for k, v in info.config.items() if k.startswith("provider_")})


@pytest.mark.asyncio
async def test_analytics_label_follows_switched_model(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, _ = await make_session(coordinator, SessionConfig(**SINGLE))
    assert _resolve_analytics_model_label(_provider_cfg(session(coordinator, sid))) == "catA--m1"
    await coordinator.set_provider_model(sid, "catB", "m1")
    assert _resolve_analytics_model_label(_provider_cfg(session(coordinator, sid))) == make_model_alias(
        "catB", "m1"
    )


# ── HTTP endpoints ──────────────────────────────────────────────────────────


async def _runtime_client(webui):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from backend.routers.session_runtime import build_router

    app = FastAPI()
    app.include_router(build_router(webui))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _webui(coordinator_mock):
    from backend.application_service import ApplicationService

    webui = MagicMock()
    webui.service = ApplicationService(coordinator_mock)
    return webui


@pytest.mark.asyncio
async def test_provider_model_endpoint_success_and_validation_error():
    coord = MagicMock()
    coord.session_manager.get_session_info = AsyncMock(return_value=MagicMock())
    coord.set_provider_model = AsyncMock(
        return_value={"model": "catB--m1", "applied": True, "restart_required": []}
    )
    async with await _runtime_client(_webui(coord)) as client:
        ok = await client.post("/api/sessions/s1/provider-model",
                               json={"provider_catalog_id": "catB", "provider_model_id": "m1"})
        assert ok.status_code == 200
        assert ok.json() == {"success": True, "model": "catB--m1", "applied": True,
                             "restart_required": []}
        coord.set_provider_model.assert_awaited_once_with("s1", "catB", "m1")

        coord.set_provider_model.side_effect = ValueError("upstream rejected it")
        bad = await client.post("/api/sessions/s1/provider-model",
                                json={"provider_catalog_id": "catB", "provider_model_id": "m1"})
        assert bad.status_code == 400
        assert "upstream rejected it" in bad.text


@pytest.mark.asyncio
async def test_provider_model_endpoint_404_for_unknown_session():
    coord = MagicMock()
    coord.session_manager.get_session_info = AsyncMock(return_value=None)
    async with await _runtime_client(_webui(coord)) as client:
        resp = await client.post("/api/sessions/nope/provider-model",
                                 json={"provider_catalog_id": "catB", "provider_model_id": "m1"})
    assert resp.status_code == 404


def test_routing_payload_carries_version():
    from backend.application_service import ApplicationService

    coord = MagicMock()
    proxy = make_proxy()
    coord.litellm_proxy_manager = proxy
    proxy.register_session_routing("s1", "vk", "http://x/", default_model="catA--m1")
    proxy.update_session_routing_model("s1", "catB--m1")
    payload = ApplicationService(coord).get_litellm_session_routing("s1")
    assert payload["version"] == 2 and payload["default_model"] == "catB--m1"


@pytest.mark.asyncio
async def test_patch_config_triggers_restart_required_refresh():
    from backend.application_service import ApplicationService

    coord = MagicMock()
    coord.session_manager.update_session = AsyncMock(return_value=True)
    coord.refresh_restart_required = AsyncMock(return_value=[])
    svc = ApplicationService(coord)
    await svc.update_session("s1", _replace_config={"a": 1})
    coord.refresh_restart_required.assert_awaited_once_with("s1")
    coord.refresh_restart_required.reset_mock()
    await svc.update_session("s1", name="x")
    coord.refresh_restart_required.assert_not_awaited()


# ── Review hardening: serialization, restart races, launch window ───────────


@pytest.mark.asyncio
async def test_concurrent_switches_are_serialized_and_end_consistent(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, sdk = await make_session(coordinator, SessionConfig(**SINGLE))
    in_flight = 0
    max_in_flight = 0

    async def slow_set_model(alias):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return True

    sdk.set_model.side_effect = slow_set_model
    await asyncio.gather(
        coordinator.set_provider_model(sid, "catA", "m2"),
        coordinator.set_provider_model(sid, "catB", "m1"),
    )
    assert max_in_flight == 1
    info = session(coordinator, sid)
    last_applied = sdk.set_model.await_args_list[-1].args[0]
    assert info.current_model == last_applied == coordinator._launch_snapshots[sid].live_alias
    assert make_model_alias(info.config["provider_catalog_id"], info.config["provider_model_id"]) == last_applied


@pytest.mark.asyncio
async def test_switch_across_a_restart_persists_without_stale_snapshot_update(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, sdk = await make_session(coordinator, SessionConfig(**SINGLE))
    old_snap = coordinator._launch_snapshots[sid]

    async def restart_during_switch(alias):
        coordinator._launch_snapshots[sid] = LaunchSnapshot(**{**old_snap.__dict__})  # new launch
        return True

    sdk.set_model.side_effect = restart_during_switch
    result = await coordinator.set_provider_model(sid, "catB", "m1")
    assert result["applied"] is False
    assert session(coordinator, sid).config["provider_catalog_id"] == "catB"
    assert coordinator._launch_snapshots[sid].live_alias == "catA--m1"  # new launch untouched


@pytest.mark.asyncio
async def test_config_saved_during_launch_window_is_reported_after_launch(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, _ = await make_session(coordinator, SessionConfig(**SINGLE), start=False)
    sdk = make_sdk()
    real_start = sdk.start

    async def start_with_racing_save():
        # persisted after effective_config was read but before the launch snapshot exists
        await coordinator.session_manager.update_provider_model(sid, "catB", "m1")
        return await real_start()

    sdk.start = start_with_racing_save
    coordinator.set_sdk_factory(Mock(return_value=sdk))
    await coordinator.start_session(sid)
    session(coordinator, sid).state = SessionState.ACTIVE
    # launched on catA--m1 but config says catB--m1: a restart reason, never silent
    assert any("still using" in r for r in await coordinator.refresh_restart_required(sid))


@pytest.mark.asyncio
async def test_refresh_without_proxy_manager_reports_nothing(coordinator):
    coordinator.litellm_proxy_manager = make_proxy()
    sid, _ = await make_session(coordinator, SessionConfig(**SINGLE))
    coordinator.litellm_proxy_manager = None
    assert await coordinator.refresh_restart_required(sid) == []
