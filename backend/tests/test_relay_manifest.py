"""Issue #2103 UI stage 2a: Slack manifest, scope drift guard, relay_not_attached reason."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.relay import slack_adapter, slack_scopes
from backend.relay.slack_scopes import (
    BOT_SCOPES,
    METHOD_SCOPES,
    USER_SCOPES_REQUIRED,
    build_manifest,
)
from backend.relay_manager import RelayRecord
from backend.restart_required import (
    RELAY_NOT_ATTACHED_REASON,
    build_launch_snapshot,
    compute_restart_reasons,
)
from backend.routers import relay as relay_router
from backend.session_config import SessionConfig


def _adapter_methods() -> set[str]:
    src = Path(slack_adapter.__file__).read_text()
    return set(re.findall(r'(?:_call|_paginate)\(\s*"([\w.]+)"', src))


def test_every_adapter_method_has_a_scope_entry_and_no_scope_is_unused():
    methods = _adapter_methods()
    assert "chat.postMessage" in methods and "files.completeUploadExternal" in methods
    assert methods == set(METHOD_SCOPES)
    needed = {s for m in methods for s in METHOD_SCOPES[m]}
    assert needed == set(USER_SCOPES_REQUIRED)


def test_manifest_shape_and_no_bot():
    m = build_manifest()
    assert m["display_information"]["name"] == "ccWebUI Relay"
    assert len(m["display_information"]["description"]) <= 140
    assert "owner" in m["display_information"]["description"]
    assert m["oauth_config"]["scopes"]["user"] == list(USER_SCOPES_REQUIRED)
    assert "bot" not in m["oauth_config"]["scopes"] and not BOT_SCOPES
    assert "features" not in m
    assert m["settings"]["socket_mode_enabled"] is True
    assert set(m["settings"]["event_subscriptions"]["user_events"]) == {
        "message.channels", "message.groups", "message.im", "message.mpim"}
    no_inbound = build_manifest(include_inbound=False)
    assert no_inbound["settings"]["socket_mode_enabled"] is False
    assert "event_subscriptions" not in no_inbound["settings"]


def test_manifest_contains_no_relay_record_fields():
    record = RelayRecord(relay_id="rid-123456", name="My Slack", adapter_type="slack")
    text = json.dumps(build_manifest())
    for key in record.to_dict():
        if key == "name":  # the manifest's own display name, not the relay's
            continue
        assert f'"{key}"' not in text
    assert "rid-123456" not in text and "xox" not in text and "xapp" not in text
    assert "My Slack" not in text


def test_manifest_route():
    async def get_relay(rid):
        return object() if rid == "r1" else None

    webui = SimpleNamespace(service=SimpleNamespace(
        relay_manager=SimpleNamespace(get_relay=get_relay)))
    app = FastAPI()
    app.include_router(relay_router.build_router(webui))
    client = TestClient(app)
    body = client.get("/api/relays/r1/manifest").json()
    assert json.loads(body["manifest_json"]) == body["manifest"]
    assert [s["name"] for s in body["scopes"]["required"]] == list(USER_SCOPES_REQUIRED)
    assert any(s["kind"] == "event" for s in body["scopes"]["inbound"])
    assert all(s["reason"] for g in body["scopes"].values() for s in g)
    assert body["steps"]
    lean = client.get("/api/relays/r1/manifest?include_inbound=false").json()
    assert "event_subscriptions" not in lean["manifest"]["settings"]
    assert client.get("/api/relays/nope/manifest").status_code == 404


def test_scope_module_has_no_runtime_dependencies_on_relay_state():
    assert slack_scopes.APP_NAME == "ccWebUI Relay"


def test_relay_not_attached_reason_set_and_cleared():
    none_cfg = SessionConfig()
    snap = build_launch_snapshot(none_cfg, litellm_routed=False, live_alias=None,
                                 wants_compact_window=False, compact_window=None,
                                 relay_wanted=False)
    kw = dict(new_compact_window=None, alias_in_live_router=True)
    assert compute_restart_reasons(snap, none_cfg, **kw) == []
    attached = SessionConfig(relay_id="r1")
    assert compute_restart_reasons(snap, attached, **kw) == [RELAY_NOT_ATTACHED_REASON]
    # switching relay or clearing it on a session that had the server is live: no reason
    snap2 = build_launch_snapshot(attached, litellm_routed=False, live_alias=None,
                                  wants_compact_window=False, compact_window=None,
                                  relay_wanted=True)
    assert compute_restart_reasons(snap2, SessionConfig(relay_id="r2"), **kw) == []
    assert compute_restart_reasons(snap2, none_cfg, **kw) == []
    # reverting clears
    assert compute_restart_reasons(snap, none_cfg, **kw) == []


def test_oauth_requests_exactly_the_manifest_scopes():
    from backend.relay_manager import SLACK_USER_SCOPES

    assert list(SLACK_USER_SCOPES) == list(USER_SCOPES_REQUIRED)
