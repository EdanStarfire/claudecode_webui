"""Issue #2103 outbound-1a: relay REST API, vault integration and live policy edits.

Slack is a httpx.MockTransport; the OS keyring is an in-memory dict.
"""

import json
import re

import httpx
import pytest

from backend.relay.slack_adapter import SlackAdapter

USER_TOKEN = "xoxp-1111-2222-SECRETVALUE"
APP_TOKEN = "xapp-1-A111-SECRETAPPVALUE"
CHANNEL = "C0123456789"
OTHER = "C0999999999"


@pytest.fixture
def fake_keyring(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr("backend.credential_vault.set_secret_value", lambda n, v: store.__setitem__(n, v))
    monkeypatch.setattr("backend.credential_vault.get_secret_value", lambda n: store.get(n))
    monkeypatch.setattr("backend.credential_vault.delete_secret_value", lambda n: store.pop(n, None))
    monkeypatch.setattr("backend.relay_manager.get_secret_value", lambda n: store.get(n))
    return store


@pytest.fixture
async def relay_env(api_integration_env, fake_keyring):
    env = api_integration_env
    seen_auth = []

    def slack(request: httpx.Request):
        seen_auth.append(request.headers.get("Authorization"))
        if str(request.url).endswith("auth.test"):
            if request.headers.get("Authorization") != f"Bearer {USER_TOKEN}":
                return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
            return httpx.Response(200, json={"ok": True, "user_id": "U01OWNER", "team_id": "T01", "team": "edanstestingrig"})
        return httpx.Response(200, json={"ok": True})

    manager = env["coordinator"].relay_manager
    transport = httpx.MockTransport(slack)
    manager._adapter_factory = lambda provider: SlackAdapter(provider, transport=transport)
    manager._http_transport = transport
    env["keyring"] = fake_keyring
    env["seen_auth"] = seen_auth
    return env


async def _make_session(env, name="Planner"):
    project = await env["create_test_project"]()
    return await env["create_test_session"](project["project_id"], name=name)


@pytest.mark.asyncio
async def test_issue_2103_relay_create_one_per_backend_and_no_token_echo(relay_env):
    client = relay_env["client"]
    r = await client.post("/api/relays", json={"name": "Rig", "oauth_client_id": "123.456",
                                               "oauth_client_secret": "client-secret-value"})
    assert r.status_code == 201, r.text
    relay = r.json()
    assert relay["has_user_token"] is False and "user_token_secret" not in relay
    assert (await client.post("/api/relays", json={"name": "Second"})).status_code == 409

    r = await client.put(f"/api/relays/{relay['relay_id']}/tokens",
                         json={"user_token": USER_TOKEN, "app_token": APP_TOKEN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_user_token"] and body["has_app_token"]
    assert body["owner_user_id"] == "U01OWNER" and body["workspace_name"] == "edanstestingrig"

    # No endpoint returns a token value or a secret name
    for path in ("/api/relays", f"/api/relays/{relay['relay_id']}"):
        text = (await client.get(path)).text
        for needle in (USER_TOKEN, APP_TOKEN, "client-secret-value", "relay-"):
            assert needle not in text
    assert USER_TOKEN not in r.text and APP_TOKEN not in r.text
    # Nor the on-disk relay record
    files = list((relay_env["data_dir"] / "relays").glob("*.json"))
    assert len(files) == 1
    disk = files[0].read_text()
    assert USER_TOKEN not in disk and APP_TOKEN not in disk
    assert json.loads(disk)["user_token_secret"] == f"relay-{relay['relay_id']}-user-token"
    # Values live in the vault (keyring), not the secrets API listing
    assert relay_env["keyring"][f"relay-{relay['relay_id']}-user-token"] == USER_TOKEN

    r = await client.post(f"/api/relays/{relay['relay_id']}/test")
    assert r.json()["ok"] is True and USER_TOKEN not in r.text
    # The adapter used the stored token
    assert f"Bearer {USER_TOKEN}" in relay_env["seen_auth"]


@pytest.mark.asyncio
async def test_issue_2103_tokens_validation_and_bad_token_not_stored(relay_env):
    client = relay_env["client"]
    relay = (await client.post("/api/relays", json={"name": "Rig"})).json()
    url = f"/api/relays/{relay['relay_id']}/tokens"
    assert (await client.put(url, json={"user_token": "xoxb-wrong-prefix"})).status_code == 400
    assert (await client.put(url, json={"app_token": "xoxp-wrong"})).status_code == 400
    assert (await client.put(url, json={})).status_code == 400
    r = await client.put(url, json={"user_token": "xoxp-rejected-by-slack"})
    assert r.status_code == 400 and "invalid_auth" in r.text and "rejected-by-slack" not in r.text
    assert (await client.get(f"/api/relays/{relay['relay_id']}")).json()["has_user_token"] is False
    assert not relay_env["keyring"]
    assert (await client.put("/api/relays/missing/tokens", json={"user_token": USER_TOKEN})).status_code == 404


@pytest.mark.asyncio
async def test_issue_2103_delete_relay_cleans_vault(relay_env):
    client = relay_env["client"]
    relay = (await client.post("/api/relays", json={"name": "Rig"})).json()
    await client.put(f"/api/relays/{relay['relay_id']}/tokens",
                     json={"user_token": USER_TOKEN, "app_token": APP_TOKEN})
    assert len(relay_env["keyring"]) == 2
    assert (await client.delete(f"/api/relays/{relay['relay_id']}")).status_code == 200
    assert relay_env["keyring"] == {}
    assert (await client.get("/api/relays")).json() == {"relays": []}
    assert (await client.delete(f"/api/relays/{relay['relay_id']}")).status_code == 404
    # A new relay may be created again
    assert (await client.post("/api/relays", json={"name": "Rig2"})).status_code == 201


@pytest.mark.asyncio
async def test_issue_2103_relay_secrets_not_assignable(relay_env):
    client = relay_env["client"]
    project = await relay_env["create_test_project"]()
    r = await client.post("/api/sessions", json={"project_id": project["project_id"],
                                                 "assigned_secrets": ["relay-abc-user-token"]})
    assert r.status_code == 422
    session = await relay_env["create_test_session"](project["project_id"])
    r = await client.patch(f"/api/sessions/{session['session_id']}",
                           json={"config": {"assigned_secrets": ["relay-abc-user-token"]}})
    assert r.status_code == 422
    r = await client.put("/api/templates/x", json={"assigned_secrets": ["relay-abc-user-token"]})
    assert r.status_code == 422
    r = await client.post("/api/profiles", json={"name": "p", "area": "isolation",
                                                 "config": {"assigned_secrets": ["relay-x"]}})
    assert r.status_code == 400
    # Defense in depth: the vault refuses to resolve one for assignment even if it got through
    vault = relay_env["coordinator"].credential_vault
    assert await vault.resolve_secrets_for_assignment(["relay-abc-user-token"]) == []


@pytest.mark.asyncio
async def test_issue_2103_policy_edit_visible_immediately_with_tier_merge(relay_env):
    client = relay_env["client"]
    coordinator = relay_env["coordinator"]
    # profile (relay area) + template + session tiers
    profile = (await client.post("/api/profiles", json={
        "name": "relay-base", "area": "relay",
        "config": {"relay_channel_policies": {CHANNEL: "read_only", OTHER: "blocked"}},
    })).json()
    template = (await client.post("/api/templates", json={
        "name": "T", "profile_ids": {"relay": profile["profile_id"]},
        "config": {"relay_channel_policies": {CHANNEL: "ask"}},
    })).json()
    project = await relay_env["create_test_project"]()
    session = await relay_env["create_test_session"](project["project_id"], template_id=template["template_id"])
    sid = session["session_id"]

    got = (await client.get(f"/api/sessions/{sid}/relay")).json()
    assert got["policies"] == {
        CHANNEL: {"policy": "ask", "source": "template"},
        OTHER: {"policy": "blocked", "source": "profile"},
    }

    # Session promotion of one channel keeps the other tiers' entries (per-key merge)
    r = await client.put(f"/api/sessions/{sid}/relay/policies/{CHANNEL}", json={"policy": "auto"})
    assert r.status_code == 200, r.text
    assert r.json()["policies"][CHANNEL] == {"policy": "auto", "source": "session"}
    assert r.json()["policies"][OTHER]["source"] == "profile"

    # effective_policies (what 1b's gate reads) sees it with no restart
    info = await coordinator.session_manager.get_session_info(sid)
    eff = await coordinator.relay_service.effective_policies(info)
    assert eff[CHANNEL].policy == "auto" and eff[OTHER].policy == "blocked"

    # Template edit is also live (no start-time snapshot)
    await client.put(f"/api/templates/{template['template_id']}",
                     json={"config": {"relay_channel_policies": {CHANNEL: "read_only", "C0555555555": "ask"}}})
    eff = await coordinator.relay_service.effective_policies(info)
    assert eff["C0555555555"].policy == "ask" and eff[CHANNEL].policy == "auto"

    # DELETE removes the session key, falling back to the lower tier
    r = await client.delete(f"/api/sessions/{sid}/relay/policies/{CHANNEL}")
    assert r.json()["policies"][CHANNEL] == {"policy": "read_only", "source": "template"}

    # Validation
    assert (await client.put(f"/api/sessions/{sid}/relay/policies/general", json={"policy": "auto"})).status_code == 422
    assert (await client.put(f"/api/sessions/{sid}/relay/policies/{CHANNEL}", json={"policy": "yes"})).status_code == 422
    assert (await client.put("/api/sessions/nope/relay/policies/" + CHANNEL, json={"policy": "auto"})).status_code == 404


@pytest.mark.asyncio
async def test_issue_2103_handle_assignment_duplicates_and_rename_stability(relay_env):
    client = relay_env["client"]
    coordinator = relay_env["coordinator"]
    relay = (await client.post("/api/relays", json={"name": "Rig"})).json()
    project = await relay_env["create_test_project"]()
    s1 = await relay_env["create_test_session"](project["project_id"], name="Planner",
                                                relay_id=relay["relay_id"])
    s2 = await relay_env["create_test_session"](project["project_id"], name="Planner",
                                                relay_id=relay["relay_id"])
    svc = coordinator.relay_service
    assert await svc.sync_session(s1["session_id"]) == "planner"
    assert await svc.sync_session(s2["session_id"]) == "planner-2"
    assert (await client.get(f"/api/sessions/{s1['session_id']}/relay")).json()["relay_handle"] == "planner"

    # Rename leaves the handle unchanged (existing slug logic is untouched)
    r = await client.put(f"/api/sessions/{s1['session_id']}/name", json={"name": "Totally Different"})
    assert r.status_code == 200
    info = await coordinator.session_manager.get_session_info(s1["session_id"])
    assert info.slug == "totally-different" and info.config["relay_handle"] == "planner"
    assert await svc.sync_session(s1["session_id"]) == "planner"

    # Duplicate edit -> 409; invalid / reserved -> 422; free edit works
    url = f"/api/sessions/{s2['session_id']}/relay/handle"
    assert (await client.put(url, json={"handle": "planner"})).status_code == 409
    assert (await client.put(url, json={"handle": "Bad_Handle"})).status_code == 422
    assert (await client.put(url, json={"handle": "here"})).status_code == 422
    assert (await client.put(url, json={"handle": "builder"})).json() == {"relay_handle": "builder"}
    info2 = await coordinator.session_manager.get_session_info(s2["session_id"])
    assert info2.config["relay_handle"] == "builder"

    # Whole-config PATCH: duplicate -> 409, omission keeps the stable handle
    cfg_url = f"/api/sessions/{s2['session_id']}"
    assert (await client.patch(cfg_url, json={"config": {"relay_handle": "planner"}})).status_code == 409
    assert (await client.patch(cfg_url, json={"config": {"relay_id": relay["relay_id"]}})).status_code == 200
    info2 = await coordinator.session_manager.get_session_info(s2["session_id"])
    assert info2.config["relay_handle"] == "builder"

    # ...and so do runtime-set policies
    await client.put(f"/api/sessions/{s2['session_id']}/relay/policies/{CHANNEL}", json={"policy": "auto"})
    assert (await client.patch(cfg_url, json={"config": {"relay_id": relay["relay_id"]}})).status_code == 200
    info2 = await coordinator.session_manager.get_session_info(s2["session_id"])
    assert info2.config["relay_channel_policies"] == {CHANNEL: "auto"}

    # Deleting the session frees the handle
    assert (await client.delete(f"/api/sessions/{s1['session_id']}")).status_code == 200
    assert coordinator.relay_service.handles.get_session("planner") is None
    assert (await client.put(url, json={"handle": "planner"})).status_code == 200


@pytest.mark.asyncio
async def test_issue_2103_relay_fields_validated_at_every_tier_and_handle_session_only(relay_env):
    client = relay_env["client"]
    r = await client.post("/api/profiles", json={"name": "bad", "area": "relay",
                                                 "config": {"relay_handle": "nope"}})
    assert r.status_code == 400
    r = await client.post("/api/profiles", json={"name": "bad2", "area": "relay",
                                                 "config": {"relay_footer_template": "hi !deploy"}})
    assert r.status_code == 400
    r = await client.post("/api/templates", json={"name": "T2", "config": {"relay_channel_policies": {"x": "auto"}}})
    assert r.status_code == 400
    project = await relay_env["create_test_project"]()
    r = await client.post("/api/sessions", json={"project_id": project["project_id"],
                                                 "relay_footer_template": "Ping !bot"})
    assert r.status_code == 422
    # Valid footer/policies round-trip through the profile and template endpoints
    r = await client.post("/api/profiles", json={"name": "ok", "area": "relay", "config": {
        "relay_id": "r1", "relay_footer_enabled": True,
        "relay_footer_template": "Sent by {session_name} ({session_handle})"}})
    assert r.status_code == 201, r.text
    assert re.search("session_handle", r.text)


@pytest.mark.asyncio
async def test_issue_2103_oauth_initiate_builds_slack_url_and_rejects_http_remote(relay_env):
    client = relay_env["client"]
    relay = (await client.post("/api/relays", json={
        "name": "Rig", "oauth_client_id": "123.456", "oauth_client_secret": "shh"})).json()
    base = f"/api/relays/{relay['relay_id']}/oauth"
    bad = await client.post(f"{base}/initiate", json={"redirect_uri": "http://example.com/oauth/callback"})
    assert bad.status_code == 400 and "https" in bad.text
    ok = await client.post(f"{base}/initiate", json={"redirect_uri": "http://localhost:8000/oauth/callback"})
    assert ok.status_code == 200
    url = ok.json()["auth_url"]
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "user_scope=" in url and "client_id=123.456" in url and "code_challenge" not in url
    assert "&scope=" not in url and "shh" not in url
    state = ok.json()["state"]
    assert (await client.get(f"{base}/status", params={"state": state})).json() == {"status": "pending"}


@pytest.mark.asyncio
async def test_issue_2103_oauth_callback_exchanges_and_stores_user_token(relay_env):
    client = relay_env["client"]
    manager = relay_env["coordinator"].relay_manager

    def slack(request: httpx.Request):
        url = str(request.url)
        if url.endswith("oauth.v2.access"):
            form = dict(p.split("=", 1) for p in request.content.decode().split("&"))
            assert form["code"] == "the-code" and "code_verifier" not in form
            return httpx.Response(200, json={"ok": True, "authed_user": {"access_token": USER_TOKEN}})
        if url.endswith("auth.test"):
            return httpx.Response(200, json={"ok": True, "user_id": "U01OWNER", "team_id": "T01", "team": "rig"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(slack)
    manager._http_transport = transport
    manager._adapter_factory = lambda provider: SlackAdapter(provider, transport=transport)

    relay = (await client.post("/api/relays", json={
        "name": "Rig", "oauth_client_id": "123.456", "oauth_client_secret": "shh"})).json()
    init = (await client.post(f"/api/relays/{relay['relay_id']}/oauth/initiate",
                              json={"redirect_uri": "http://localhost:8000/oauth/callback"})).json()
    listener = relay_env["coordinator"].oauth_callback_listener_manager
    assert await listener.complete_and_broadcast(init["state"], "the-code") == relay["relay_id"]
    got = (await client.get(f"/api/relays/{relay['relay_id']}")).json()
    assert got["has_user_token"] and got["owner_user_id"] == "U01OWNER"
    assert relay_env["keyring"][f"relay-{relay['relay_id']}-user-token"] == USER_TOKEN
    status = (await client.get(f"/api/relays/{relay['relay_id']}/oauth/status", params={"state": init["state"]})).json()
    assert status["status"] == "complete"
    # State is single-use
    with pytest.raises(ValueError):
        await listener.complete_and_broadcast(init["state"], "the-code")


@pytest.mark.asyncio
async def test_issue_2103_oauth_denied_and_unknown_state(relay_env):
    client = relay_env["client"]
    manager = relay_env["coordinator"].relay_manager
    listener = relay_env["coordinator"].oauth_callback_listener_manager
    relay = (await client.post("/api/relays", json={
        "name": "Rig", "oauth_client_id": "123.456", "oauth_client_secret": "shh"})).json()
    init = (await client.post(f"/api/relays/{relay['relay_id']}/oauth/initiate",
                              json={"redirect_uri": "http://localhost:8000/oauth/callback"})).json()
    await listener.denied_and_broadcast(init["state"], "access_denied", "User said no")
    status = (await client.get(f"/api/relays/{relay['relay_id']}/oauth/status", params={"state": init["state"]})).json()
    assert status == {"status": "error", "error": "User said no"}
    # An unissued state is never recorded (callback query values are attacker-controlled)
    await listener.denied_and_broadcast("forged-state", "access_denied", "x")
    assert "forged-state" not in manager._oauth_results
    # A code for an unknown state falls through to the MCP path and fails without touching relays
    with pytest.raises(ValueError):
        await listener.complete_and_broadcast("forged-state", "code")
