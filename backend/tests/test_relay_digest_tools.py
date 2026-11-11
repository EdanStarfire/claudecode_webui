"""Digest creation through the monitor tools (issue #2103 stage 3c-i): schedule parameter, timezone
statements, eligibility without Socket Mode, consent wording, caps, run_monitor_now."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.relay.adapter import ConversationInfo
from backend.relay.inbound import constants as const
from backend.relay.inbound import schedule as sched
from backend.tests.relay_prompt_support import SID, Harness
from backend.tests.relay_support import CH_AUTO, CH_BLOCKED, CH_RO, CH_UNLISTED, result_text
from backend.tests.test_relay_monitor_tools import FakeMonitorService

ALLOW = {"behavior": "allow"}
MENTIONS = "mcp__relay_slack__monitor_mentions"
DAILY = {"daily": {"times": ["09:00", "17:00"], "days": ["mon", "tue", "wed", "thu", "fri"]}}


@pytest.fixture
def h(tmp_path):
    h = Harness(tmp_path)
    h.svc = FakeMonitorService(tmp_path)
    h.tools.inbound_lookup = lambda relay_id: h.svc if relay_id == "relay1" else None

    async def info(ch):
        return ConversationInfo(id=ch, name="x", is_channel=True, is_member=True)

    h.adapter.conversation_info = info
    return h


def channel(h, ch=CH_AUTO, **kw):
    kw.setdefault("keywords", ["deploy"])
    return h.tools.monitors.handle_monitor("s1", "monitor_channel", {"channel": ch, **kw})


def scope(h, tool="monitor_mentions", **kw):
    return h.tools.monitors.handle_scope_monitor("s1", tool, kw)


def code_of(result) -> str:
    assert result["is_error"] is True
    return json.loads(result["content"][1]["text"])["code"]


def body(result) -> dict:
    assert not result.get("is_error"), result_text(result)
    return json.loads(result["content"][0]["text"])


class TestDigestCreation:
    async def test_result_states_schedule_timezone_and_next_run(self, h):
        res = body(await channel(h, schedule=DAILY))
        assert res["mode"] == "digest"
        assert res["schedule"] == "daily at 09:00 and 17:00, Mon–Fri, Europe/Paris (your Slack timezone)"
        assert (res["timezone"], res["timezone_source"]) == ("Europe/Paris", "slack")
        assert res["next_run"].endswith("Z") and "+0" in res["next_run_local"]
        rec = h.svc.store.get_monitor(res["monitor_id"])
        assert rec["mode"] == "digest" and rec["next_run"] > rec["created_at"]
        assert "DIGEST" in res["note"] and "UNTRUSTED" in res["note"]

    async def test_explicit_timezone_and_interval(self, h):
        res = body(await channel(h, schedule={"daily": {"times": ["08:00"], "timezone": "Asia/Kolkata"}}))
        assert (res["timezone"], res["timezone_source"]) == ("Asia/Kolkata", "explicit")
        res = body(await channel(h, ch=CH_RO, schedule={"every_minutes": 120}))
        assert res["schedule"] == "every 2 hours" and "timezone" not in res

    async def test_utc_fallback_is_stated(self, h):
        h.svc.owner_tz = None
        res = body(await channel(h, schedule={"daily": {"times": ["09:00"]}}))
        assert (res["timezone"], res["timezone_source"]) == ("UTC", "utc_fallback")
        assert "Slack did not report your timezone, so UTC is used" in res["note"]

    @pytest.mark.parametrize("bad", [
        {"every_minutes": 10}, {"daily": {"times": ["25:00"]}}, "daily", {"daily": {"times": ["09:00"], "timezone": "Mars/X"}},
    ])
    async def test_invalid_schedule(self, h, bad):
        r = await channel(h, schedule=bad)
        assert code_of(r) == "invalid_schedule"
        assert h.svc.store.monitors() == []

    async def test_digest_needs_no_app_token_but_live_does(self, h):
        h.svc.problem = "no_app_token"
        assert body(await channel(h, schedule={"every_minutes": 60}))["mode"] == "digest"
        assert code_of(await channel(h)) == "inbound_unavailable"

    async def test_owner_switch_off_refuses_digest(self, h):
        h.svc.digest_problem = "disabled_by_owner"
        assert code_of(await channel(h, schedule={"every_minutes": 60})) == "inbound_unavailable"

    async def test_blocked_channel_refused_and_unlisted_prompt_names_the_digest(self, h):
        assert code_of(await channel(h, CH_BLOCKED, schedule={"every_minutes": 60})) == "channel_blocked"
        res = await h.gate.evaluate(
            SID, "mcp__relay_slack__monitor_channel",
            {"channel": CH_UNLISTED, "keywords": ["x"], "schedule": {"every_minutes": 60}})
        assert "delivered as a digest at every 1 hour" in res.prompt["text_preview"]
        task = asyncio.ensure_future(channel(h, CH_UNLISTED, schedule={"every_minutes": 60}))
        [rid] = await h.wait_pending()
        h.answer(rid, {"behavior": "deny", "reason": "user_denied"})
        await asyncio.wait_for(task, 3)
        assert h.svc.store.monitors() == []

    async def test_live_and_digest_coexist_with_repeat_note(self, h):
        live = body(await channel(h))
        assert "twice" not in live["note"]
        dig = body(await channel(h, schedule={"every_minutes": 60}))
        assert dig["monitor_id"] != live["monitor_id"] and h.svc.store.count() == 2
        assert "same messages twice" in dig["note"]
        again = body(await channel(h, schedule={"every_minutes": 60}))
        assert again["outcome"] == "existing" and again["monitor_id"] == dig["monitor_id"]
        other = body(await channel(h, schedule={"every_minutes": 90}))
        assert other["monitor_id"] not in (live["monitor_id"], dig["monitor_id"])
        # a live creation after a digest warns as well
        h2 = body(await channel(h, ch=CH_RO))
        await channel(h, ch=CH_RO, schedule={"every_minutes": 60})
        assert "twice" in body(await channel(h, ch=CH_RO))["note"]
        assert h2["mode"] == "live"

    async def test_scheduled_cap(self, h):
        for i in range(const.MAX_SCHEDULED_PER_RELAY):
            await h.svc.store.create_monitor(
                f"other{i}", const.KIND_MONITOR_CHANNEL, CH_AUTO, filters={"keywords": ["x"], "users": []},
                schedule=sched.parse_schedule({"every_minutes": 60}, owner_tz=None, now=1.0))
        r = await channel(h, schedule={"every_minutes": 60})
        assert code_of(r) == "monitor_limit"
        assert "Scheduled digest limit" in r["content"][0]["text"]
        assert body(await channel(h))["mode"] == "live"  # live monitors are not scheduled


class TestScopeDigests:
    async def test_consent_names_the_schedule(self, h):
        res = await h.gate.evaluate(SID, MENTIONS, {"schedule": DAILY})
        assert res.prompt["text"].endswith(
            "It will be delivered as a digest at daily at 09:00 and 17:00, Mon–Fri, "
            "your Slack timezone, only when there is content.")
        assert "digest" in res.prompt["schedule"] or "daily" in res.prompt["schedule"]
        live = await h.gate.evaluate(SID, MENTIONS, {})
        assert "digest" not in live.prompt["text"] and "schedule" not in live.prompt

    async def test_created_once_then_identical_returns_without_prompt(self, h):
        task = asyncio.ensure_future(scope(h, schedule=DAILY))
        [rid] = await h.wait_pending()
        h.answer(rid, ALLOW)
        first = body(await asyncio.wait_for(task, 3))
        assert first["mode"] == "digest" and first["outcome"] == "created"
        again = body(await asyncio.wait_for(scope(h, schedule=DAILY), 3))
        assert again["outcome"] == "already_monitored" and again["monitor_id"] == first["monitor_id"]
        assert h.service.pending_permissions == {}
        # a live mentions monitor is a separate monitor and prompts again
        task = asyncio.ensure_future(scope(h))
        [rid] = await h.wait_pending()
        h.answer(rid, ALLOW)
        live = body(await asyncio.wait_for(task, 3))
        assert live["monitor_id"] != first["monitor_id"] and "same messages twice" in live["note"]

    async def test_denied_creates_nothing_and_dont_ask_refuses(self, h):
        task = asyncio.ensure_future(scope(h, schedule=DAILY))
        [rid] = await h.wait_pending()
        h.answer(rid, {"behavior": "deny", "reason": "user_denied"})
        assert code_of(await asyncio.wait_for(task, 3)) == "approval_denied"
        assert h.svc.store.monitors() == []


class TestRunNowTool:
    async def test_success_does_not_wait(self, h):
        res = body(await h.tools.monitors.handle_run_now("s1", {"monitor_id": "m1"}))
        assert res["ok"] and res["started"]
        assert h.svc.run_now_calls == [("m1", "agent", "s1")]

    async def test_refusal_codes_pass_through(self, h):
        h.svc.run_now_result = {"ok": False, "code": "run_too_soon", "message": "wait 120 s"}
        assert code_of(await h.tools.monitors.handle_run_now("s1", {"monitor_id": "m1"})) == "run_too_soon"
        assert code_of(await h.tools.monitors.handle_run_now("s1", {"monitor_id": 5})) == "monitor_not_found"

    async def test_list_shows_digest_facts(self, h):
        body(await channel(h, schedule=DAILY))
        row = json.loads((await h.tools.monitors.handle_list("s1", {}))["content"][0]["text"])["monitors"][0]
        assert row["mode"] == "digest" and row["timezone"] == "Europe/Paris" and row["next_run"]
        assert row["last_run"] is None

    async def test_descriptions_state_the_repeat_warning_and_tool_is_registered(self, h):
        server = h.tools.create_mcp_server_for_session("s1")
        entry = server["instance"].get_request_handler("tools/list")
        tools = {t.name: t for t in (await entry.handler(None, None)).tools}
        assert "run_monitor_now" in tools
        for name in ("monitor_thread", "monitor_channel", "monitor_mentions", "monitor_dms"):
            desc = tools[name].description
            assert "A digest and a live monitor on the same scope repeat the same messages" in desc
            assert "No `schedule` = live" in desc
            assert "schedule" in tools[name].input_schema["properties"]
