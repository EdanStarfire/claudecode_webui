"""monitor_unread through the tool, the gate and the consent prompt (issue #2103 stage 3c-ii)."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.models.permission_mode import PermissionMode
from backend.relay.adapter import ConversationInfo
from backend.relay.inbound import constants as const
from backend.relay.inbound.manager import InboundManager
from backend.tests.relay_prompt_support import SID, Harness
from backend.tests.relay_support import (
    CH_ASK,
    CH_AUTO,
    CH_BLOCKED,
    CH_RO,
    CH_UNLISTED,
    SELF_DM,
    result_text,
)
from backend.tests.test_relay_monitor_tools import FakeMonitorService

ALLOW = {"behavior": "allow"}
UNREAD = "mcp__relay_slack__monitor_unread"
DAILY = {"daily": {"times": ["09:00"]}}
EVERY = {"every_minutes": 120}


class Svc(FakeMonitorService):
    async def create_monitor(self, session_id, kind, channel, *, thread_ts, filters, expires_at,
                             schedule=None, options=None, **kw):
        self.audit_args = getattr(self, "audit_args", []) + [kw]
        rec, outcome = await self.store.create_monitor(
            session_id, kind, channel, thread_ts=thread_ts, filters=filters, expires_at=expires_at,
            schedule=schedule, options=options, consent=kw.get("consent"))
        return rec, outcome


@pytest.fixture
def h(tmp_path):
    h = Harness(tmp_path)
    h.svc = Svc(tmp_path)
    h.tools.inbound_lookup = lambda relay_id: h.svc if relay_id == "relay1" else None
    h.member = True

    async def info(ch):
        return ConversationInfo(id=ch, name="x", is_channel=True, is_member=h.member)

    h.adapter.conversation_info = info
    return h


def call(h, **args):
    return h.tools.monitors.handle_unread_monitor("s1", "monitor_unread", args)


def code_of(result) -> str:
    assert result["is_error"] is True
    return json.loads(result["content"][1]["text"])["code"]


def body(result) -> dict:
    assert not result.get("is_error"), result_text(result)
    return json.loads(result["content"][0]["text"])


async def create_with_consent(h, **args):
    task = asyncio.ensure_future(call(h, **args))
    [rid] = await h.wait_pending()
    assert h.answer(rid, ALLOW)
    return body(await asyncio.wait_for(task, 3))


class TestArguments:
    async def test_schedule_is_required(self, h):
        assert code_of(await call(h)) == "schedule_required"

    @pytest.mark.parametrize("schedule", [{"every_minutes": 30}, {"every_minutes": 59}, {"daily": {"times": ["x"]}}])
    async def test_invalid_schedule_and_sixty_minute_minimum(self, h, schedule):
        assert code_of(await call(h, schedule=schedule)) == "invalid_schedule"

    async def test_sixty_minutes_is_accepted(self, h):
        assert (await create_with_consent(h, schedule={"every_minutes": 60}))["schedule"] == "every 1 hour"

    async def test_nothing_to_watch(self, h):
        assert code_of(await call(h, schedule=EVERY, dms=False)) == "filter_required"
        assert h.service.pending_permissions == {}

    @pytest.mark.parametrize("bad", [{"dms": "no"}, {"channels": "C0AUTO0001"}, {"exclude": ["#x"]}])
    async def test_invalid_options(self, h, bad):
        assert code_of(await call(h, schedule=EVERY, **bad)) == "invalid_filter"

    async def test_inbound_unavailable_before_any_prompt(self, h):
        h.svc.digest_problem = "disabled_by_owner"
        assert code_of(await call(h, schedule=EVERY)) == "inbound_unavailable"
        assert h.service.pending_permissions == {}


class TestConsent:
    async def test_prompt_payload(self, h):
        res = await h.gate.evaluate(SID, UNREAD, {"schedule": DAILY})
        assert res.kind == "prompt" and res.prompt["scope"] == "unread" and res.prompt["kind"] == "monitor_scope"
        text = res.prompt["text"]
        assert text.startswith("Let this session see which of your direct and group conversations have unread messages")
        assert "never marks anything as read, never posts and never reacts" in text
        assert "Blocked conversations are excluded" in text and "untrusted content" in text
        assert "digest at daily at 09:00" in text and res.prompt["schedule"]

    async def test_prompt_then_create_with_consent_and_result_wording(self, h):
        out = await create_with_consent(h, schedule=DAILY)
        assert out["outcome"] == "created" and out["kind"] == const.KIND_MONITOR_UNREAD
        assert out["schedule"].startswith("daily at 09:00") and out["timezone_source"] == "slack"
        assert out["next_run"].endswith("Z") and out["next_run_local"]
        assert out["options_text"] == "direct and group messages, only newly unread"
        assert "never marks anything read" in out["note"] and "mark_read" in out["note"]
        rec = h.svc.store.get_monitor(out["monitor_id"])
        assert rec["consent"]["via"] == "prompt" and rec["options"]["dms"] is True and rec["mode"] == "digest"
        assert h.svc.audit_args[0]["approval"] == "prompt_allow" and h.svc.audit_args[0]["prompt_via"] == "handler"

    async def test_identical_monitor_needs_no_second_prompt(self, h):
        first = await create_with_consent(h, schedule=EVERY, exclude=["D0AAAAAAAA"])
        again = body(await asyncio.wait_for(call(h, schedule=EVERY, exclude=["D0AAAAAAAA"]), 3))
        assert again["outcome"] == "already_monitored" and again["monitor_id"] == first["monitor_id"]
        assert h.service.pending_permissions == {}
        other = asyncio.ensure_future(call(h, schedule=EVERY, repeat=True))  # different options: asks again
        [rid] = await h.wait_pending()
        h.answer(rid, ALLOW)
        assert body(await asyncio.wait_for(other, 3))["outcome"] == "created"

    async def test_pre_hook_shortcut_for_the_identical_monitor_and_grant_consumed(self, h):
        await create_with_consent(h, schedule=EVERY)
        mgr = InboundManager.__new__(InboundManager)
        mgr.services = {"relay1": h.svc}
        h.gate.scope_existing = mgr.scope_monitor_exists
        args = {"schedule": EVERY}
        assert (await h.env.bridge.pre_hook(SID, UNREAD, args)).kind == "allow"
        assert (await h.env.bridge.pre_hook(SID, UNREAD, {"schedule": EVERY, "repeat": True})).kind == "prompt"
        assert (await h.env.bridge.pre_hook(SID, UNREAD, {"schedule": EVERY, "channels": ["#named"]})).kind == "prompt"
        h.gate.grants.add(SID, "monitor_unread", args, "prompt_allow")
        assert body(await call(h, **args))["outcome"] == "already_monitored"
        assert h.gate.grants.consume(SID, "monitor_unread", args) is None

    async def test_callback_grant_is_honoured(self, h):
        args = {"schedule": EVERY}
        pre = await h.env.bridge.pre_hook(SID, UNREAD, args)
        assert pre.kind == "prompt"
        await h.env.bridge.apply_response(SID, UNREAD, args, pre, ALLOW)
        out = body(await asyncio.wait_for(call(h, **args), 3))
        assert out["outcome"] == "created" and h.service.pending_permissions == {}
        assert h.svc.audit_args[0]["prompt_via"] == "callback"

    async def test_bypass_still_prompts(self, h):
        h.env.info.current_permission_mode = PermissionMode.BYPASS_PERMISSIONS
        assert (await create_with_consent(h, schedule=EVERY))["outcome"] == "created"

    async def test_dont_ask_and_unattended_refuse(self, h):
        h.env.info.current_permission_mode = PermissionMode.DONT_ASK
        assert code_of(await asyncio.wait_for(call(h, schedule=EVERY), 3)) == "monitor_consent_unavailable_dont_ask"
        h.env.info.current_permission_mode = PermissionMode.DEFAULT
        h.env.info.is_ephemeral = True
        assert code_of(await asyncio.wait_for(call(h, schedule=EVERY), 3)) == "monitor_consent_unavailable"
        assert h.svc.store.monitors() == [] and h.service.pending_permissions == {}

    async def test_deny_creates_nothing(self, h):
        task = asyncio.ensure_future(call(h, schedule=EVERY))
        [rid] = await h.wait_pending()
        h.answer(rid, {"behavior": "deny", "reason": "user_denied"})
        assert code_of(await asyncio.wait_for(task, 3)) == "approval_denied"
        assert h.svc.store.monitors() == []


class TestExplicitChannels:
    async def test_readable_channels_need_no_prompt_when_dms_is_off(self, h):
        for ch in (CH_RO, CH_ASK, CH_AUTO):
            res = await h.gate.evaluate(SID, UNREAD, {"schedule": EVERY, "dms": False, "channels": [ch]})
            assert res.kind == "allow"
        out = body(await asyncio.wait_for(call(h, schedule=EVERY, dms=False, channels=[CH_RO, CH_ASK, CH_AUTO]), 3))
        assert out["outcome"] == "created" and out["options"]["channels"] == sorted([CH_RO, CH_ASK, CH_AUTO])
        assert h.service.pending_permissions == {}
        assert "consent" not in h.svc.store.get_monitor(out["monitor_id"]) or h.svc.store.get_monitor(out["monitor_id"])["consent"] is None

    async def test_unlisted_refused_with_all_ids_and_no_prompt(self, h):
        extra = "C0UNLIST002"
        res = await asyncio.wait_for(call(h, schedule=EVERY, dms=False, channels=[CH_AUTO, CH_UNLISTED, extra]), 3)
        assert code_of(res) == "channel_policy_required"
        text = res["content"][0]["text"]
        assert CH_UNLISTED in text and extra in text and CH_AUTO not in text and "nothing was created" in text
        assert h.service.pending_permissions == {} and h.svc.store.monitors() == []

    async def test_blocked_refused_by_id(self, h):
        res = await asyncio.wait_for(call(h, schedule=EVERY, dms=False, channels=[CH_BLOCKED, CH_AUTO]), 3)
        assert code_of(res) == "channel_blocked" and CH_BLOCKED in res["content"][0]["text"]
        assert h.svc.store.monitors() == []

    async def test_self_dm_refused(self, h):
        res = await asyncio.wait_for(call(h, schedule=EVERY, dms=False, channels=[SELF_DM]), 3)
        assert code_of(res) == "self_dm_not_monitorable"

    async def test_not_a_member_refused(self, h):
        h.member = False
        res = await asyncio.wait_for(call(h, schedule=EVERY, dms=False, channels=[CH_AUTO]), 3)
        assert code_of(res) == "not_a_member" and h.svc.store.monitors() == []

    async def test_name_is_resolved_and_unknown_name_refused(self, h):
        out = body(await asyncio.wait_for(call(h, schedule=EVERY, dms=False, channels=["#auto-ch"]), 3))
        assert out["options"]["channels"] == [CH_AUTO]
        res = await asyncio.wait_for(call(h, schedule=EVERY, dms=False, channels=["#nope-nope"]), 3)
        assert code_of(res) in ("name_not_found", "channel_unresolved")

    async def test_channels_plus_dms_checks_policy_before_the_handler_prompt(self, h):
        res = await asyncio.wait_for(call(h, schedule=EVERY, channels=[CH_UNLISTED]), 3)
        assert code_of(res) == "channel_policy_required" and h.service.pending_permissions == {}


class TestListAndCaps:
    async def test_list_monitors_shows_options_and_schedule(self, h):
        await create_with_consent(h, schedule=EVERY, repeat=True)
        res = await h.tools.monitors.handle_list("s1", {})
        row = json.loads(res["content"][0]["text"])["monitors"][0]
        assert row["kind"] == const.KIND_MONITOR_UNREAD and row["options"]["repeat"] is True
        assert row["options_text"].endswith("every unread conversation at each run")
        assert row["schedule"] == "every 2 hours" and row["next_run"]

    async def test_session_cap_refused_before_prompt(self, h):
        for i in range(const.MAX_MONITORS_PER_SESSION):
            await h.svc.store.create_monitor("s1", const.KIND_MONITOR_CHANNEL, f"C0CHAN{i:04d}",
                                             filters={"keywords": ["a"], "users": []})
        assert code_of(await call(h, schedule=EVERY)) == "monitor_limit"
        assert h.service.pending_permissions == {}


class TestRegistration:
    async def test_tool_is_registered_with_the_plain_wording(self, h):
        from backend.relay.adapters import SLACK_SPEC
        assert "monitor_unread" in SLACK_SPEC.monitor_tools and "monitor_unread" in SLACK_SPEC.scope_tools
        assert SLACK_SPEC.tool_actions["monitor_unread"] == "monitor_scope"
        assert "monitor_unread" not in SLACK_SPEC.write_tools
        server = h.tools.create_mcp_server_for_session("s1")
        entry = server["instance"].get_request_handler("tools/list")
        tools = {t.name: t for t in (await entry.handler(None, None)).tools}
        desc = tools["monitor_unread"].description
        for needle in ("unread now", "NEW unread", "NEVER marks anything read", "mark_read", "UNTRUSTED",
                       "restarted", "approximate", "ALREADY be readable"):
            assert needle in desc
        assert tools["monitor_unread"].input_schema["required"] == ["schedule"]
