"""Issue #2355 fix 4: one "awaiting permission" record per tool call, whatever the order.

Real ToolCallTracker + PermissionService + the production `_emit_tool_call_updates`, with
in-memory storage and queue. A turn is several SDK assistant messages, but the #1694 ordering
barrier is keyed by turn id: once a text-only assistant message of the turn was emitted the
barrier passes at once, so the permission request can reach the tracker BEFORE the tool_use
assistant message (the one that carries the input).
"""

from __future__ import annotations

import asyncio

import pytest

from backend.models.messages import ToolState
from backend.tests.relay_prompt_support import SID, Harness
from backend.tests.relay_support import CH_ASK
from backend.web_server import BackendApp

TURN = "turn-1"
ALLOW = {"behavior": "allow"}
BASH = {"command": "echo hello", "description": "say hi"}


class Rig(Harness):
    """Harness whose coordinator has a REAL per-turn "assistant message emitted" barrier."""

    def __init__(self, tmp_path, **kw):
        super().__init__(tmp_path, **kw)
        self.emitted_turns: set[str] = set()
        self.emitted_event = asyncio.Event()
        coord = self.coord
        coord.is_assistant_message_emitted = lambda sid, turn: turn in self.emitted_turns
        coord.get_message_emitted_event = lambda sid: self.emitted_event
        shim = type("Shim", (), {})()
        shim.coordinator = coord
        shim.session_queues = {SID: self.queue}
        shim.tenant_context = coord.tenant_context
        self._shim = shim

    def mark_emitted(self):
        self.emitted_turns.add(TURN)
        self.emitted_event.set()

    def pending_card(self, tool_use_id, name):
        """The content_block_start placeholder (#2109): input {} and the turn's id."""
        return self.coord.create_tool_call(
            SID, tool_use_id, name, {}, requires_permission=False, turn_id=TURN
        )

    async def tool_use_message(self, tool_use_id, name, args):
        await BackendApp._emit_tool_call_updates(self._shim, SID, {
            "type": "assistant", "turn_id": TURN,
            "metadata": {"tool_uses": [{"id": tool_use_id, "name": name, "input": args}]},
        })

    async def callback(self, tool_use_id, name, args):
        ctx = type("C", (), {"tool_use_id": tool_use_id, "agent_id": None, "suggestions": []})()
        return await self.service.create_permission_callback(SID)(name, args, ctx)

    def awaiting_stored(self, tool_use_id):
        return [
            r for r in self.storage.records
            if r.get("type") == "tool_call" and r.get("tool_use_id") == tool_use_id
            and r.get("status") == ToolState.AWAITING_PERMISSION.value
        ]

    def awaiting_emitted(self, tool_use_id):
        return [
            d for d in self.tool_call_events(tool_use_id)
            if d.get("status") == ToolState.AWAITING_PERMISSION.value
        ]

    def assert_single_awaiting(self, tool_use_id, args):
        stored, emitted = self.awaiting_stored(tool_use_id), self.awaiting_emitted(tool_use_id)
        assert len(stored) == 1, stored
        assert len(emitted) == 1, emitted
        assert stored[0]["input"] == args
        assert emitted[0]["input"] == args


@pytest.fixture(autouse=True)
def _real_permission_models(monkeypatch):
    import backend.models.messages as models
    import backend.permission_service as ps

    for name in ("PermissionInfo", "PermissionRequestMessage", "PermissionResponseMessage"):
        monkeypatch.setattr(ps, name, getattr(models, name))


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


async def _settle():
    await asyncio.sleep(0.05)


async def test_text_message_marks_turn_emitted_then_callback_then_tool_use(rig):
    """The reported order: barrier already passed, callback first, tool_use message 11 ms later."""
    rig.pending_card("tu1", "Bash")
    rig.mark_emitted()  # a text-only assistant message of the same turn
    task = asyncio.create_task(rig.callback("tu1", "Bash", BASH))
    [rid] = await rig.wait_pending()
    await rig.tool_use_message("tu1", "Bash", BASH)  # the late message that carries the input
    rig.answer(rid, ALLOW)
    assert (await task)["behavior"] == "allow"
    await _settle()
    rig.assert_single_awaiting("tu1", BASH)


