"""Issue #2353: relay approval prompt raised from inside the relay tool call (every mode)."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.models.messages import ToolState
from backend.relay import prompt_broker as pb
from backend.relay.policy import CHOOSE_POLICY, PROMPT
from backend.session_manager import SessionState
from backend.tests.relay_prompt_support import SID, Harness, settle
from backend.tests.relay_support import CH_ASK, CH_AUTO, CH_BLOCKED, CH_RO, CH_UNLISTED

ALLOW = {"behavior": "allow"}
DENY = {"behavior": "deny", "reason": "user_denied"}  # what the REST route sends


def code_of(result) -> str:
    assert result["is_error"] is True
    return json.loads(result["content"][1]["text"])["code"]


def args_for(channel=CH_ASK, text="hello"):
    return {"channel": channel, "text": text}


@pytest.fixture(autouse=True)
def _real_permission_models(monkeypatch):
    """Other permission suites patch these module names with MagicMocks; make sure a leaked patch
    cannot turn this suite's real prompts into mocks."""
    import backend.models.messages as models
    import backend.permission_service as ps

    for name in ("PermissionInfo", "PermissionRequestMessage", "PermissionResponseMessage"):
        monkeypatch.setattr(ps, name, getattr(models, name))


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


async def run_post(h, args, answer=ALLOW, *, card="tu1", hook=True):
    """Start a post that needs approval, answer the single prompt, return (result, request_id)."""
    if card:
        h.add_card(card, args)
        if hook:
            h.hook(card, args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    request_id = (await h.wait_pending())[0]
    if answer is not None:
        assert h.answer(request_id, answer)
    return await task, request_id


# ------------------------------------------------------------------ UC1/AC1/T1, AC8, AC10

async def test_ask_prompts_inside_the_tool_call_allow_posts(h):
    args = args_for()
    h.add_card("tu1", args)
    h.hook("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [request_id] = await h.wait_pending()
    card = h.coord.get_tool_call_by_id(SID, "tu1")
    # AC8: the original card turned awaiting_permission and carries the relay prompt
    assert card.status == ToolState.AWAITING_PERMISSION
    assert card.request_id == request_id
    assert card.permission.relay_prompt["kind"] == "ask_send"
    assert card.permission.relay_prompt["channel_id"] == CH_ASK
    assert h.coord.session_manager.info.state == SessionState.PAUSED
    assert h.adapter.network_calls() == []  # nothing sent while waiting
    h.answer(request_id, ALLOW)
    result = await task
    assert not result.get("is_error")
    assert [c[0] for c in h.adapter.network_calls()] == ["post"]
    assert card.status == ToolState.RUNNING
    assert h.coord.session_manager.info.state == SessionState.ACTIVE
    rows = [r for r in h.audit_rows() if r["outcome"] == "ok"]
    assert rows[-1]["approval"] == "prompt_allow"
    assert rows[-1]["prompt_via"] == "handler"
    # broker approvals mint no grant
    assert h.gate.grants.consume(SID, "post", args) is None


async def test_ask_deny_refuses_nothing_sent(h):
    result, _ = await run_post(h, args_for(), DENY)
    assert code_of(result) == "approval_denied"
    assert "owner declined" in result["content"][0]["text"]
    assert h.adapter.network_calls() == []
    card = h.coord.get_tool_call_by_id(SID, "tu1")
    assert card is None  # denied is terminal and removed from the active set
    assert h.coord.session_manager.info.state == SessionState.ACTIVE
    row = h.audit_rows()[-1]
    assert (row["outcome"], row["code"], row["prompt_via"]) == ("denied", "approval_denied", "handler")


async def test_ask_promote_persists_auto_and_audits_promote(h):
    result, _ = await run_post(
        h, args_for(), {"behavior": "allow", "relay_choice": {"promote": True}}
    )
    assert not result.get("is_error")
    assert h.env.info.config["relay_channel_policies"]["relay1"][CH_ASK] == "auto"
    ok = [r for r in h.audit_rows() if r["outcome"] == "ok"][-1]
    assert ok["approval"] == "prompt_promote" and ok["prompt_via"] == "handler"
    # the next send to that channel needs no prompt
    again = await h.tools.handle_post("s1", "post", args_for(text="second"))
    assert not again.get("is_error")


# ------------------------------------------------------------------ UC2/AC2/T2

async def test_unlisted_choose_auto_posts_and_saves_policy(h):
    args = args_for(CH_UNLISTED)
    h.add_card("tu1", args)
    h.hook("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    assert h.coord.get_tool_call_by_id(SID, "tu1").permission.relay_prompt["kind"] == "choose_policy"
    h.answer(rid, {"behavior": "allow", "relay_choice": {"policy": "auto"}})
    assert not (await task).get("is_error")
    assert h.env.info.config["relay_channel_policies"]["relay1"][CH_UNLISTED] == "auto"
    ok = [r for r in h.audit_rows() if r["outcome"] == "ok"][-1]
    assert ok["approval"] == "prompt_promote"
    pol = [r for r in h.audit_rows() if r["event"] == "policy_change"]
    assert pol and pol[-1]["policy"] == "auto"


async def test_unlisted_choose_read_only_refuses_send(h):
    result, _ = await run_post(
        h, args_for(CH_UNLISTED), {"behavior": "allow", "relay_choice": {"policy": "read_only"}}
    )
    assert code_of(result) == "send_not_permitted"
    assert h.adapter.network_calls() == []
    assert h.env.info.config["relay_channel_policies"]["relay1"][CH_UNLISTED] == "read_only"


async def test_unlisted_deny_refuses(h):
    result, _ = await run_post(h, args_for(CH_UNLISTED), DENY)
    assert code_of(result) == "approval_denied"
    assert CH_UNLISTED not in h.env.info.config["relay_channel_policies"]["relay1"]


# ------------------------------------------------------------------ AC3: permission mode is never read

async def test_permission_mode_is_never_consulted(tmp_path):
    # StrictInfo raises if anything reads a permission-mode attribute: every mode behaves alike
    h = Harness(tmp_path, permission_mode="bypassPermissions")
    h.env.info.current_permission_mode = "bypassPermissions"
    result, _ = await run_post(h, args_for(), ALLOW)
    assert not result.get("is_error")
    result, _ = await run_post(h, args_for(text="two"), DENY, card="tu2")
    assert code_of(result) == "approval_denied"


# ------------------------------------------------------------------ AC4 unchanged policies

@pytest.mark.parametrize("with_broker", [True, False])
async def test_auto_sends_without_prompt_blocked_and_read_only_refuse(tmp_path, with_broker):
    h = Harness(tmp_path)
    if not with_broker:
        h.env.tools.broker = None
    assert not (await h.tools.handle_post("s1", "post", args_for(CH_AUTO))).get("is_error")
    assert code_of(await h.tools.handle_post("s1", "post", args_for(CH_BLOCKED))) == "channel_blocked"
    assert code_of(await h.tools.handle_post("s1", "post", args_for(CH_RO))) == "send_not_permitted"
    assert h.service.pending_permissions == {}
    # reads of ask channels never prompt
    read = await h.tools.handle_read_thread(
        "s1", {"channel": CH_ASK, "thread_ts": "1700000000.000100"}
    )
    assert not read.get("is_error")
    assert h.service.pending_permissions == {}


# ------------------------------------------------------------------ AC5 exactly one prompt

async def test_callback_grant_path_does_not_prompt_again(h):
    args = args_for()
    h.env.gate.grants.add(SID, "post", args, "prompt_allow")  # what the callback's answer leaves
    called = []
    orig = h.broker.request

    async def spy(*a, **k):
        called.append(a)
        return await orig(*a, **k)

    h.broker.request = spy
    result = await h.tools.handle_post("s1", "post", args)
    assert not result.get("is_error")
    assert called == [] and h.service.pending_permissions == {}
    ok = [r for r in h.audit_rows() if r["outcome"] == "ok"][-1]
    assert (ok["approval"], ok["prompt_via"]) == ("prompt_allow", "callback")


async def test_callback_then_handler_yields_exactly_one_prompt(h):
    """default/acceptEdits: the callback prompts (request_decision), its allow mints the grant, the
    handler then consumes it. One request in total."""
    args = args_for()
    h.add_card("tu1", args)
    ctx_obj = type("C", (), {"tool_use_id": "tu1", "agent_id": None, "suggestions": []})()
    cb = h.service.create_permission_callback(SID)
    cb_task = asyncio.create_task(cb("mcp__relay_slack__post", args, ctx_obj))
    [rid] = await h.wait_pending()
    h.answer(rid, ALLOW)
    assert (await cb_task)["behavior"] == "allow"
    result = await h.tools.handle_post("s1", "post", args)
    assert not result.get("is_error")
    assert h.service.pending_permissions == {}
    prompts = [e for e in h.tool_call_events("tu1")
               if (e.get("permission") or {}).get("relay_prompt")]
    assert len({e.get("request_id") for e in prompts}) == 1


# ------------------------------------------------------------------ AC6 unattended

async def can_prompt(h):
    return await h.service.can_prompt(SID)


async def test_can_prompt_attended_session_is_true(h):
    assert await can_prompt(h) == (True, None)


async def test_can_prompt_false_when_unattended_prompts_denied(h):
    h.coord._active_sdks[SID].deny_unattended_permission_prompts = True
    assert await can_prompt(h) == (False, "deny_unattended_prompts")


async def test_can_prompt_false_for_ephemeral_session(h):
    h.env.info.is_ephemeral = True
    assert await can_prompt(h) == (False, "ephemeral_session")


async def test_can_prompt_false_without_permission_callback(h):
    h.coord._active_sdks[SID].permission_callback = None  # scheduler-style start_session
    assert await can_prompt(h) == (False, "no_permission_callback")


@pytest.mark.parametrize(
    "state", [SessionState.TERMINATING, SessionState.TERMINATED, SessionState.ERROR]
)
async def test_can_prompt_false_when_session_not_running(h, state):
    h.env.info.state = state
    assert await can_prompt(h) == (False, "session_not_running")


async def test_can_prompt_false_without_active_sdk(h):
    h.coord._active_sdks.clear()
    assert await can_prompt(h) == (False, "session_not_running")


async def test_can_prompt_true_for_permanent_minion_scheduled_session(h):
    # a permanent-minion schedule only enqueues a prompt into an ordinary attended session
    h.env.info.is_ephemeral = False
    h.env.info.config["schedule_id"] = "sched-1"
    assert await can_prompt(h) == (True, None)


@pytest.mark.parametrize("signal", ["deny_unattended", "ephemeral", "no_callback"])
async def test_unattended_send_refused_immediately_without_prompt(h, signal):
    if signal == "deny_unattended":
        h.coord._active_sdks[SID].deny_unattended_permission_prompts = True
    elif signal == "ephemeral":
        h.env.info.is_ephemeral = True
    else:
        h.coord._active_sdks[SID].permission_callback = None
    result = await asyncio.wait_for(h.tools.handle_post("s1", "post", args_for()), 1)
    assert code_of(result) == "approval_unavailable"
    text = result["content"][0]["text"]
    assert "No one is available to approve" in text and "`auto`" in text
    unlisted = await asyncio.wait_for(h.tools.handle_post("s1", "post", args_for(CH_UNLISTED)), 1)
    assert code_of(unlisted) == "policy_unset"
    assert f"Channel {CH_UNLISTED} has no policy and no one is available" in unlisted["content"][0]["text"]
    assert h.service.pending_permissions == {} and h.adapter.network_calls() == []
    rows = [r for r in h.audit_rows() if r["outcome"] == "denied"]
    assert [r["code"] for r in rows] == ["approval_unavailable", "policy_unset"]


# ------------------------------------------------------------------ AC9 / edge cases

async def test_timeout_refuses_clears_prompt_and_session(h):
    h.broker.timeout = 0.05
    result, rid = await run_post(h, args_for(), answer=None)
    assert code_of(result) == "approval_timeout"
    assert "within 10 minutes" in result["content"][0]["text"]
    assert h.adapter.network_calls() == []
    assert h.service.pending_permissions == {} and h.service.pending_by_session == {}
    assert h.coord.session_manager.info.state == SessionState.ACTIVE
    assert h.coord.get_tool_call_by_id(SID, "tu1") is None  # card ended denied
    assert h.answer(rid, ALLOW) is False  # late answer finds nothing...
    assert h.gate.grants.consume(SID, "post", args_for()) is None  # ...and mints nothing


def test_timeout_constant_is_600_seconds():
    from backend.relay.constants import RELAY_PROMPT_TIMEOUT_SECONDS

    assert RELAY_PROMPT_TIMEOUT_SECONDS == 600


async def test_interrupt_while_waiting_refuses_cancelled(h):
    args = args_for()
    h.add_card("tu1", args)
    h.hook("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    await h.wait_pending()
    await h.service.deny_for_session(SID)
    result = await task
    assert code_of(result) == "approval_cancelled"
    assert "interrupted or stopped" in result["content"][0]["text"]
    assert h.adapter.network_calls() == []
    assert h.service.pending_by_session == {}
    assert h.coord.session_manager.info.state == SessionState.ACTIVE
    assert h.audit_rows()[-1]["code"] == "approval_cancelled"


async def test_terminate_while_waiting_refuses_cancelled(h):
    args = args_for()
    h.add_card("tu1", args)
    h.hook("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    await h.wait_pending()
    h.service.cleanup_pending_for_session(SID)
    result = await task
    assert code_of(result) == "approval_cancelled"
    assert h.adapter.network_calls() == []
    # terminate also drops the session's grants and hook entries
    assert h.registry.claim(SID, "mcp__relay_slack__post", args) is None


async def test_task_cancellation_sends_nothing_and_clears_prompt(h):
    args = args_for()
    h.add_card("tu1", args)
    h.hook("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await settle()
    assert h.adapter.network_calls() == []
    assert h.service.pending_permissions == {} and h.service.pending_by_session == {}
    assert h.coord.session_manager.info.state == SessionState.ACTIVE
    assert h.coord.get_tool_call_by_id(SID, "tu1") is None  # prompt cleared: card denied
    assert h.answer(rid, ALLOW) is False
    assert h.audit_rows()[-1]["code"] == "approval_cancelled"


async def test_policy_edited_to_blocked_while_waiting_refuses(h):
    args = args_for()
    h.add_card("tu1", args)
    h.hook("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    h.env.info.config["relay_channel_policies"]["relay1"][CH_ASK] = "blocked"
    h.answer(rid, ALLOW)
    assert code_of(await task) == "channel_blocked"
    assert h.adapter.network_calls() == []


async def test_policy_edited_to_read_only_while_waiting_refuses(h):
    args = args_for()
    h.add_card("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    h.env.info.config["relay_channel_policies"]["relay1"][CH_ASK] = "read_only"
    h.answer(rid, ALLOW)
    assert code_of(await task) == "send_not_permitted"
    assert h.adapter.network_calls() == []


async def test_policy_edited_to_auto_while_waiting_still_sends_once(h):
    args = args_for()
    h.add_card("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    h.env.info.config["relay_channel_policies"]["relay1"][CH_ASK] = "auto"
    h.answer(rid, ALLOW)
    assert not (await task).get("is_error")
    assert len(h.adapter.network_calls()) == 1


async def test_relay_detached_while_waiting_refuses(h):
    args = args_for()
    h.add_card("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    h.env.info.config["relay_id"] = None
    h.answer(rid, ALLOW)
    assert code_of(await task) == "relay_not_attached"
    assert h.adapter.network_calls() == []


async def test_credential_removed_while_waiting_refuses(h):
    args = args_for()
    h.add_card("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    h.env.gate.relay_manager.has_token = lambda relay_id, kind="user": False
    h.answer(rid, ALLOW)
    assert code_of(await task) == "relay_not_attached"
    assert h.adapter.network_calls() == []


async def test_two_parallel_identical_sends_prompt_twice_independently(h):
    args = args_for()
    for tid in ("tu1", "tu2"):
        h.add_card(tid, args)
        h.hook(tid, args)
    t1 = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    t2 = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    rids = await h.wait_pending(2)
    cards = {tid: h.coord.get_tool_call_by_id(SID, tid) for tid in ("tu1", "tu2")}
    assert all(c.status == ToolState.AWAITING_PERMISSION for c in cards.values())
    assert {c.request_id for c in cards.values()} == set(rids)
    h.answer(cards["tu1"].request_id, ALLOW)
    h.answer(cards["tu2"].request_id, DENY)
    results = await asyncio.gather(t1, t2)
    assert sorted(r.get("is_error", False) for r in results) == [False, True]
    assert len(h.adapter.network_calls()) == 1


async def test_two_different_sends_prompt_independently(h):
    a1, a2 = args_for(text="one"), args_for(CH_UNLISTED, text="two")
    for tid, a in (("tu1", a1), ("tu2", a2)):
        h.add_card(tid, a)
        h.hook(tid, a)
    t1 = asyncio.create_task(h.tools.handle_post("s1", "post", a1))
    t2 = asyncio.create_task(h.tools.handle_post("s1", "post", a2))
    await h.wait_pending(2)
    k1 = h.coord.get_tool_call_by_id(SID, "tu1").permission.relay_prompt["kind"]
    k2 = h.coord.get_tool_call_by_id(SID, "tu2").permission.relay_prompt["kind"]
    assert (k1, k2) == ("ask_send", "choose_policy")
    h.answer(h.coord.get_tool_call_by_id(SID, "tu2").request_id,
             {"behavior": "allow", "relay_choice": {"policy": "ask"}})
    h.answer(h.coord.get_tool_call_by_id(SID, "tu1").request_id, ALLOW)
    r1, r2 = await asyncio.gather(t1, t2)
    assert not r1.get("is_error") and not r2.get("is_error")
    assert len(h.adapter.network_calls()) == 2


async def test_subagent_call_uses_its_own_tool_use_id_and_agent_id(h):
    args = args_for()
    h.add_card("tu_sub", args)
    h.hook("tu_sub", args, agent_id="agent-7")
    seen = {}
    orig = h.service.request_decision

    async def spy(*a, **k):
        seen.update(k)
        return await orig(*a, **k)

    h.service.request_decision = spy
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    assert seen["tool_use_id"] == "tu_sub" and seen["agent_id"] == "agent-7"
    assert h.coord.get_tool_call_by_id(SID, "tu_sub").status == ToolState.AWAITING_PERMISSION
    h.answer(rid, ALLOW)
    assert not (await task).get("is_error")


# ------------------------------------------------------------------ AC8 degradation: hook -> tracker -> standalone

async def test_hook_missing_falls_back_to_tracker_scan(h):
    args = args_for()
    card = h.add_card("tu_scan", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))  # no hook entry
    [rid] = await h.wait_pending()
    assert card.status == ToolState.AWAITING_PERMISSION and card.request_id == rid
    h.answer(rid, ALLOW)
    assert not (await task).get("is_error")


async def test_registry_failure_degrades_never_blocks(h):
    args = args_for()
    card = h.add_card("tu_scan", args)

    def boom(*a, **k):
        raise RuntimeError("registry down")

    h.registry.claim = boom
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    assert card.request_id == rid
    h.answer(rid, ALLOW)
    assert not (await task).get("is_error")


async def test_no_tool_call_found_shows_standalone_card_allow(h, monkeypatch):
    monkeypatch.setattr(pb, "TRACKER_WAIT_SECONDS", 0.01)
    args = args_for()
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))  # no card, no hook
    [rid] = await h.wait_pending()
    card = h.coord.get_tool_call_by_id(SID, f"relay-prompt-{rid}")
    assert card is not None and card.name == "mcp__relay_slack__post"
    assert card.status == ToolState.AWAITING_PERMISSION
    assert card.permission.relay_prompt["kind"] == "ask_send"
    h.answer(rid, ALLOW)
    assert not (await task).get("is_error")
    await settle()
    # finalised: nothing left active, and the stored history carries the synthetic id
    assert h.coord._active_tool_calls[SID] == {}
    final = [r for r in h.storage.records if r.get("tool_use_id") == f"relay-prompt-{rid}"]
    assert final and final[-1]["status"] == "completed"


async def test_no_tool_call_found_standalone_card_deny_and_timeout(h, monkeypatch):
    monkeypatch.setattr(pb, "TRACKER_WAIT_SECONDS", 0.01)
    result, rid = await run_post(h, args_for(), DENY, card=None)
    assert code_of(result) == "approval_denied"
    assert h.coord._active_tool_calls[SID] == {}
    final = [r for r in h.storage.records if r.get("tool_use_id") == f"relay-prompt-{rid}"]
    assert final[-1]["status"] == "denied"


async def test_standalone_card_cancel_leaves_no_orphan(h, monkeypatch):
    monkeypatch.setattr(pb, "TRACKER_WAIT_SECONDS", 0.01)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args_for()))
    await h.wait_pending()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await settle()
    assert h.coord._active_tool_calls[SID] == {}


# ------------------------------------------------------------------ gate: authorize / re_authorize

async def test_authorize_returns_needs_approval_with_prompt_instead_of_failing_closed(h):
    ctx = await h.gate.context(SID)
    result, grant = await h.gate.authorize(ctx, "post", args_for())
    assert grant is None and result.kind == PROMPT and result.prompt["kind"] == "ask_send"
    result, _ = await h.gate.authorize(ctx, "post", args_for(CH_UNLISTED))
    assert result.kind == CHOOSE_POLICY and result.prompt["kind"] == "choose_policy"


async def test_grants_are_isolated_by_session_tool_args_and_single_use(h):
    args = args_for()
    h.gate.grants.add(SID, "post", args, "prompt_allow")
    assert h.gate.grants.consume("other-session", "post", args) is None
    assert h.gate.grants.consume(SID, "reply", args) is None
    assert h.gate.grants.consume(SID, "post", args_for(text="other")) is None
    assert h.gate.grants.consume(SID, "post", args) is not None
    assert h.gate.grants.consume(SID, "post", args) is None


# ------------------------------------------------------------------ wording sweep (AC11)

def test_no_stale_permission_mode_wording_in_relay_sources():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in [*root.glob("relay/*.py"), root / "mcp" / "relay_mcp_tools.py"]:
        text = path.read_text().lower()
        for needle in ("does not prompt", "approval_required", "mode does not", "fail closed under"):
            if needle in text:
                offenders.append((path.name, needle))
    assert offenders == []


def test_permission_service_attaches_the_broker_to_its_coordinator(tmp_path):
    from types import SimpleNamespace

    from backend.permission_service import PermissionService
    from backend.relay.call_registry import RelayCallRegistry
    from backend.relay.prompt_broker import RelayPromptBroker

    broker = RelayPromptBroker(RelayCallRegistry(), bridge=None)
    coordinator = SimpleNamespace(relay=SimpleNamespace(broker=broker))
    service = PermissionService(coordinator, {})
    coordinator.permission_service = service
    assert broker._coordinator is coordinator
    assert broker._service() is service


# ------------------------------------------------------------------ review follow-ups

async def test_unclaimed_hook_entries_of_non_prompting_calls_do_not_leak_to_later_calls(h):
    """A completed auto send leaves a hook entry; _gated discards it, so a later identical call
    (policy now `ask`) prompts on ITS card, not the old one."""
    args = args_for(CH_AUTO)
    h.add_card("tu_old", args)
    h.hook("tu_old", args)
    assert not (await h.tools.handle_post("s1", "post", args)).get("is_error")
    assert h.registry.claim(SID, "mcp__relay_slack__post", args) is None
    h.env.info.config["relay_channel_policies"]["relay1"][CH_AUTO] = "ask"
    h.add_card("tu_new", args)
    h.hook("tu_new", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    assert h.coord.get_tool_call_by_id(SID, "tu_new").request_id == rid
    assert h.coord.get_tool_call_by_id(SID, "tu_old").status != ToolState.AWAITING_PERMISSION
    h.answer(rid, DENY)
    assert code_of(await task) == "approval_denied"


async def test_parallel_identical_calls_without_hook_bind_distinct_cards(h):
    args = args_for()
    for tid in ("tu1", "tu2"):
        h.add_card(tid, args)  # no hook entries: tracker scan for both
    t1 = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    t2 = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    await h.wait_pending(2)
    ids = {h.coord.get_tool_call_by_id(SID, t).request_id for t in ("tu1", "tu2")}
    assert len(ids) == 2 and None not in ids
    for rid in ids:
        h.answer(rid, DENY)
    await asyncio.gather(t1, t2)


async def test_bare_deny_without_reason_is_unavailable_not_an_owner_decline(h):
    args = args_for()
    h.add_card("tu1", args)

    async def failed_prompt(*a, **k):
        return {"behavior": "deny"}  # request_decision could not raise the prompt

    h.service.request_decision = failed_prompt
    result = await h.tools.handle_post("s1", "post", args)
    assert code_of(result) == "approval_unavailable"
    assert "declined" not in result["content"][0]["text"]
    assert h.adapter.network_calls() == []


async def test_cancel_before_the_future_wait_leaves_nothing_behind(h, monkeypatch):
    """Cancelled while the standalone card exists but the prompt is not yet awaiting an answer."""
    monkeypatch.setattr(pb, "TRACKER_WAIT_SECONDS", 0.01)
    gate = asyncio.Event()

    async def slow_pause(session_id):
        gate.set()
        await asyncio.sleep(10)

    h.coord.session_manager.pause_session = slow_pause
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args_for()))
    await asyncio.wait_for(gate.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await settle()
    assert h.service.pending_permissions == {} and h.service.pending_by_session == {}
    assert h.coord._active_tool_calls[SID] == {}
    assert h.adapter.network_calls() == []


async def test_policy_removed_while_an_ask_prompt_waits_refuses_policy_unset(h):
    args = args_for()
    h.add_card("tu1", args)
    task = asyncio.create_task(h.tools.handle_post("s1", "post", args))
    [rid] = await h.wait_pending()
    del h.env.info.config["relay_channel_policies"]["relay1"][CH_ASK]
    h.answer(rid, ALLOW)
    assert code_of(await task) == "policy_unset"
    assert h.adapter.network_calls() == []


def test_two_identical_callback_approvals_give_two_grants(h):
    args = args_for()
    h.gate.grants.add(SID, "post", args, "prompt_allow")
    h.gate.grants.add(SID, "post", args, "prompt_allow")
    assert h.gate.grants.consume(SID, "post", args) is not None
    assert h.gate.grants.consume(SID, "post", args) is not None
    assert h.gate.grants.consume(SID, "post", args) is None
