"""Issue #2103 ui-2b: conversations route (picker + labels), session-less directory context."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.relay.adapter import ConversationInfo, RelayError
from backend.relay.directory import entry_label
from backend.routers import relay as relay_router
from backend.tests.relay_support import (
    CH_BLOCKED,
    CH_UNLISTED,
    OTHER_DM,
    OTHER_USER,
    OWNER,
    SELF_DM,
    make_env,
)


def make_client(tmp_path, **kw):
    env = make_env(tmp_path, **kw)

    async def get_relay(rid):
        return object() if rid == "relay1" else None

    webui = SimpleNamespace(service=SimpleNamespace(
        relay_manager=SimpleNamespace(get_relay=get_relay),
        relay_gate=env.gate, relay_audit=env.audit, relay_service=None,
    ))
    app = FastAPI()
    app.include_router(relay_router.build_router(webui))
    return env, TestClient(app)


def test_blocked_and_unlisted_both_listed_without_policies(tmp_path):
    env, client = make_client(tmp_path)
    body = client.get("/api/relays/relay1/conversations?kinds=public").json()
    ids = {e["id"] for e in body["entries"]}
    assert CH_BLOCKED in ids and CH_UNLISTED in ids  # the agent tool would hide the blocked one
    assert all("policy" not in e for e in body["entries"])
    assert {"id", "kind", "name", "label"} <= set(body["entries"][0])


def test_query_filters_and_labels(tmp_path):
    env, client = make_client(tmp_path)
    body = client.get("/api/relays/relay1/conversations?q=unlisted").json()
    assert [e["id"] for e in body["entries"]] == [CH_UNLISTED]
    assert body["entries"][0]["label"] == "#unlisted-ch"


def test_dm_labels_and_self_dm(tmp_path):
    env, client = make_client(tmp_path)
    entries = client.get("/api/relays/relay1/conversations?kinds=dm").json()["entries"]
    by_id = {e["id"]: e for e in entries}
    assert by_id[SELF_DM]["label"] == "your self-DM"
    assert by_id[OTHER_DM]["label"] == "DM with Alice Example"


def test_person_needs_query_and_carries_dm_id(tmp_path):
    env, client = make_client(tmp_path)
    r = client.get("/api/relays/relay1/conversations?kinds=person")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "query_required"
    people = client.get("/api/relays/relay1/conversations?kinds=person&q=alice").json()["entries"]
    assert people[0]["id"] == OTHER_USER and people[0]["dm_id"] == OTHER_DM
    env.adapter.user_list.append(
        __import__("backend.relay.adapter", fromlist=["UserInfo"]).UserInfo(
            id="U0NODM0001", name="Bob Nodm", display_name="Bob Nodm", handle="bob")
    )
    env.gate.directory._snapshots.clear()
    people = client.get("/api/relays/relay1/conversations?kinds=person&q=bob").json()["entries"]
    assert people[0]["dm_id"] is None


def test_invalid_kind_and_cursor_are_400(tmp_path):
    env, client = make_client(tmp_path)
    assert client.get("/api/relays/relay1/conversations?kinds=nope").status_code == 400
    assert client.get("/api/relays/relay1/conversations?cursor=zz").status_code == 400


def test_paging(tmp_path):
    env, client = make_client(tmp_path)
    first = client.get("/api/relays/relay1/conversations?kinds=public&limit=2").json()
    assert len(first["entries"]) == 2 and first["next_cursor"]
    second = client.get(
        f"/api/relays/relay1/conversations?kinds=public&limit=2&cursor={first['next_cursor']}"
    ).json()
    assert not {e["id"] for e in first["entries"]} & {e["id"] for e in second["entries"]}


def test_slack_error_maps_to_502_with_safe_code(tmp_path):
    env, client = make_client(tmp_path)
    env.adapter.channels_error = "ratelimited"
    r = client.get("/api/relays/relay1/conversations")
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "ratelimited"
    assert "xox" not in r.text


def test_missing_relay_404_and_no_token_409(tmp_path):
    env, client = make_client(tmp_path)
    assert client.get("/api/relays/nope/conversations").status_code == 404
    env.gate.relay_manager.has_token = lambda *a, **k: False
    assert client.get("/api/relays/relay1/conversations").status_code == 409


def test_names_are_cleaned(tmp_path):
    env, client = make_client(tmp_path)
    env.adapter.extra_channels = [
        ConversationInfo(id="C0EVIL0001", name="ev‮il​-name", is_channel=True)
    ]
    entries = client.get("/api/relays/relay1/conversations?q=evil").json()["entries"]
    assert entries[0]["name"] == "evil-name"


def test_warm_snapshot_makes_no_slack_calls_while_typing(tmp_path):
    env, client = make_client(tmp_path)
    client.get("/api/relays/relay1/conversations?q=a")
    before = len(env.adapter.calls)
    for q in ("au", "aut", "auto"):
        client.get(f"/api/relays/relay1/conversations?q={q}")
    assert len(env.adapter.calls) == before


def test_labels_by_ids(tmp_path):
    env, client = make_client(tmp_path)
    r = client.get(f"/api/relays/relay1/conversations?ids={SELF_DM},{CH_BLOCKED},{OTHER_DM}")
    assert r.json()["labels"] == {
        SELF_DM: "your self-DM", CH_BLOCKED: "#blocked-ch", OTHER_DM: "DM with Alice Example",
    }


def test_labels_fall_back_to_id_and_validate(tmp_path):
    env, client = make_client(tmp_path)
    env.adapter.fail_lookups = True
    r = client.get("/api/relays/relay1/conversations?ids=C0UNKNOWN01")
    assert r.json()["labels"] == {"C0UNKNOWN01": "C0UNKNOWN01"}
    assert client.get("/api/relays/relay1/conversations?ids=bad").status_code == 400
    many = ",".join(f"C0AAAAA{i:03d}" for i in range(51))
    assert client.get(f"/api/relays/relay1/conversations?ids={many}").status_code == 400


def test_agent_search_has_no_dm_id(tmp_path):
    import asyncio

    env, _ = make_client(tmp_path)

    async def run():
        ctx = await env.gate.context("s1")
        return await env.gate.directory.search(ctx, "alice", ("person",), 50, None, {})

    page = asyncio.run(run())
    assert "dm_id" not in page.entries[0]


def test_relay_context_none_without_token(tmp_path):
    env, _ = make_client(tmp_path)
    assert env.gate.relay_context("relay1").relay_id == "relay1"
    assert env.gate.relay_context("nope") is None


@pytest.mark.parametrize("row,expected", [
    ({"kind": "public", "name": "x", "id": "C1"}, "#x"),
    ({"kind": "public", "name": None, "id": "C1"}, "C1"),
    ({"kind": "group_dm", "name": "a-b", "id": "G1"}, "group DM a-b"),
    ({"kind": "dm", "name": None, "user": "U9", "id": "D1"}, "DM with U9"),
    ({"kind": "dm", "name": "Me", "user": OWNER, "id": "D1"}, "your self-DM"),
])
def test_entry_label(row, expected):
    assert entry_label(row, OWNER) == expected


def test_relay_error_class_used():
    assert RelayError("x").code == "x"
