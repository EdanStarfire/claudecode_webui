"""Issue #2355 fix 2: a relay refusal carries the same detail in every permission mode.

default / acceptEdits refuse in the permission pre-hook (deny message string), auto in the
handler (content blocks). The permission callback never consults the mode, so one callback
run stands for both.
"""

from __future__ import annotations

import json

import pytest

from backend.tests.relay_prompt_support import SID, Harness
from backend.tests.relay_support import CH_BLOCKED, CH_RO, result_text


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


async def _callback_deny(h, args):
    ctx = type("C", (), {"tool_use_id": "tu1", "agent_id": None, "suggestions": []})()
    return await h.service.create_permission_callback(SID)("mcp__relay_slack__post", args, ctx)


@pytest.mark.parametrize("channel", [CH_RO, CH_BLOCKED])
async def test_issue_2355_prehook_deny_equals_handler_refusal(h, channel):
    args = {"channel": channel, "text": "hello"}
    denied = await _callback_deny(h, args)
    handled = await h.tools.handle_post("s1", "post", args)
    assert denied["behavior"] == "deny"
    assert denied["message"] == result_text(handled)
    human, structured = denied["message"].split("\n", 1)
    detail = json.loads(structured)
    assert detail == {
        "error": "relay_policy",
        "code": "channel_blocked" if channel == CH_BLOCKED else "send_not_permitted",
        "channel": channel, "action": "send",
        "policy": "blocked" if channel == CH_BLOCKED else "read_only",
    }
    if channel == CH_BLOCKED:  # reveals nothing beyond "not accessible", no name or label
        assert human == f"Channel {channel} is not accessible to this session."
        assert "blocked-ch" not in denied["message"]
    assert h.adapter.network_calls() == []


async def test_issue_2355_prehook_refusal_audit_rows_unchanged(h):
    await _callback_deny(h, {"channel": CH_RO, "text": "hello"})
    (row,) = [r for r in h.audit_rows() if r["event"] == "tool"]
    assert (row["outcome"], row["code"], row["approval"], row["policy"]) == (
        "denied", "send_not_permitted", "auto", "read_only"
    )
    assert row["prompt_via"] is None and row["tool"] == "post"
