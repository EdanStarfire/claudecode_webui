"""Issue #2103 outbound-1a: SlackAdapter on httpx.MockTransport (no network)."""

import json
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from backend.relay.adapter import RelayAdapter, RelayError
from backend.relay.slack_adapter import SlackAdapter

TOKEN = "xoxp-SECRET-TOKEN-VALUE"


def _form(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def make_adapter(handler, sleeps=None):
    async def provider():
        return TOKEN

    async def fake_sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    return SlackAdapter(provider, transport=httpx.MockTransport(handler), sleep=fake_sleep)


def test_issue_2103_adapter_satisfies_protocol():
    assert isinstance(make_adapter(lambda r: httpx.Response(200, json={"ok": True})), RelayAdapter)


@pytest.mark.asyncio
async def test_issue_2103_auth_test_sends_bearer_and_parses():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json={
            "ok": True, "user_id": "U1", "team_id": "T1", "team": "Rig", "user": "me",
        })

    info = await make_adapter(handler).auth_test()
    assert (info.user_id, info.team_id, info.team) == ("U1", "T1", "Rig")
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["url"].endswith("/api/auth.test")


@pytest.mark.asyncio
async def test_issue_2103_ok_false_maps_to_relay_error_without_token(caplog):
    caplog.set_level(logging.DEBUG)

    def handler(request):
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    with pytest.raises(RelayError) as exc:
        await make_adapter(handler).post("C1234567", "hi")
    assert exc.value.code == "channel_not_found"
    assert TOKEN not in str(exc.value) and TOKEN not in repr(exc.value)
    assert TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_issue_2103_transport_error_hides_details_and_token(caplog):
    def handler(request):
        raise httpx.ConnectError(f"boom with {TOKEN}")

    caplog.set_level(logging.DEBUG)
    with pytest.raises(RelayError) as exc:
        await make_adapter(handler).auth_test()
    assert exc.value.code == "network_error"
    assert TOKEN not in str(exc.value) and TOKEN not in caplog.text
    assert exc.value.__cause__ is None


@pytest.mark.asyncio
async def test_issue_2103_429_retry_after_then_success():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "user_id": "U1", "team_id": "T1"})

    info = await make_adapter(handler, sleeps).auth_test()
    assert info.user_id == "U1"
    assert sleeps == [3.0] and len(calls) == 2


@pytest.mark.asyncio
async def test_issue_2103_ratelimited_error_code_retries_then_gives_up():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"ok": False, "error": "ratelimited"})

    with pytest.raises(RelayError) as exc:
        await make_adapter(handler, sleeps).auth_test()
    assert exc.value.code == "ratelimited"
    assert len(calls) == 3 and len(sleeps) == 2  # bounded retries


@pytest.mark.asyncio
async def test_issue_2103_retry_after_is_capped():
    sleeps = []
    n = []

    def handler(request):
        n.append(1)
        if len(n) == 1:
            return httpx.Response(429, headers={"Retry-After": "9999"})
        return httpx.Response(200, json={"ok": True})

    await make_adapter(handler, sleeps).auth_test()
    assert sleeps == [30.0]


@pytest.mark.asyncio
async def test_issue_2103_post_json_body_with_metadata():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "channel": "C1234567", "ts": "1.2"})

    meta = {"event_type": "cc_webui_agent_message", "event_payload": {"handle": "x"}}
    res = await make_adapter(handler).post(
        "C1234567", "hello", blocks=[{"type": "section"}], thread_ts="1.1", metadata=meta
    )
    assert (res.channel, res.ts) == ("C1234567", "1.2")
    assert seen["body"] == {
        "channel": "C1234567", "text": "hello", "blocks": [{"type": "section"}],
        "thread_ts": "1.1", "metadata": meta,
    }


@pytest.mark.asyncio
async def test_issue_2103_read_history_pagination_and_metadata_param():
    pages = []

    def handler(request):
        form = _form(request)
        pages.append(form)
        if "cursor" not in form:
            return httpx.Response(200, json={
                "ok": True,
                "messages": [{"ts": "3", "text": "c", "user": "U1"}, {"ts": "2", "text": "b", "bot_id": "B1",
                             "app_id": "A1", "metadata": {"event_type": "cc_webui_agent_message"}}],
                "response_metadata": {"next_cursor": "NEXT"},
            })
        return httpx.Response(200, json={"ok": True, "messages": [{"ts": "1", "text": "a"}]})

    page = await make_adapter(handler).read_history("C1234567", limit=10)
    assert [m.ts for m in page.messages] == ["3", "2", "1"]
    assert page.messages[1].bot_id == "B1" and page.messages[1].metadata["event_type"]
    assert page.next_cursor is None
    assert all(p["include_all_metadata"] == "true" for p in pages)
    assert pages[1]["cursor"] == "NEXT"


