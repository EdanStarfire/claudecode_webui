"""Regression test for issue #2268 AC4 — emitted events must carry the live
TenantContext.backend_id, not emit()'s own hardcoded DEFAULT_BACKEND_ID default.

Every emit() call site in backend/ was given an explicit `backend_id=` kwarg
sourced from tenant_context (or, where no tenant_context is reachable, from a
constructor-seeded `backend_id` that traces back to one). Today
TenantContext.backend_id always equals DEFAULT_BACKEND_ID, so asserting a
literal "local" would pass even if a call site silently fell back to the
hardcoded default instead of actually reading tenant_context — this test
instead constructs a BackendApp with a deliberately non-default backend_id,
so a regression (a call site reverting to the hardcoded default) would be
caught by a value mismatch, not hidden by coincidence.
"""

from backend.tenant_context import TenantContext
from backend.web_server import BackendApp


def test_emitted_event_backend_id_tracks_tenant_context_not_the_hardcoded_default(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    tenant_context = TenantContext(
        data_dir=data_dir,
        config_file=tmp_path / "config.json",
        host="127.0.0.1",
        backend_id="custom-backend-xyz",
    )
    webui = BackendApp(data_dir=data_dir, tenant_context=tenant_context)

    webui._broadcast_project_updated({"project_id": "p1"})

    events, _next_cursor, _evicted = webui.ui_queue.events_since(0)
    assert len(events) == 1
    assert events[0]["backend_id"] == "custom-backend-xyz"
    assert events[0]["backend_id"] == webui.tenant_context.backend_id
