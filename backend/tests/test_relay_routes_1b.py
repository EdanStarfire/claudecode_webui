"""Issue #2103 outbound-1b: audit endpoint, relay_choice pass-through, PermissionInfo field."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models.messages import PermissionInfo
from backend.relay.audit import RelayAuditLog
from backend.routers import relay as relay_router
from backend.routers import session_runtime


def test_permission_info_relay_prompt_round_trip():
    prompt = {"kind": "ask_send", "channel_id": "C0ASK00001"}
    info = PermissionInfo(message="m", relay_prompt=prompt)
    assert PermissionInfo.from_dict(info.to_dict()).relay_prompt == prompt
    assert "relay_prompt" not in PermissionInfo(message="m").to_dict()


def test_audit_endpoint(tmp_path):
    import asyncio

    audit = RelayAuditLog(tmp_path)
    for i in range(3):
        asyncio.run(audit.record(
            "relay1", session_id="s1" if i else "s2", channel_id=f"C{i}", tool="post", outcome="ok",
            ts=f"2026-01-01T00:00:0{i}+00:00",
        ))

    async def get_relay(rid):
        return object() if rid == "relay1" else None

    webui = SimpleNamespace(service=SimpleNamespace(
        relay_manager=SimpleNamespace(get_relay=get_relay), relay_audit=audit,
        relay_service=None,
    ))
    app = FastAPI()
    app.include_router(relay_router.build_router(webui))
    client = TestClient(app)
    recs = client.get("/api/relays/relay1/audit").json()["records"]
    assert [r["channel_id"] for r in recs] == ["C2", "C1", "C0"]
    assert len(client.get("/api/relays/relay1/audit?session_id=s2").json()["records"]) == 1
    assert len(client.get("/api/relays/relay1/audit?limit=1").json()["records"]) == 1
    assert client.get("/api/relays/nope/audit").status_code == 404


def test_permission_endpoint_forwards_relay_choice():
    resolved = {}
    service = SimpleNamespace(
        has_pending_permission=lambda rid: True,
        resolve_permission=lambda rid, resp: resolved.update(resp) or True,
    )
    app = FastAPI()
    app.include_router(session_runtime.build_router(SimpleNamespace(service=service)))
    client = TestClient(app)
    r = client.post(
        "/api/sessions/s1/permission/req1",
        json={"decision": "allow", "relay_choice": {"promote": True}},
    )
    assert r.status_code == 200
    assert resolved["relay_choice"] == {"policy": None, "promote": True}
    resolved.clear()
    client.post("/api/sessions/s1/permission/req1", json={"decision": "deny",
                "relay_choice": {"policy": "auto"}})
    assert "relay_choice" not in resolved