@pytest.mark.asyncio
async def test_issue_2103_pagination_stops_at_limit_and_returns_cursor():
    def handler(request):
        return httpx.Response(200, json={
            "ok": True,
            "messages": [{"ts": str(i)} for i in range(5)],
            "response_metadata": {"next_cursor": "MORE"},
        })

    page = await make_adapter(handler).read_thread("C1234567", "1.0", limit=3)
    assert len(page.messages) == 3 and page.next_cursor == "MORE"


@pytest.mark.asyncio
async def test_issue_2103_search_appends_in_filters_and_maps_channel():
    seen = {}

    def handler(request):
        seen.update(_form(request))
        return httpx.Response(200, json={"ok": True, "messages": {"matches": [
            {"ts": "9", "text": "hit", "user": "U2", "channel": {"id": "C7777777"}, "permalink": "http://p"},
        ]}})

    res = await make_adapter(handler).search("deploy", in_channels=["C1111111", "C2222222"], limit=5)
    assert seen["query"] == "deploy in:<#C1111111> in:<#C2222222>"
    assert res[0].channel == "C7777777" and res[0].permalink == "http://p"


@pytest.mark.asyncio
async def test_issue_2103_list_conversations_and_info_and_open_dm():
    def handler(request):
        method = str(request.url).rsplit("/", 1)[1]
        if method == "users.conversations":
            return httpx.Response(200, json={"ok": True, "channels": [
                {"id": "D1234567", "is_im": True, "user": "U9"}, {"id": "C1234567", "name": "general"}]})
        if method == "conversations.info":
            return httpx.Response(200, json={"ok": True, "channel": {"id": "D1234567", "is_im": True,
                                                                      "unread_count_display": 4}})
        if method == "conversations.open":
            assert json.loads(request.content) == {"users": "U9"}
            return httpx.Response(200, json={"ok": True, "channel": {"id": "D1234567"}})
        raise AssertionError(method)

    adapter = make_adapter(handler)
    convs = await adapter.list_conversations()
    assert [c.id for c in convs] == ["D1234567", "C1234567"] and convs[0].is_im
    assert (await adapter.conversation_info("D1234567")).unread_count == 4
    assert await adapter.open_dm("U9") == "D1234567"


@pytest.mark.asyncio
async def test_issue_2103_react_and_mark_read_payloads():
    seen = {}

    def handler(request):
        seen[str(request.url).rsplit("/", 1)[1]] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    adapter = make_adapter(handler)
    await adapter.react("C1234567", "1.5", "eyes")
    await adapter.mark_read("C1234567", "1.5")
    assert seen["reactions.add"] == {"channel": "C1234567", "timestamp": "1.5", "name": "eyes"}
    assert seen["conversations.mark"] == {"channel": "C1234567", "ts": "1.5"}


@pytest.mark.asyncio
async def test_issue_2103_upload_two_step_without_token_on_upload_host(tmp_path):
    f = tmp_path / "note.txt"
    f.write_bytes(b"hello")
    seen = {}

    def handler(request):
        url = str(request.url)
        if url.endswith("files.getUploadURLExternal"):
            seen["slot"] = _form(request)
            return httpx.Response(200, json={"ok": True, "upload_url": "https://files.slack.test/up", "file_id": "F1"})
        if url == "https://files.slack.test/up":
            seen["upload_auth"] = request.headers.get("Authorization")
            seen["upload_body"] = request.content
            return httpx.Response(200)
        if url.endswith("files.completeUploadExternal"):
            seen["complete"] = _form(request)
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(url)

    res = await make_adapter(handler).upload("C1234567", f, thread_ts="1.0", comment="[test] hi")
    assert res.file_id == "F1"
    assert seen["slot"] == {"filename": "note.txt", "length": "5"}
    assert seen["upload_auth"] is None and seen["upload_body"] == b"hello"
    assert json.loads(seen["complete"]["files"]) == [{"id": "F1", "title": "note.txt"}]
    assert seen["complete"]["channel_id"] == "C1234567" and seen["complete"]["initial_comment"] == "[test] hi"


@pytest.mark.asyncio
async def test_issue_2103_start_events_reserved():
    adapter = make_adapter(lambda r: httpx.Response(200, json={"ok": True}))
    with pytest.raises(NotImplementedError):
        await adapter.start_events(lambda e: None)


async def test_issue_2103_search_text_falls_back_to_blocks():
    """search.messages returns text "" for block messages: use section/rich_text, skip footer."""
    match = {
        "channel": {"id": "C0AUTO0001"}, "ts": "1.1", "text": "",
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "body one"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "body two"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "footer"}]},
        ],
    }
    rich = {
        "channel": {"id": "C0AUTO0001"}, "ts": "1.2", "text": "",
        "blocks": [{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [
            {"type": "text", "text": "hi "}, {"type": "link", "url": "https://x.y"},
        ]}]}],
    }
    plain = {"channel": {"id": "C0AUTO0001"}, "ts": "1.3", "text": "kept"}

    def handler(request):
        return httpx.Response(200, json={"ok": True, "messages": {"matches": [match, rich, plain]}})

    results = await make_adapter(handler).search("q", ["C0AUTO0001"])
    assert [r.text for r in results] == ["body one\nbody two", "hi https://x.y", "kept"]
