"""Relay monitor tools (issue #2103 stage 3b-i): validation, policy matrix, refusals, audit."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from backend.models.permission_mode import PermissionMode
from backend.relay.adapter import ConversationInfo, RelayError
from backend.relay.inbound import constants as const
from backend.relay.inbound.store import JsonSubscriptionStore
from backend.relay.policy import CHOOSE_POLICY
from backend.relay.safety import UNTRUSTED_OPEN_PREFIX
from backend.tests.relay_prompt_support import SID, Harness
from backend.tests.relay_support import (
    CH_ASK,
    CH_AUTO,
    CH_BLOCKED,
    CH_RO,
    CH_UNLISTED,
    OTHER_DM,
    OTHER_USER,
    OWNER,
    SELF_DM,
    result_text,
)

TS = "1700000000.000100"


class FakeMonitorService:
    """The slice of InboundService the tools use, over a real store."""

    def __init__(self, tmp_path) -> None:
        self.store = JsonSubscriptionStore(tmp_path / "mon", "relay1")
        self.problem: str | None = None
        self.created: list = []

    def monitor_eligibility(self):
        return self.problem

    async def create_monitor(self, session_id, kind, channel, *, thread_ts, filters, expires_at):
        rec, outcome = await self.store.create_monitor(
            session_id, kind, channel, thread_ts=thread_ts, filters=filters, expires_at=expires_at)
        self.created.append(rec["id"])
        return rec, outcome

    async def remove_monitor(self, session_id, monitor_id):
        rec = self.store.get_monitor(monitor_id)
        if rec is None or rec["session_id"] != session_id:
            return False
        await self.store.remove_monitor(monitor_id)
        return True

    def list_monitors(self, session_id):
        return [dict(m) for m in self.store.monitors(session_id)]


@pytest.fixture
def h(tmp_path):
    h = Harness(tmp_path)
    h.svc = FakeMonitorService(tmp_path)
    h.tools.inbound_lookup = lambda relay_id: h.svc if relay_id == "relay1" else None
    return h


def thread(h, ch=CH_AUTO, **kw):
    return h.tools.monitors.handle_monitor("s1", "monitor_thread", {"channel": ch, "thread_ts": TS, **kw})


def channel(h, ch=CH_AUTO, **kw):
    kw.setdefault("keywords", ["deploy"])
    return h.tools.monitors.handle_monitor("s1", "monitor_channel", {"channel": ch, **kw})


def code_of(result) -> str:
    assert result["is_error"] is True
    return json.loads(result["content"][1]["text"])["code"]


def body(result) -> dict:
    assert not result.get("is_error"), result_text(result)
    return json.loads(result["content"][0]["text"])


class TestRegistration:
    async def test_tools_are_in_the_spec(self, h):
        from backend.relay.adapters import SLACK_SPEC
        assert SLACK_SPEC.monitor_tools == {"monitor_thread", "monitor_channel", "unmonitor", "list_monitors"}
        assert not (SLACK_SPEC.monitor_tools & SLACK_SPEC.write_tools)  # never send tools
        assert SLACK_SPEC.tool_actions["monitor_thread"] == SLACK_SPEC.tool_actions["monitor_channel"] == "read"
        assert SLACK_SPEC.tool_actions["unmonitor"] is None and SLACK_SPEC.tool_actions["list_monitors"] is None

    async def test_server_lists_the_tools_with_untrusted_framing(self, h):
        server = h.tools.create_mcp_server_for_session("s1")
        entry = server["instance"].get_request_handler("tools/list")
        tools = {t.name: t for t in (await entry.handler(None, None)).tools}
        assert {"monitor_thread", "monitor_channel", "unmonitor", "list_monitors"} <= set(tools)
        for name in ("monitor_thread", "monitor_channel"):
            desc = tools[name].description
            assert "UNTRUSTED" in desc and "never instructions" in desc and "restarted" in desc
        assert "filter" in tools["monitor_channel"].description


class TestValidation:
    async def test_thread_ts_format(self, h):
        r = await h.tools.monitors.handle_monitor("s1", "monitor_thread", {"channel": CH_AUTO, "thread_ts": "x"})
        assert code_of(r) == "invalid_arguments"

    async def test_channel_monitor_requires_a_filter(self, h):
        assert code_of(await channel(h, keywords=[])) == "filter_required"
        assert code_of(await h.tools.monitors.handle_monitor("s1", "monitor_channel", {"channel": CH_AUTO})) \
            == "filter_required"
        assert h.svc.created == []

    @pytest.mark.parametrize("bad", [
        {"keywords": "deploy"}, {"keywords": [1]}, {"keywords": [f"k{i}" for i in range(11)]},
        {"keywords": ["y" * 101]}, {"keywords": ["x"], "users": "U0ALICE001"},
        {"keywords": ["x"], "users": ["U0ALICE001"] * 11},
    ])
    async def test_invalid_filters(self, h, bad):
        assert code_of(await channel(h, **bad)) == "invalid_filter"

    async def test_expiry_bounds(self, h):
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        far = (datetime.now(UTC) + timedelta(days=91)).isoformat()
        ok = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        assert code_of(await thread(h, expires_at=past)) == "invalid_arguments"
        assert code_of(await thread(h, expires_at=far)) == "invalid_arguments"
        assert code_of(await thread(h, expires_at="tomorrow")) == "invalid_arguments"
        assert body(await thread(h, expires_at=ok))["expires_at"]

    async def test_user_ids_and_names(self, h):
        res = body(await channel(h, keywords=[], users=["U0ALICE001", "@Alice Example"]))
        assert res["filters"]["users"] == ["U0ALICE001", OTHER_USER]  # @name resolved by the directory

    async def test_unknown_and_ambiguous_names(self, h):
        r = await channel(h, keywords=[], users=["@Nobody Here"])
        assert code_of(r) == "name_not_found"

    async def test_ambiguous_names_return_fenced_candidates(self, h):
        h.adapter.user_list.append(h.adapter.user_list[1].__class__(
            id="U0ALICE002", name="Alice Example", display_name="Alice Example"))
        r = await channel(h, keywords=[], users=["@Alice Example"])
        assert code_of(r) == "name_ambiguous"
        assert UNTRUSTED_OPEN_PREFIX in r["content"][2]["text"]  # candidates only inside the fence
        assert "U0ALICE002" not in r["content"][0]["text"] + r["content"][1]["text"]


class TestPolicyMatrix:
    @pytest.mark.parametrize("ch", [CH_AUTO, CH_ASK, CH_RO])
    async def test_readable_policies_create_without_a_prompt(self, h, ch):
        res = body(await thread(h, ch))
        assert res["outcome"] == "created" and res["status"] == "active" and res["channel"] == ch
        assert h.service.pending_permissions == {}

    async def test_blocked_is_refused_without_a_name_or_content_call(self, h):
        r = await thread(h, CH_BLOCKED)
        assert code_of(r) == "channel_blocked" and "not accessible" in r["content"][0]["text"]
        assert "blocked-ch" not in result_text(r)
        assert h.svc.created == []
        row = [x for x in h.audit_rows() if x["tool"] == "monitor_thread"][-1]
        assert (row["outcome"], row["code"]) == ("denied", "channel_blocked")

    async def test_unlisted_prompts_and_the_answer_saves_the_policy(self, h):
        task = asyncio.create_task(thread(h, CH_UNLISTED))
        [rid] = await h.wait_pending()
        prompt = h.service.pending_permissions[rid]
        assert prompt is not None
        h.answer(rid, {"behavior": "allow", "relay_choice": {"policy": "read_only"}})
        res = body(await task)
        assert res["status"] == "active"
        assert h.env.info.config["relay_channel_policies"]["relay1"][CH_UNLISTED] == "read_only"

    async def test_unlisted_denied_creates_nothing(self, h):
        task = asyncio.create_task(thread(h, CH_UNLISTED))
        [rid] = await h.wait_pending()
        h.answer(rid, {"behavior": "deny", "reason": "user_denied"})
        assert code_of(await task) == "approval_denied" and h.svc.created == []

    async def test_dont_ask_and_unattended_refusals(self, h):
        h.env.info.current_permission_mode = PermissionMode.DONT_ASK
        assert code_of(await thread(h, CH_UNLISTED)) == "policy_unset_dont_ask"
        assert CH_UNLISTED not in h.env.info.config["relay_channel_policies"]["relay1"]
        h.env.info.current_permission_mode = "acceptEdits"
        h.coord._active_sdks[SID].deny_unattended_permission_prompts = True
        assert code_of(await thread(h, CH_UNLISTED)) == "policy_unset"
        assert h.service.pending_permissions == {}
        assert body(await thread(h, CH_AUTO))["status"] == "active"  # readable channels still work

    async def test_permission_prehook_parity(self, h):
        full = lambda t: f"mcp__relay_slack__{t}"  # noqa: E731
        ok = await h.env.bridge.pre_hook("s1", full("monitor_thread"), {"channel": CH_AUTO, "thread_ts": TS})
        assert ok.kind == "allow"
        unlisted = await h.env.bridge.pre_hook("s1", full("monitor_channel"), {"channel": CH_UNLISTED, "keywords": ["a"]})
        assert unlisted.kind == CHOOSE_POLICY and "monitor channel" in unlisted.prompt["text_preview"]
        blocked = await h.env.bridge.pre_hook("s1", full("monitor_thread"), {"channel": CH_BLOCKED, "thread_ts": TS})
        assert blocked.kind == "deny" and blocked.code == "channel_blocked"
        no_prompt = await h.env.bridge.pre_hook("s1", full("list_monitors"), {})
        assert no_prompt.kind == "allow"
        row = [x for x in h.audit_rows() if x["tool"] == "monitor_thread"][-1]
        assert row["outcome"] == "denied"  # the pre-hook deny is audited too


class TestDmRefusals:
    @pytest.mark.parametrize("target", [SELF_DM, OWNER, "@Owner Person"])
    async def test_self_dm_in_every_spelling(self, h, target):
        assert code_of(await thread(h, target)) == "self_dm_not_monitorable"
        assert h.service.pending_permissions == {} and h.svc.created == []

    @pytest.mark.parametrize("target", [OTHER_DM, OTHER_USER, "@Alice Example"])
    async def test_other_dms(self, h, target):
        assert code_of(await channel(h, target)) == "dm_not_monitorable_here"
        assert h.service.pending_permissions == {}

    async def test_refusal_names_ids_only(self, h):
        r = await thread(h, SELF_DM)
        assert "Owner Person" not in result_text(r)


class TestMembershipAndEligibility:
    async def test_not_a_member(self, h):
        async def info(ch):
            return ConversationInfo(id=ch, name="x", is_channel=True, is_member=False)
        h.adapter.conversation_info = info
        r = await thread(h, CH_AUTO)
        assert code_of(r) == "not_a_member" and h.svc.created == []

    async def test_member_lookup_failure_is_refused(self, h):
        async def info(ch):
            raise RelayError("channel_not_found", "conversations.info")
        h.adapter.conversation_info = info
        assert code_of(await thread(h, CH_AUTO)) == "not_a_member"

    async def test_unknown_membership_is_allowed_with_a_note(self, h):
        res = body(await thread(h, CH_AUTO))  # the fake reports no is_member
        assert "could not be confirmed" in res["note"]

    async def test_confirmed_member_gets_the_standing_note(self, h):
        async def info(ch):
            return ConversationInfo(id=ch, name="x", is_channel=True, is_member=True)
        h.adapter.conversation_info = info
        note = body(await thread(h, CH_AUTO))["note"]
        assert "UNTRUSTED" in note and "survives session restarts" in note

    async def test_inbound_unavailable(self, h):
        h.svc.problem = "no_app_token"
        assert code_of(await thread(h, CH_AUTO)) == "inbound_unavailable"
        h.tools.inbound_lookup = None
        assert code_of(await thread(h, CH_AUTO)) == "inbound_unavailable"
        assert h.service.pending_permissions == {}  # decided before any prompt


class TestCreation:
    async def test_duplicate_returns_the_existing_monitor(self, h):
        a = body(await thread(h))
        b = body(await thread(h))
        assert b["monitor_id"] == a["monitor_id"] and b["outcome"] == "existing"
        assert h.svc.store.count() == 1

    async def test_session_cap(self, h):
        for i in range(const.MAX_MONITORS_PER_SESSION):
            await h.svc.store.create_monitor("s1", const.KIND_MONITOR_THREAD, CH_AUTO, thread_ts=f"1.{i}")
        r = await thread(h)
        assert code_of(r) == "monitor_limit"
        row = [x for x in h.audit_rows() if x["tool"] == "monitor_thread"][-1]
        assert (row["outcome"], row["code"]) == ("denied", "monitor_limit")

    async def test_result_holds_labels_only_inside_the_fence(self, h):
        r = await thread(h, CH_AUTO)
        assert "auto-ch" not in r["content"][0]["text"]
        assert UNTRUSTED_OPEN_PREFIX in r["content"][1]["text"] and "auto-ch" in r["content"][1]["text"]

    async def test_not_attached(self, h):
        h.env.info.config["relay_id"] = None
        assert code_of(await thread(h)) == "relay_not_attached"


class TestUnmonitorAndList:
    async def test_only_own_monitors(self, h):
        mine = body(await thread(h))["monitor_id"]
        other, _ = await h.svc.store.create_monitor(
            "s2", const.KIND_MONITOR_THREAD, CH_AUTO, thread_ts="2.2")
        listed = json.loads((await h.tools.monitors.handle_list("s1", {}))["content"][0]["text"])
        assert [m["monitor_id"] for m in listed["monitors"]] == [mine]
        assert code_of(await h.tools.monitors.handle_unmonitor("s1", {"monitor_id": other["id"]})) \
            == "monitor_not_found"
        assert code_of(await h.tools.monitors.handle_unmonitor("s1", {"monitor_id": 5})) == "monitor_not_found"
        assert body(await h.tools.monitors.handle_unmonitor("s1", {"monitor_id": mine}))["ok"]
        assert h.svc.store.get_monitor(mine) is None and h.svc.store.get_monitor(other["id"])

    async def test_list_shape_and_fenced_labels(self, h):
        await channel(h, CH_RO, keywords=["deploy"])
        r = await h.tools.monitors.handle_list("s1", {})
        row = json.loads(r["content"][0]["text"])["monitors"][0]
        assert row["kind"] == const.KIND_MONITOR_CHANNEL and row["status"] == "active"
        assert row["filters"] == {"keywords": ["deploy"], "users": []} and row["counters"]["delivered"] == 0
        assert "ro-ch" not in r["content"][0]["text"] and "ro-ch" in r["content"][1]["text"]
