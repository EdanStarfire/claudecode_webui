"""Scope monitors: the monitor_scope consent prompt (issue #2103 stage 3b-ii)."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.models.permission_mode import PermissionMode
from backend.relay.inbound import constants as const
from backend.relay.inbound.store import JsonSubscriptionStore
from backend.tests.relay_prompt_support import SID, Harness
from backend.tests.relay_support import result_text

ALLOW = {"behavior": "allow"}
MENTIONS, DMS = "mcp__relay_slack__monitor_mentions", "mcp__relay_slack__monitor_dms"


class FakeService:
    def __init__(self, tmp_path) -> None:
        self.store = JsonSubscriptionStore(tmp_path / "mon", "relay1")
        self.problem = None
        self.audit_args: list[dict] = []

    def monitor_eligibility(self):
        return self.problem

    async def create_monitor(self, session_id, kind, channel, *, thread_ts, filters, expires_at, **kw):
        self.audit_args.append(kw)
        return await self.store.create_monitor(
            session_id, kind, channel, thread_ts=thread_ts, filters=filters, expires_at=expires_at,
            consent=kw.get("consent"))


@pytest.fixture
def h(tmp_path):
    h = Harness(tmp_path)
    h.svc = FakeService(tmp_path)
    h.tools.inbound_lookup = lambda relay_id: h.svc if relay_id == "relay1" else None
    return h


def call(h, tool="monitor_mentions", **args):
    return h.tools.monitors.handle_scope_monitor("s1", tool, args)


def code_of(result) -> str:
    assert result["is_error"] is True
    return json.loads(result["content"][1]["text"])["code"]


def body(result) -> dict:
    assert not result.get("is_error"), result_text(result)
    return json.loads(result["content"][0]["text"])


class TestPromptPayload:
    async def test_mentions_and_dms_wording(self, h):
        res = await h.gate.evaluate(SID, MENTIONS, {"keywords": ["deploy"]})
        assert res.kind == "prompt" and res.action == "monitor_scope"
        p = res.prompt
        assert p["kind"] == "monitor_scope" and p["scope"] == "mentions"
        assert p["text"].startswith("Let this session read every Slack message that mentions you")
        assert "untrusted content" in p["text"] and "blocked stay excluded" in p["text"]
        assert p["filters_summary"] == "keywords: deploy"
        res = await h.gate.evaluate(SID, DMS, {})
        assert res.prompt["scope"] == "dms"
        assert "direct and group messages other people send you" in res.prompt["text"]
        assert "self-DM are excluded" in res.prompt["text"]

    async def test_no_channel_involved_and_no_policy_options(self, h):
        res = await h.gate.evaluate(SID, MENTIONS, {})
        assert res.channel_id is None and res.policy is None


class TestHandlerFlow:
    async def test_prompts_once_and_creates_with_consent(self, h):
        task = asyncio.ensure_future(call(h, keywords=["deploy"]))
        [rid] = await h.wait_pending()
        assert h.answer(rid, ALLOW)
        out = body(await asyncio.wait_for(task, 3))
        assert out["outcome"] == "created" and out["kind"] == const.KIND_MONITOR_MENTIONS
        rec = h.svc.store.get_monitor(out["monitor_id"])
        assert rec["channel"] is None and rec["consent"]["via"] == "prompt"
        assert h.svc.audit_args[0]["approval"] == "prompt_allow"
        assert h.svc.audit_args[0]["prompt_via"] == "handler"
        assert [r for r in h.audit_rows() if r["event"] == "policy_change"] == []  # no policy written

    async def test_identical_monitor_returns_without_prompt(self, h):
        task = asyncio.ensure_future(call(h, "monitor_dms"))
        [rid] = await h.wait_pending()
        h.answer(rid, ALLOW)
        first = body(await asyncio.wait_for(task, 3))
        again = body(await asyncio.wait_for(call(h, "monitor_dms"), 3))
        assert again["outcome"] == "already_monitored" and again["monitor_id"] == first["monitor_id"]
        assert h.service.pending_permissions == {}

    async def test_deny_creates_nothing(self, h):
        task = asyncio.ensure_future(call(h))
        [rid] = await h.wait_pending()
        h.answer(rid, {"behavior": "deny", "reason": "user_denied"})
        assert code_of(await asyncio.wait_for(task, 3)) == "approval_denied"
        assert h.svc.store.monitors() == []

    async def test_bypass_still_prompts(self, h):
        h.env.info.current_permission_mode = PermissionMode.BYPASS_PERMISSIONS
        task = asyncio.ensure_future(call(h))
        [rid] = await h.wait_pending()
        h.answer(rid, ALLOW)
        assert body(await asyncio.wait_for(task, 3))["outcome"] == "created"

    async def test_dont_ask_refuses_without_prompt(self, h):
        h.env.info.current_permission_mode = PermissionMode.DONT_ASK
        res = await asyncio.wait_for(call(h), 3)
        assert code_of(res) == "monitor_consent_unavailable_dont_ask"
        assert "don't-ask" in res["content"][0]["text"]
        assert h.service.pending_permissions == {} and h.svc.store.monitors() == []
        row = h.audit_rows()[-1]
        assert (row["outcome"], row["code"]) == ("denied", "monitor_consent_unavailable_dont_ask")

    async def test_unattended_refuses(self, h):
        h.env.info.is_ephemeral = True
        res = await asyncio.wait_for(call(h, "monitor_dms"), 3)
        assert code_of(res) == "monitor_consent_unavailable"
        assert h.svc.store.monitors() == []

    async def test_callback_grant_is_honoured_without_second_prompt(self, h):
        args = {"keywords": ["x"]}
        pre = await h.env.bridge.pre_hook(SID, MENTIONS, args)
        assert pre.kind == "prompt" and pre.prompt["kind"] == "monitor_scope"
        resp = await h.env.bridge.apply_response(
            SID, MENTIONS, args, pre, {"behavior": "allow", "relay_choice": {"policy": "blocked", "promote": True}})
        assert resp["relay_approval"] == "prompt_allow"  # a hand-made policy choice is ignored
        out = body(await asyncio.wait_for(call(h, **args), 3))
        assert out["outcome"] == "created" and h.service.pending_permissions == {}
        assert h.svc.audit_args[0]["prompt_via"] == "callback"

    async def test_inbound_unavailable_refused_before_prompt(self, h):
        h.svc.problem = "no_app_token"
        assert code_of(await asyncio.wait_for(call(h), 3)) == "inbound_unavailable"
        assert h.service.pending_permissions == {}

    async def test_invalid_filter_and_cap(self, h):
        assert code_of(await call(h, keywords="deploy")) == "invalid_filter"
        for i in range(const.MAX_MONITORS_PER_SESSION):
            await h.svc.store.create_monitor("s1", const.KIND_MONITOR_CHANNEL, f"C0CHAN{i:04d}",
                                             filters={"keywords": ["a"], "users": []})
        assert code_of(await call(h)) == "monitor_limit"
        assert h.service.pending_permissions == {}
