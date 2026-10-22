"""AC9 closeout (issue #2268, epic #1990 stage 8, PR 7 of 7).

Each of PRs 1-6 added its own targeted tests for its own AC. This is the one
place that constructs a single real BackendApp the way production actually
does (no custom TenantContext, no non-default backend_id) and exercises
every seam this stage introduced — TenantContext (AC1/AC2), Principal (AC3),
event backend_id (AC4), Comm.origin_backend_id (AC7), and the LiteLLM proxy
manager's composite-key registries (AC8) — together, in one place, asserting
they all default to the exact same values the pre-stage-8 hardcoded
constants always produced. Confirms embedded single-user mode's
production-observable behavior (events, Comms, the LiteLLM registries) is
unchanged, which is what AC9 actually requires — Principal is the one
exception: nothing in production consumes it yet this stage, so its
assertion here only proves the contextvar wiring itself, via a test-only
injected route, not a production-observable effect. Does not re-prove each
PR's own internal mechanism (see each PR's own test file for that).
"""

from shared.event_envelope import DEFAULT_BACKEND_ID


async def test_a_real_backend_app_defaults_every_stage_8_seam_identically(tmp_path):
    from backend.models.legion_models import Comm
    from backend.web_server import BackendApp

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_file = tmp_path / "config.json"
    webui = BackendApp(data_dir=data_dir, config_file=config_file, auth_token="test-token")
    await webui.initialize()

    try:
        # AC1: TenantContext defaults to the same backend_id the pre-stage-8
        # hardcoded DEFAULT_BACKEND_ID constant always provided.
        assert webui.tenant_context.backend_id == DEFAULT_BACKEND_ID
        assert webui.tenant_context.data_dir == data_dir

        # AC3: Principal, set by AuthMiddleware per request, carries that same
        # backend_id — exercised through a real authenticated request, not a
        # direct contextvar poke.
        @webui.app.get("/__test/current_principal")
        async def _current_principal():
            principal = webui.service.current_principal()
            return {"principal": None if principal is None else vars(principal)}

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=webui.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get(
                "/__test/current_principal", headers={"Authorization": "Bearer test-token"}
            )
        assert resp.status_code == 200
        assert resp.json()["principal"] == {"id": "local", "backend_id": DEFAULT_BACKEND_ID}

        # AC4: a real emit()-triggered broadcast carries that same backend_id
        # on the live event, not emit()'s own hardcoded default parameter.
        webui._broadcast_project_updated({"project_id": "p1"})
        events, _next_cursor, _evicted = webui.ui_queue.events_since(0)
        assert events
        assert events[-1]["backend_id"] == DEFAULT_BACKEND_ID

        # AC7: a real Comm defaults origin_backend_id the same way, and
        # round-trips it through to_dict()/from_dict() unchanged.
        comm = Comm(comm_id="c1", from_user=True, to_minion_id="minion-1")
        assert comm.origin_backend_id == DEFAULT_BACKEND_ID
        assert Comm.from_dict(comm.to_dict()).origin_backend_id == DEFAULT_BACKEND_ID

        # AC8: the LiteLLM proxy manager's composite-key registries are keyed
        # on that same backend_id, transparently to every public method.
        key = webui.litellm_proxy_manager.register_session_key("sess-1")
        assert webui.litellm_proxy_manager.lookup_session_for_key(key) == "sess-1"
        assert webui.litellm_proxy_manager._backend_id == DEFAULT_BACKEND_ID
    finally:
        await webui.cleanup()
