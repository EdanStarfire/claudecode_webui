"""Issue #2377: relay-wide default footer, overridable per profile, template and session."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models.relay import RelayRecord
from backend.relay.footer import DEFAULT_FOOTER_TEMPLATE
from backend.relay.service import RelayService
from backend.relay_manager import UNSET, RelayManager
from backend.routers import relay as relay_router
from backend.tests.relay_support import CH_AUTO, make_env


class _Vault:
    def secret_exists(self, name):
        return True


class _Templates:
    def __init__(self, config=None):
        self.t = SimpleNamespace(template_id="t1", profile_ids={"relay": "p1"}, config=config or {})

    async def get_template(self, tid):
        return self.t if tid == "t1" else None


class _Profiles:
    def __init__(self, config=None):
        self.p = SimpleNamespace(config=config or {})

    async def get_profile(self, pid):
        return self.p if pid == "p1" else None


def _service(relay=None, template=None, profile=None):
    relays = {"r1": relay} if relay else {}
    return RelayService(
        SimpleNamespace(relays=relays), None, None, _Templates(template), _Profiles(profile)
    )


def _info(**cfg):
    return SimpleNamespace(template_id="t1", config={"relay_id": "r1", **cfg})


def _relay(**kw):
    return RelayRecord(relay_id="r1", name="Slack", **kw)


async def test_relay_default_is_lowest_tier():
    svc = _service(_relay(default_footer_enabled=True, default_footer_template="R {session_name}"))
    a = await svc.effective_attachment(_info())
    assert a["relay_footer_template"] == "R {session_name}" and a["relay_footer_enabled"] is True
    assert a["sources"]["relay_footer_template"] == "relay"
    assert a["sources"]["relay_footer_enabled"] == "relay"


@pytest.mark.parametrize("tier", ["profile", "template", "session"])
async def test_each_tier_overrides_relay_default(tier):
    relay = _relay(default_footer_enabled=True, default_footer_template="R")
    cfg = {"relay_footer_template": "T", "relay_footer_enabled": False}
    svc = _service(
        relay, template=cfg if tier == "template" else None, profile=cfg if tier == "profile" else None
    )
    a = await svc.effective_attachment(_info(**(cfg if tier == "session" else {})))
    assert a["relay_footer_template"] == "T" and a["relay_footer_enabled"] is False
    assert a["sources"]["relay_footer_template"] == tier


async def test_relay_off_session_on():
    svc = _service(_relay(default_footer_enabled=False))
    a = await svc.effective_attachment(_info(relay_footer_enabled=True))
    assert a["relay_footer_enabled"] is True and a["sources"]["relay_footer_enabled"] == "session"


async def test_no_relay_default_keeps_unset():
    a = await _service(_relay()).effective_attachment(_info())
    assert a["relay_footer_template"] is None and a["relay_footer_enabled"] is None
    assert "relay_footer_template" not in a["sources"]


async def test_blank_tier_template_falls_through():
    svc = _service(_relay(default_footer_template="R"), template={"relay_footer_template": "  "})
    a = await svc.effective_attachment(_info(relay_footer_template=""))
    assert a["relay_footer_template"] == "R" and a["sources"]["relay_footer_template"] == "relay"
    a = await _service(_relay()).effective_attachment(_info(relay_footer_template=" "))
    assert a["relay_footer_template"] is None


async def test_dangling_relay_id_has_no_fallback():
    a = await _service(None).effective_attachment(_info())
    assert a["relay_footer_template"] is None and a["relay_footer_enabled"] is None


async def test_default_follows_the_effective_relay():
    svc = _service(_relay(default_footer_template="R"))
    a = await svc.effective_attachment(_info(relay_id="other"))
    assert a["relay_footer_template"] is None


async def test_get_session_relay_exposes_defaults():
    svc = _service(_relay(default_footer_enabled=False, default_footer_template="R"))

    async def get_info(sid):
        return _info()

    svc._sessions = SimpleNamespace(get_session_info=get_info)
    out = await svc.get_session_relay("s1")
    assert out["relay_default_footer"] == {"enabled": False, "template": "R"}
    assert out["built_in_footer_template"] == DEFAULT_FOOTER_TEMPLATE
    assert out["sources"]["relay_footer_enabled"] == "relay"


# ------------------------------------------------------------------ record + manager


def test_record_round_trip_and_legacy_file():
    rec = _relay(default_footer_enabled=False, default_footer_template="X")
    back = RelayRecord.from_dict(rec.to_dict())
    assert back.default_footer_enabled is False and back.default_footer_template == "X"
    assert rec.to_public_dict()["default_footer_template"] == "X"
    legacy = rec.to_dict()
    del legacy["default_footer_enabled"], legacy["default_footer_template"]
    old = RelayRecord.from_dict(legacy)
    assert old.default_footer_enabled is None and old.default_footer_template is None


@pytest.fixture
async def mgr(tmp_path):
    m = RelayManager(tmp_path, _Vault())
    record = await m.create_relay("Slack")
    return m, record.relay_id


async def test_manager_validates_before_changing(mgr):
    m, rid = mgr
    await m.update_relay(rid, default_footer_template="Hello {session_name}", default_footer_enabled=False)
    for bad in ("ping !deploy", "{nope}", "!{session_handle}", "x" * 501):
        with pytest.raises(ValueError):
            await m.update_relay(rid, name="Changed", default_footer_template=bad)
    rec = await m.get_relay(rid)
    assert rec.default_footer_template == "Hello {session_name}" and rec.name != "Changed"
    with pytest.raises(ValueError):
        await m.update_relay(rid, default_footer_enabled="yes")


async def test_manager_absent_leaves_null_clears_blank_is_unset(mgr):
    m, rid = mgr
    await m.update_relay(rid, default_footer_template="T", default_footer_enabled=True)
    rec = await m.update_relay(rid, name="Renamed")
    assert rec.default_footer_template == "T" and rec.default_footer_enabled is True
    rec = await m.update_relay(rid, default_footer_template="   ")
    assert rec.default_footer_template is None and rec.default_footer_enabled is True
    await m.update_relay(rid, default_footer_template="T")
    rec = await m.update_relay(rid, default_footer_template=None, default_footer_enabled=None)
    assert rec.default_footer_template is None and rec.default_footer_enabled is None
    assert UNSET is not None


async def test_persisted_and_reloaded(mgr, tmp_path):
    m, rid = mgr
    await m.update_relay(rid, default_footer_template="T", default_footer_enabled=False)
    files = list((tmp_path / "relays").glob("*.json"))
    assert json.loads(files[0].read_text())["default_footer_template"] == "T"


def _client(m):
    webui = SimpleNamespace(service=SimpleNamespace(relay_manager=m, relay_service=None))
    app = FastAPI()
    app.include_router(relay_router.build_router(webui))
    return TestClient(app)


async def test_route_round_trip_invalid_and_null(mgr):
    m, rid = mgr
    client = _client(m)
    r = client.put(f"/api/relays/{rid}", json={"default_footer_template": "Hi", "default_footer_enabled": True})
    assert r.status_code == 200 and r.json()["default_footer_template"] == "Hi"
    r = client.put(f"/api/relays/{rid}", json={"name": "Other"})
    assert r.json()["default_footer_template"] == "Hi"  # absent leaves it
    r = client.put(f"/api/relays/{rid}", json={"default_footer_template": "!deploy"})
    assert r.status_code == 400
    assert client.get(f"/api/relays/{rid}").json()["default_footer_template"] == "Hi"
    r = client.put(f"/api/relays/{rid}", json={"default_footer_template": None})
    assert r.json()["default_footer_template"] is None and r.json()["default_footer_enabled"] is True


# ------------------------------------------------------------------ outbound


def _real_attachment(env, record):
    real = RelayService(SimpleNamespace(relays={"relay1": record}), None, None, _Templates(), _Profiles())
    env.service.effective_attachment = lambda info: real.effective_attachment(
        SimpleNamespace(template_id=None, config=info.config)
    )
    env.gate.relay_manager.relays["relay1"].default_footer_template = record.default_footer_template


def _footer(env):
    return env.adapter.network_calls()[-1][3][-1]["elements"][0]["text"]


async def test_post_reply_upload_use_relay_default_live(tmp_path):
    env = make_env(tmp_path)
    record = _relay(default_footer_template="RELAY {session_name}")
    _real_attachment(env, record)
    await env.tools.handle_post("s1", "post", {"channel": CH_AUTO, "text": "a"})
    assert _footer(env) == "RELAY My Session"
    await env.tools.handle_post("s1", "reply", {"channel": CH_AUTO, "text": "b", "thread_ts": "1700000000.000100"})
    assert _footer(env) == "RELAY My Session"
    f = env.workdir / "a.txt"
    f.write_text("d")
    await env.tools.handle_upload("s1", {"channel": CH_AUTO, "path": "a.txt", "comment": "hi"})
    assert "RELAY My Session" in env.adapter.network_calls()[-1][4]
    record.default_footer_template = "EDITED"  # live: no restart
    await env.tools.handle_post("s1", "post", {"channel": CH_AUTO, "text": "c"})
    assert _footer(env) == "EDITED"
    record.default_footer_enabled = False
    await env.tools.handle_post("s1", "post", {"channel": CH_AUTO, "text": "d"})
    assert env.adapter.network_calls()[-1][3] is None
    record.default_footer_enabled = None
    record.default_footer_template = None
    await env.tools.handle_post("s1", "post", {"channel": CH_AUTO, "text": "e"})
    assert "via Claude WebUI" in _footer(env)


def test_frontend_built_in_footer_constant_matches_backend():
    from pathlib import Path

    from backend.relay.footer import FOOTER_MAX_LENGTH

    js = (Path(__file__).resolve().parents[2] / "frontend/src/utils/relayFooter.js").read_text()
    assert f"'{DEFAULT_FOOTER_TEMPLATE}'" in js
    assert f"FOOTER_MAX_LENGTH = {FOOTER_MAX_LENGTH}" in js