async def test_tool_use_message_first_then_callback(rig):
    rig.pending_card("tu1", "Bash")
    await rig.tool_use_message("tu1", "Bash", BASH)
    rig.mark_emitted()
    task = asyncio.create_task(rig.callback("tu1", "Bash", BASH))
    [rid] = await rig.wait_pending()
    rig.answer(rid, ALLOW)
    await task
    await _settle()
    rig.assert_single_awaiting("tu1", BASH)


async def test_no_text_block_callback_waits_for_barrier_then_message(rig):
    rig.pending_card("tu1", "Bash")
    task = asyncio.create_task(rig.callback("tu1", "Bash", BASH))
    await asyncio.sleep(0.05)  # callback is parked on the turn barrier
    await rig.tool_use_message("tu1", "Bash", BASH)
    rig.mark_emitted()
    [rid] = await rig.wait_pending()
    rig.answer(rid, ALLOW)
    await task
    await _settle()
    rig.assert_single_awaiting("tu1", BASH)


async def test_consecutive_second_call_also_single(rig):
    for n in (1, 2):
        tid = f"tu{n}"
        rig.pending_card(tid, "Bash")
        rig.mark_emitted()
        task = asyncio.create_task(rig.callback(tid, "Bash", BASH))
        [rid] = await rig.wait_pending()
        await rig.tool_use_message(tid, "Bash", BASH)
        rig.answer(rid, ALLOW)
        await task
        await _settle()
        rig.assert_single_awaiting(tid, BASH)


# ---- the relay send paths: callback (default / acceptEdits) and handler broker (auto)

RELAY_ARGS = {"channel": CH_ASK, "text": "hello"}


async def test_relay_default_mode_callback_then_handler_single_record(rig):
    name = "mcp__relay_slack__post"
    rig.pending_card("tu1", name)
    rig.mark_emitted()
    task = asyncio.create_task(rig.callback("tu1", name, RELAY_ARGS))
    [rid] = await rig.wait_pending()
    await rig.tool_use_message("tu1", name, RELAY_ARGS)
    rig.answer(rid, ALLOW)
    assert (await task)["behavior"] == "allow"
    assert not (await rig.tools.handle_post("s1", "post", RELAY_ARGS)).get("is_error")  # grant
    await _settle()
    rig.assert_single_awaiting("tu1", RELAY_ARGS)


async def test_relay_auto_mode_handler_prompt_single_record_and_second_send(rig):
    name = "mcp__relay_slack__post"
    for n in (1, 2):
        tid = f"tu{n}"
        args = {**RELAY_ARGS, "text": f"hello {n}"}
        rig.pending_card(tid, name)
        rig.hook(tid, args)
        rig.mark_emitted()
        task = asyncio.create_task(rig.tools.handle_post("s1", "post", args))
        [rid] = await rig.wait_pending()
        await rig.tool_use_message(tid, name, args)
        rig.answer(rid, ALLOW)
        assert not (await task).get("is_error")
        await _settle()
        rig.assert_single_awaiting(tid, args)


# ---- tracker unit tests

async def test_input_update_on_awaiting_call_with_equal_input_writes_nothing(rig):
    card = rig.pending_card("tu1", "Bash")
    rig.coord.update_tool_call_input(SID, "tu1", BASH)
    card.status = ToolState.AWAITING_PERMISSION
    scheduled = []
    rig.coord.tracker._schedule_tool_call_update_storage = lambda *a, **k: scheduled.append(a)
    assert rig.coord.update_tool_call_input(SID, "tu1", dict(BASH)) is card
    assert scheduled == []


async def test_different_input_on_awaiting_call_still_stores(rig):
    card = rig.pending_card("tu1", "Bash")
    rig.coord.update_tool_call_input(SID, "tu1", BASH)
    card.status = ToolState.AWAITING_PERMISSION
    scheduled = []
    rig.coord.tracker._schedule_tool_call_update_storage = lambda *a, **k: scheduled.append(a)
    rig.coord.update_tool_call_input(SID, "tu1", {"command": "other"})
    assert len(scheduled) == 1 and card.input == {"command": "other"}


async def test_pending_placeholder_still_fills_with_real_input_issue_2109(rig):
    card = rig.pending_card("tu1", "Bash")
    scheduled = []
    rig.coord.tracker._schedule_tool_call_update_storage = lambda *a, **k: scheduled.append(a)
    rig.coord.update_tool_call_input(SID, "tu1", BASH)
    assert card.input == BASH and len(scheduled) == 1
