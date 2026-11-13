"""Monitor REST routes (issue #2103 stage 3b-ii): relay- and session-qualified list / pause / delete."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.relay.service import RelayNotAttachedError, SessionNotFoundError
from backend.relay_manager import RelayNotFoundError
from backend.routers import relay as relay_router


class StubInbound:
    def __init__(self) -> None:
        self.recs = {
            "m1": {"id": "m1", "session_id": "s1", "kind": "monitor_dms"},
            "m2": {"id": "m2", "session_id": "s2", "kind": "monitor_mentions"},
        }
        self.store = SimpleNamespace(get_monitor=self.recs.get)
        self.calls: list = []

    async def public_monitors(self, session_id=None, *, status=None, kind=None, limit=100, offset=0, mode=None):
        self.calls.append(("list", session_id, status, kind, limit, offset))
        rows = [r for r in self.recs.values() if session_id in (None, r["session_id"])]
        return rows, len(rows)

    async def public_one(self, monitor_id):
        return {**self.recs[monitor_id], "paused": self.recs[monitor_id].get("paused", False)}

    async def set_paused(self, monitor_id, paused, *, source):
        self.calls.append(("pause", monitor_id, paused, source))
        self.recs[monitor_id]["paused"] = paused

    async def delete_monitor(self, monitor_id, *, source):
        self.calls.append(("delete", monitor_id, source))
        self.recs.pop(monitor_id)


@pytest.fixture
def ctx():
    inbound = StubInbound()

    async def get_relay(rid):
        return object() if rid == "relay1" else None

    async def require_attached(session_id, relay_id):
        if session_id == "nosession":
            raise SessionNotFoundError(session_id)
        if relay_id != "relay1":
            raise RelayNotFoundError(relay_id)
        if session_id == "detached":
            raise RelayNotAttachedError("not attached")

    webui = SimpleNamespace(service=SimpleNamespace(
        relay_manager=SimpleNamespace(get_relay=get_relay),
        relay_service=SimpleNamespace(require_attached=require_attached),
        relay_inbound=SimpleNamespace(service_for=lambda rid: inbound if rid == "relay1" else None),
    ))
    app = FastAPI()
    app.include_router(relay_router.build_router(webui))
    return TestClient(app), inbound


def test_list_relay_wide_and_session_qualified(ctx):
    client, inbound = ctx
    assert client.get("/api/relays/relay1/monitors").json()["total"] == 2
    r = client.get("/api/relays/relay1/monitors?session_id=s1&status=active&kind=monitor_dms&limit=5")
    assert r.status_code == 200 and r.json()["total"] == 1
    assert inbound.calls[-1] == ("list", "s1", "active", "monitor_dms", 5, 0)
    assert client.get("/api/sessions/s2/relays/relay1/monitors").json()["monitors"][0]["id"] == "m2"


def test_list_errors(ctx):
    client, _ = ctx
    assert client.get("/api/relays/nope/monitors").status_code == 404
    assert client.get("/api/sessions/nosession/relays/relay1/monitors").status_code == 404
    assert client.get("/api/sessions/s1/relays/other/monitors").status_code == 404
    assert client.get("/api/sessions/detached/relays/relay1/monitors").status_code == 409
    assert client.get("/api/relays/relay1/monitors?status=weird").status_code == 422
    assert client.get("/api/relays/relay1/monitors?kind=weird").status_code == 422


def test_inbound_not_running_is_409():
    # a relay that exists but has no inbound service
    webui = SimpleNamespace(service=SimpleNamespace(
        relay_manager=SimpleNamespace(get_relay=_exists), relay_service=None,
        relay_inbound=SimpleNamespace(service_for=lambda rid: None)))
    app = FastAPI()
    app.include_router(relay_router.build_router(webui))
    assert TestClient(app).get("/api/relays/relay1/monitors").status_code == 409


async def _exists(rid):
    return object()


def test_pause_resume_audit_source_owner_and_parity(ctx):
    client, inbound = ctx
    r = client.patch("/api/relays/relay1/monitors/m1", json={"paused": True})
    assert r.status_code == 200 and r.json()["paused"] is True
    assert inbound.calls[-1] == ("pause", "m1", True, "owner")
    r = client.patch("/api/sessions/s1/relays/relay1/monitors/m1", json={"paused": False})
    assert r.status_code == 200 and r.json()["paused"] is False
    assert inbound.calls[-1] == ("pause", "m1", False, "owner")


def test_patch_errors(ctx):
    client, _ = ctx
    assert client.patch("/api/relays/relay1/monitors/m1", json={}).status_code == 422
    assert client.patch("/api/relays/relay1/monitors/m1", json={"paused": "maybe"}).status_code == 422
    assert client.patch("/api/relays/relay1/monitors/zzz", json={"paused": True}).status_code == 404
    # another session's monitor through the session-qualified form
    assert client.patch("/api/sessions/s1/relays/relay1/monitors/m2", json={"paused": True}).status_code == 404
    assert client.patch("/api/sessions/detached/relays/relay1/monitors/m1", json={"paused": True}).status_code == 409


def test_delete(ctx):
    client, inbound = ctx
    assert client.delete("/api/sessions/s1/relays/relay1/monitors/m2").status_code == 404
    assert "m2" in inbound.recs
    assert client.delete("/api/sessions/s1/relays/relay1/monitors/m1").json() == {
        "deleted": True, "monitor_id": "m1"}
    assert inbound.calls[-1] == ("delete", "m1", "owner")
    assert client.delete("/api/relays/relay1/monitors/m2").status_code == 200
    assert client.delete("/api/relays/relay1/monitors/m2").status_code == 404
