"""Issue #2360: don't-ask mode fails closed for `ask` and unspecified channels (bypass keeps prompting)."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.models.permission_mode import PermissionMode
from backend.tests.relay_prompt_support import SID, Harness
from backend.tests.relay_support import CH_ASK, CH_AUTO, CH_BLOCKED, CH_RO, CH_UNLISTED

ALLOW = {"behavior": "allow"}
TS = "1700000000.000100"
DONT_ASK = PermissionMode.DONT_ASK


def parts(result):
    assert result["is_error"] is True
    return result["content"][0]["text"], json.loads(result["content"][1]["text"])


def code_of(result) -> str:
    return parts(result)[1]["code"]


@pytest.fixture
def h(tmp_path):
    h = Harness(tmp_path)
    (h.env.workdir / "a.txt").write_text("data")
    return h


def set_mode(h, mode) -> None:
    h.env.info.current_permission_mode = mode


CALLS = {
    "post": lambda h, ch: h.tools.handle_post("s1", "post", {"channel": ch, "text": "hello"}),
    "reply": lambda h, ch: h.tools.handle_post(
        "s1", "reply", {"channel": ch, "thread_ts": TS, "text": "hello"}
    ),
    "upload_file": lambda h, ch: h.tools.handle_upload(
        "s1", {"channel": ch, "path": str(h.env.workdir / "a.txt")}
    ),
    "react": lambda h, ch: h.tools.handle_react("s1", {"channel": ch, "ts": TS, "name": "thumbsup"}),
    "read_thread": lambda h, ch: h.tools.handle_read_thread("s1", {"channel": ch, "thread_ts": TS}),
    "mark_read": lambda h, ch: h.tools.handle_mark_read("s1", {"channel": ch, "ts": TS}),
}
SENDS = ("post", "reply", "upload_file", "react")


def test_dont_ask_mode_string_is_pinned():
    # the CLI's wire value; an upstream rename must fail here, not silently fail open
    assert PermissionMode.DONT_ASK == "dontAsk"


@pytest.mark.parametrize("tool", SENDS)
async def test_ask_channel_refused_without_prompt(h, tool):  # AC1, AC3, AC4
    set_mode(h, DONT_ASK)
    result = await asyncio.wait_for(CALLS[tool](h, CH_ASK), 3)
    text, detail = parts(result)
    assert detail["code"] == "approval_unavailable_dont_ask"
    assert "don't-ask" in text and "`auto`" in text and "permission mode" in text
    assert detail["error"] == "relay_policy" and detail["channel"] == CH_ASK
    assert h.service.pending_permissions == {}
    assert h.adapter.network_calls() == []
    row = h.audit_rows()[-1]
    assert (row["outcome"], row["code"]) == ("denied", "approval_unavailable_dont_ask")


@pytest.mark.parametrize("tool", list(CALLS))
async def test_unlisted_channel_refused_no_policy_saved(h, tool):  # AC2, AC6 interpretation
    set_mode(h, DONT_ASK)
    result = await asyncio.wait_for(CALLS[tool](h, CH_UNLISTED), 3)
    text, detail = parts(result)
    assert detail["code"] == "policy_unset_dont_ask"
    assert "don't-ask" in text and "`auto`" in text
    assert h.service.pending_permissions == {}
    assert h.adapter.network_calls() == []
    assert CH_UNLISTED not in h.env.info.config["relay_channel_policies"]["relay1"]
    assert [r for r in h.audit_rows() if r["event"] == "policy_change"] == []
    if tool != "read_thread":  # reads are not audited (write tools only)
        row = h.audit_rows()[-1]
        assert (row["outcome"], row["code"]) == ("denied", "policy_unset_dont_ask")


async def test_other_policies_and_reads_unchanged_in_dont_ask(h):  # AC6
    set_mode(h, DONT_ASK)
    for tool in SENDS:
        assert not (await CALLS[tool](h, CH_AUTO)).get("is_error"), tool
        assert code_of(await CALLS[tool](h, CH_BLOCKED)) == "channel_blocked"
        assert code_of(await CALLS[tool](h, CH_RO)) == "send_not_permitted"
    for tool in ("read_thread", "mark_read"):
        assert not (await CALLS[tool](h, CH_ASK)).get("is_error"), tool
        assert not (await CALLS[tool](h, CH_RO)).get("is_error"), tool
    for name, call in (
        ("search", h.tools.handle_search("s1", {"query": "x"})),
        ("get_identity", h.tools.handle_get_identity("s1", {})),
        ("find_channels", h.tools.handle_find_channels("s1", {})),
    ):
        assert not (await call).get("is_error"), name
    assert h.service.pending_permissions == {}


@pytest.mark.parametrize(
    "mode", ["default", "manual", "acceptEdits", "plan", "auto", "bypassPermissions"]
)
@pytest.mark.parametrize("channel", [CH_ASK, CH_UNLISTED])
async def test_other_modes_still_prompt(h, mode, channel):  # AC7, UC4 (explicit bypass)
    set_mode(h, mode)
    task = asyncio.create_task(CALLS["post"](h, channel))
    [rid] = await h.wait_pending()
    h.answer(rid, {"behavior": "allow", "relay_choice": {"policy": "auto"}} if channel == CH_UNLISTED else ALLOW)
    assert not (await task).get("is_error")
    assert [c[0] for c in h.adapter.network_calls()] == ["post"]


@pytest.mark.parametrize("channel,expected", [(CH_ASK, "approval_unavailable"), (CH_UNLISTED, "policy_unset")])
async def test_unattended_keeps_its_own_codes_before_dont_ask(h, channel, expected):  # AC8 ordering
    set_mode(h, DONT_ASK)
    h.coord._active_sdks[SID].deny_unattended_permission_prompts = True
    assert code_of(await CALLS["post"](h, channel)) == expected
    h.coord._active_sdks[SID].deny_unattended_permission_prompts = False
    h.env.info.is_ephemeral = True
    assert code_of(await CALLS["post"](h, channel)) == expected


async def test_mode_switch_needs_no_restart(h):  # AC5, UC3
    set_mode(h, "acceptEdits")
    task = asyncio.create_task(CALLS["post"](h, CH_ASK))
    [rid] = await h.wait_pending()
    h.answer(rid, ALLOW)
    assert not (await task).get("is_error")
    set_mode(h, DONT_ASK)
    assert code_of(await CALLS["post"](h, CH_ASK)) == "approval_unavailable_dont_ask"
    set_mode(h, "acceptEdits")
    task = asyncio.create_task(CALLS["post"](h, CH_ASK))
    [rid] = await h.wait_pending()
    h.answer(rid, ALLOW)
    assert not (await task).get("is_error")


async def test_waiting_prompt_stays_answerable_after_switch_into_dont_ask(h):
    set_mode(h, "acceptEdits")
    task = asyncio.create_task(CALLS["post"](h, CH_ASK))
    [rid] = await h.wait_pending()
    set_mode(h, DONT_ASK)
    # a call that starts after the switch refuses ...
    assert code_of(await CALLS["post"](h, CH_ASK)) == "approval_unavailable_dont_ask"
    # ... the one already waiting is still answered and sends (re_authorize ignores the mode)
    assert h.answer(rid, ALLOW)
    assert not (await task).get("is_error")
    assert [c[0] for c in h.adapter.network_calls()] == ["post"]
    assert [r for r in h.audit_rows() if r["outcome"] == "ok"][-1]["prompt_via"] == "handler"


async def test_valid_grant_is_honoured_in_dont_ask(h):
    set_mode(h, DONT_ASK)
    args = {"channel": CH_ASK, "text": "hello"}
    h.gate.grants.add(SID, "post", args, "prompt_allow")
    assert not (await h.tools.handle_post("s1", "post", args)).get("is_error")
    assert h.service.pending_permissions == {}


async def test_subagent_call_refuses_identically(h):
    set_mode(h, DONT_ASK)
    args = {"channel": CH_ASK, "text": "hello"}
    h.add_card("tu1", args)
    h.hook("tu1", args, agent_id="sub1")
    assert code_of(await h.tools.handle_post("s1", "post", args)) == "approval_unavailable_dont_ask"
    assert h.service.pending_permissions == {}


async def test_can_prompt_only_dont_ask_is_refused(h):
    for mode in ("default", "manual", "acceptEdits", "plan", "auto", "bypassPermissions"):
        set_mode(h, mode)
        assert await h.service.can_prompt(SID) == (True, None), mode
    set_mode(h, DONT_ASK)
    assert await h.service.can_prompt(SID) == (False, "dont_ask_mode")


def test_send_tool_descriptions_state_dont_ask_and_unattended():  # AC9
    from backend.mcp.relay_mcp_tools import _SEND_NOTE

    assert "don't-ask" in _SEND_NOTE and "unattended" in _SEND_NOTE and "bypass" in _SEND_NOTE
    assert "every permission mode" not in _SEND_NOTE
