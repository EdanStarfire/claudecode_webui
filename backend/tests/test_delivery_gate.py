"""Tests for DeliveryGate's unified AC3/AC4 gate (issue #2193, PR 3).

Fixture style mirrors test_comm_router.py's mock-coordinator convention, but backed
by a **real** PendingStore instance (not mocked) per PR2's "first real caller"
framing — the ledger side effects (enqueue/mark_delivered/mark_failed, positions)
are genuinely exercised here, not stubbed out.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.delivery_gate import DeliveryGate
from backend.models.legion_models import Comm, CommType, DeliveryPolicy, SenderType
from backend.pending_store import PendingStore
from backend.session_manager import SessionInfo, SessionState


def make_comm(session_id: str, *, comm_id: str | None = None, content: str = "hello", **overrides) -> Comm:
    """Build a valid Comm addressed to `session_id` (from the user, unless overridden)."""
    kwargs = {
        "comm_id": comm_id or str(uuid.uuid4()),
        "from_user": True,
        "to_minion_id": session_id,
        "content": content,
    }
    kwargs.update(overrides)
    return Comm(**kwargs)


def _make_session_info(
    session_id="s1",
    state=SessionState.ACTIVE,
    is_processing=False,
    queue_config=None,
    queue_paused=False,
):
    """Build a minimal SessionInfo-like object for tests."""
    info = MagicMock(spec=SessionInfo)
    info.session_id = session_id
    info.state = state
    info.is_processing = is_processing
    info.queue_config = queue_config
    info.error_message = None
    # Issue #2193 PR4: explicit, not left to spec's auto-mock default (which is a
    # truthy MagicMock) — AC6's queue_paused gate must see a real False here, or
    # every existing PR3 test would spuriously hit the "queues instead" branch.
    info.queue_paused = queue_paused
    return info


def _make_coordinator(session_info, session_dir):
    """Build a mock SessionCoordinator backed by a real PendingStore."""
    coord = MagicMock()
    coord.session_manager = AsyncMock()
    coord.session_manager.get_session_info = AsyncMock(return_value=session_info)
    coord.session_manager.get_session_directory = AsyncMock(return_value=session_dir)
    coord.session_manager.update_session = AsyncMock(return_value=True)
    coord.send_message = AsyncMock(return_value=True)
    coord.start_session = AsyncMock(return_value=True)
    coord.interrupt_session = AsyncMock(return_value=True)
    coord.wait_for_session_ready = AsyncMock(return_value=True)
    coord._permission_callback_factory = None
    coord._message_callback_registrar = None
    # Issue #2193 PR4 (AC7): authorized by default so every existing PR3 test (which
    # doesn't care about authority) keeps passing unchanged; individual PR4 tests
    # override to return_value=False where needed.
    coord.legion_system = MagicMock()
    coord.legion_system.comm_router = AsyncMock(is_authorized_sender=AsyncMock(return_value=True))
    coord.pending_store = PendingStore()
    return coord


def _status_of(coord, session_id, comm_id):
    for item in coord.pending_store.get_all(session_id):
        if item.comm.comm_id == comm_id:
            return item.status
    return None


# Issue #2193 PR3 tests patch `backend.delivery_gate.asyncio.sleep` — since
# `asyncio` is the same module object everywhere, that patches asyncio.sleep
# globally for the duration of the `with patch(...)` block. Capture the real
# function up front so the replacements below (and any plain `asyncio.sleep(0)`
# used elsewhere in this file while a patch is active) don't recurse into
# themselves.
_real_sleep = asyncio.sleep


async def _fast_sleep(_seconds):
    """Replacement for delivery_gate's real `asyncio.sleep` calls in ordering/settle
    tests: a genuine (but zero-duration) yield to the event loop. A literal no-op
    mock would never suspend at all, which turns any poll-until-condition loop
    (e.g. `_wait_for_busy_clear`) into a non-yielding infinite spin that can never
    observe a state change made by concurrent test code — this keeps the loop
    cooperative while staying fast."""
    await _real_sleep(0)


class TestOrderingMatrix:
    """Issue #2193 AC4 — the two worked ordering examples, traced through
    concurrent/interleaved dispatch, not just the happy path."""

    @pytest.mark.asyncio
    async def test_busy_session_immediate_folds_ahead_of_when_idle_fifo(self, tmp_path):
        """a(when_idle, pos0), b(when_idle, pos1), c(immediate, pos2) arrive while the
        session is mid-turn: c skips the busy gate entirely and delivers first; a and
        b drain from the background loop once (and between) turns. Order: c, a, b."""
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=True)
        coord = _make_coordinator(info, tmp_path)

        call_order = []

        async def fake_send_message(session_id, message, metadata=None, inject_timestamp=True):
            call_order.append(message)
            if message == "content-a":
                info.is_processing = True  # a's send starts a new turn
            return True

        coord.send_message = AsyncMock(side_effect=fake_send_message)
        gate = DeliveryGate(coord)

        a = make_comm("s1", content="content-a", delivery=DeliveryPolicy.WHEN_IDLE)
        b = make_comm("s1", content="content-b", delivery=DeliveryPolicy.WHEN_IDLE)
        c = make_comm("s1", content="content-c", delivery=DeliveryPolicy.IMMEDIATE)

        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            await gate.deliver(a)
            await gate.deliver(b)

            result_c = await gate.deliver(c)
            assert result_c.success is True
            assert call_order == ["content-c"]

            for _ in range(5):
                await asyncio.sleep(0)
            assert call_order == ["content-c"]  # a/b still blocked on the busy gate

            info.is_processing = False  # turn ends
            for _ in range(50):
                await asyncio.sleep(0)
                if "content-a" in call_order:
                    break
            assert call_order == ["content-c", "content-a"]

            for _ in range(5):
                await asyncio.sleep(0)
            assert call_order == ["content-c", "content-a"]  # b busy again (a's new turn)

            info.is_processing = False
            for _ in range(50):
                await asyncio.sleep(0)
                if "content-b" in call_order:
                    break

        assert call_order == ["content-c", "content-a", "content-b"]
        gate.stop_when_idle("s1")

    @pytest.mark.asyncio
    async def test_readiness_countdown_shared_barrier_preserves_arrival_order(self, tmp_path):
        """a(pos0, when_idle), c(pos1, immediate), b(pos2, when_idle) all arrive while
        a is inside the active 5s settle window. Only a's wait triggers the real
        auto-start; c rides the same shared deadline once the session is ready, and
        dispatches right after a releases the lock. Order: a, c, b."""
        info = _make_session_info(state=SessionState.CREATED, is_processing=False)
        coord = _make_coordinator(info, tmp_path)

        async def fake_start_session(session_id, permission_callback=None):
            info.state = SessionState.STARTING
            return True

        async def fake_wait_for_session_ready(session_id, timeout=None):
            info.state = SessionState.ACTIVE
            return True

        coord.start_session = AsyncMock(side_effect=fake_start_session)
        coord.wait_for_session_ready = AsyncMock(side_effect=fake_wait_for_session_ready)

        call_order = []

        async def fake_send_message(session_id, message, metadata=None, inject_timestamp=True):
            call_order.append(message)
            if message == "content-a":
                info.is_processing = True  # a's send starts a new turn
            return True

        coord.send_message = AsyncMock(side_effect=fake_send_message)
        gate = DeliveryGate(coord)

        a = make_comm("s1", content="content-a", delivery=DeliveryPolicy.WHEN_IDLE)
        c = make_comm("s1", content="content-c", delivery=DeliveryPolicy.IMMEDIATE)
        b = make_comm("s1", content="content-b", delivery=DeliveryPolicy.WHEN_IDLE)

        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            await gate.deliver(a)
            c_task = asyncio.create_task(gate.deliver(c))
            await gate.deliver(b)

            for _ in range(50):
                await asyncio.sleep(0)
                if c_task.done():
                    break
            assert call_order == ["content-a", "content-c"]

            info.is_processing = False  # let b's busy check pass
            for _ in range(50):
                await asyncio.sleep(0)
                if "content-b" in call_order:
                    break

        assert call_order == ["content-a", "content-c", "content-b"]
        c_result = await c_task
        assert c_result.success is True
        gate.stop_when_idle("s1")

    @pytest.mark.asyncio
    async def test_pivot_interrupts_then_sends_after_readiness(self, tmp_path):
        """Mirrors test_pivot_to_dormant_target_runs_gate_before_interrupt against
        the new deliver() path: start_session, interrupt_session, send_message."""
        info = _make_session_info(state=SessionState.CREATED)
        coord = _make_coordinator(info, tmp_path)

        call_order = []

        async def record_start_session(session_id, permission_callback=None):
            call_order.append("start_session")
            info.state = SessionState.ACTIVE
            return True

        async def record_interrupt(session_id):
            call_order.append("interrupt_session")
            return True

        async def record_send_message(session_id, message, metadata=None, inject_timestamp=True):
            call_order.append("send_message")
            return True

        coord.start_session = AsyncMock(side_effect=record_start_session)
        coord.interrupt_session = AsyncMock(side_effect=record_interrupt)
        coord.send_message = AsyncMock(side_effect=record_send_message)
        gate = DeliveryGate(coord)

        comm = make_comm("s1", content="pivot now", delivery=DeliveryPolicy.PIVOT)

        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            result = await gate.deliver(comm)

        assert result.success is True
        assert call_order == ["start_session", "interrupt_session", "send_message"]
        assert _status_of(coord, "s1", comm.comm_id) == "delivered"

    @pytest.mark.asyncio
    async def test_halt_interrupts_without_sending(self, tmp_path):
        """Mirrors the HALT equivalent: start_session, interrupt_session, no
        send_message — and the entry ends up status == 'delivered'."""
        info = _make_session_info(state=SessionState.CREATED)
        coord = _make_coordinator(info, tmp_path)

        call_order = []

        async def record_start_session(session_id, permission_callback=None):
            call_order.append("start_session")
            info.state = SessionState.ACTIVE
            return True

        async def record_interrupt(session_id):
            call_order.append("interrupt_session")
            return True

        coord.start_session = AsyncMock(side_effect=record_start_session)
        coord.interrupt_session = AsyncMock(side_effect=record_interrupt)
        gate = DeliveryGate(coord)

        comm = make_comm("s1", content="halt now", delivery=DeliveryPolicy.HALT)

        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            result = await gate.deliver(comm)

        assert result.success is True
        assert call_order == ["start_session", "interrupt_session"]
        coord.send_message.assert_not_called()
        assert _status_of(coord, "s1", comm.comm_id) == "delivered"

    @pytest.mark.asyncio
    async def test_two_immediate_items_dispatch_are_lock_serialized(self, tmp_path):
        """Fire two immediate deliver() calls concurrently; the dispatch lock must
        ensure send_message is never invoked while another call is in flight."""
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        concurrent = 0
        max_concurrent = 0

        async def tracking_send_message(session_id, message, metadata=None, inject_timestamp=True):
            nonlocal concurrent, max_concurrent
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            await asyncio.sleep(0)
            concurrent -= 1
            return True

        coord.send_message = AsyncMock(side_effect=tracking_send_message)

        c1 = make_comm("s1", content="one", delivery=DeliveryPolicy.IMMEDIATE)
        c2 = make_comm("s1", content="two", delivery=DeliveryPolicy.IMMEDIATE)

        results = await asyncio.gather(gate.deliver(c1), gate.deliver(c2))

        assert all(r.success for r in results)
        assert max_concurrent == 1

    @pytest.mark.asyncio
    async def test_halt_interrupt_failure_marks_entry_failed(self, tmp_path):
        """HALT has no later step to fall back on, so when interrupt_session()
        itself fails, the ledger must say 'failed', not 'delivered' — the gate
        did not actually perform its intended action."""
        info = _make_session_info(state=SessionState.ACTIVE)
        coord = _make_coordinator(info, tmp_path)
        coord.interrupt_session = AsyncMock(return_value=False)
        gate = DeliveryGate(coord)

        comm = make_comm("s1", content="halt now", delivery=DeliveryPolicy.HALT)
        result = await gate.deliver(comm)

        assert result.success is False
        coord.send_message.assert_not_called()
        assert _status_of(coord, "s1", comm.comm_id) == "failed"

    @pytest.mark.asyncio
    async def test_send_exception_marks_entry_failed_not_stuck_pending(self, tmp_path):
        """If send_message() raises instead of returning False, the entry must
        still reach a terminal ledger state, not stay 'pending' forever."""
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
        coord = _make_coordinator(info, tmp_path)
        coord.send_message = AsyncMock(side_effect=RuntimeError("transport exploded"))
        gate = DeliveryGate(coord)

        comm = make_comm("s1", content="hello", delivery=DeliveryPolicy.IMMEDIATE)
        result = await gate.deliver(comm)

        assert result.success is False
        assert "transport exploded" in result.error
        assert _status_of(coord, "s1", comm.comm_id) == "failed"

    @pytest.mark.asyncio
    async def test_when_idle_loop_finally_does_not_evict_a_newer_task(self, tmp_path):
        """Regression: the loop's finally block must not pop whatever task is
        currently registered for this session_id if it isn't *this* task —
        e.g. stop_when_idle() already popped+cancelled this one and a new comm
        started a replacement before this task's cancellation finished unwinding."""
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        async def noop():
            pass

        stand_in_task = asyncio.create_task(noop())
        await asyncio.sleep(0)
        gate._when_idle_tasks["s1"] = stand_in_task

        # No pending items for s1 -> the loop exits immediately via its own
        # `break`, then its finally runs under the *test's* task identity,
        # not stand_in_task's.
        await gate._run_when_idle_loop("s1")

        assert gate._when_idle_tasks.get("s1") is stand_in_task


class TestReadinessUnification:
    """Issue #2193 AC3 — the unified readiness/auto-start predicate."""

    @pytest.mark.asyncio
    async def test_dormant_session_auto_starts_waits_and_settles(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.delivery_gate.READINESS_SETTLE_SECONDS", 0.01)
        info = _make_session_info(state=SessionState.CREATED)
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        comm = make_comm("s1")
        result = await gate._ensure_session_ready(comm)

        assert result.success is True
        coord.start_session.assert_awaited_once()
        coord.wait_for_session_ready.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_starting_session_waits_without_restarting(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.delivery_gate.READINESS_SETTLE_SECONDS", 0.01)
        info = _make_session_info(state=SessionState.STARTING)
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        comm = make_comm("s1")
        result = await gate._ensure_session_ready(comm)

        assert result.success is True
        coord.start_session.assert_not_awaited()
        coord.wait_for_session_ready.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_state_fails_fast_without_restart(self, tmp_path):
        """Explicit, flagged behavior change: unlike comm_router's current
        ensure_ready(), the unified gate does not auto-restart an ERRORED target."""
        info = _make_session_info(state=SessionState.ERROR)
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        comm = make_comm("s1")
        result = await gate.deliver(comm)

        assert result.success is False
        assert "ERROR" in result.error
        coord.start_session.assert_not_called()
        assert _status_of(coord, "s1", comm.comm_id) == "failed"

    @pytest.mark.asyncio
    async def test_paused_session_ready_without_waiting(self, tmp_path):
        """Proves the PAUSED-is-already-fine unification: zero waiting, no
        wait_for_session_ready() call at all."""
        info = _make_session_info(state=SessionState.PAUSED)
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        comm = make_comm("s1")
        result = await gate._ensure_session_ready(comm)

        assert result.success is True
        coord.start_session.assert_not_called()
        coord.wait_for_session_ready.assert_not_called()


class TestSettleOptionScoping:
    """Issue #2193 AC3 — the schedule-only pre/post-send settle option."""

    @pytest.mark.asyncio
    async def test_minion_when_idle_dispatches_without_schedule_settle(self, tmp_path):
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        sleep_calls = []

        async def recording_sleep(seconds):
            sleep_calls.append(seconds)
            await _real_sleep(0)

        comm = make_comm(
            "s1", delivery=DeliveryPolicy.WHEN_IDLE, sender_type=SenderType.MINION,
            from_user=False, from_minion_id="other-minion",
        )

        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=recording_sleep)):
            await gate.deliver(comm)
            for _ in range(50):
                await asyncio.sleep(0)
                if coord.send_message.await_count:
                    break

        coord.send_message.assert_awaited_once()
        # The test's own polling loop above also goes through the patched sleep
        # (recorded as 0); no min_wait_seconds/min_idle_seconds-scale (10s) delay
        # from the gate itself should appear.
        assert all(s == 0 for s in sleep_calls)
        gate.stop_when_idle("s1")

    @pytest.mark.asyncio
    async def test_schedule_when_idle_goes_through_settle_sleeps(self, tmp_path):
        info = _make_session_info(
            state=SessionState.ACTIVE, is_processing=False,
            queue_config={"min_wait_seconds": 0.05, "min_idle_seconds": 0.05},
        )
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        sleep_calls = []

        async def recording_sleep(seconds):
            sleep_calls.append(seconds)
            await _real_sleep(0)

        comm = make_comm("s1", delivery=DeliveryPolicy.WHEN_IDLE, sender_type=SenderType.SCHEDULE)

        with (
            patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=recording_sleep)),
            patch.object(gate, "_wait_for_idle", new=AsyncMock(return_value=True)) as wait_idle_mock,
        ):
            await gate.deliver(comm)
            for _ in range(50):
                await asyncio.sleep(0)
                if coord.send_message.await_count:
                    break
            for _ in range(50):
                await asyncio.sleep(0)
                if wait_idle_mock.await_count:
                    break

        coord.send_message.assert_awaited_once()
        assert 0.05 in sleep_calls  # pre-send min_wait_seconds
        wait_idle_mock.assert_awaited_once_with("s1", 0.05)
        gate.stop_when_idle("s1")


class TestInjectTimestampScoping:
    """Direct unit tests of _should_inject_timestamp — no gate needed."""

    @pytest.mark.parametrize(
        "sender_type,expected",
        [
            (SenderType.USER, True),
            (SenderType.SCHEDULE, True),
            (SenderType.MINION, False),
            (SenderType.SYSTEM, False),
            (SenderType.EXTERNAL, False),
        ],
    )
    def test_should_inject_timestamp(self, sender_type, expected):
        kwargs = {"from_user": sender_type == SenderType.USER}
        if sender_type != SenderType.USER:
            kwargs["from_minion_id"] = "sender-x"
        comm = make_comm("s1", sender_type=sender_type, **kwargs)
        assert DeliveryGate._should_inject_timestamp(comm) == expected


class TestSenderScopedFlush:
    """Issue #2193 AC5 — sender-scoped pivot/halt flush."""

    @pytest.mark.asyncio
    async def test_pivot_cancels_only_same_sender_pending_items(self, tmp_path):
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        a1 = make_comm(
            "s1", content="a1", delivery=DeliveryPolicy.WHEN_IDLE,
            from_user=False, from_minion_id="sender-a",
        )
        a2 = make_comm(
            "s1", content="a2", delivery=DeliveryPolicy.WHEN_IDLE,
            from_user=False, from_minion_id="sender-a",
        )
        b1 = make_comm(
            "s1", content="b1", delivery=DeliveryPolicy.WHEN_IDLE,
            from_user=False, from_minion_id="sender-b",
        )
        await coord.pending_store.enqueue("s1", tmp_path, a1)
        await coord.pending_store.enqueue("s1", tmp_path, a2)
        await coord.pending_store.enqueue("s1", tmp_path, b1)

        pivot = make_comm(
            "s1", content="pivot now", delivery=DeliveryPolicy.PIVOT,
            from_user=False, from_minion_id="sender-a",
        )

        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            result = await gate.deliver(pivot)

        assert result.success is True
        assert _status_of(coord, "s1", a1.comm_id) == "cancelled"
        assert _status_of(coord, "s1", a2.comm_id) == "cancelled"
        assert _status_of(coord, "s1", b1.comm_id) == "pending"
        assert _status_of(coord, "s1", pivot.comm_id) == "delivered"

    @pytest.mark.asyncio
    async def test_flush_sender_racing_when_idle_dispatch_has_single_terminal_outcome(self, tmp_path):
        """Issue #2193 PR4 race fix: an item already popped off when_idle_pending and
        sitting in the busy-wait must not be resurrected into 'delivered' by a
        concurrent flush_sender() that cancels it first — the post-lock re-check in
        _run_when_idle_loop must skip the send entirely once that happens."""
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=True)
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        item_comm = make_comm(
            "s1", content="late item", delivery=DeliveryPolicy.WHEN_IDLE,
            from_user=False, from_minion_id="race-sender",
        )
        await coord.pending_store.enqueue("s1", tmp_path, item_comm)

        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            gate.ensure_when_idle_running("s1")

            # Let the loop pick up item_comm and park in the busy-wait (is_processing=True).
            for _ in range(20):
                await asyncio.sleep(0)
            assert _status_of(coord, "s1", item_comm.comm_id) == "pending"
            coord.send_message.assert_not_called()

            # A concurrent HALT/PIVOT's flush_sender() for the same sender wins the
            # race while the when_idle dispatch is still stuck pre-lock.
            trigger = make_comm(
                "s1", content="pivot now", delivery=DeliveryPolicy.PIVOT,
                from_user=False, from_minion_id="race-sender",
            )
            cancelled = await gate.flush_sender("s1", trigger)
            assert len(cancelled) == 1
            assert _status_of(coord, "s1", item_comm.comm_id) == "cancelled"

            # Now let the busy-wait clear and the loop reach the lock's re-check.
            info.is_processing = False
            for _ in range(50):
                await asyncio.sleep(0)

        # Exactly one terminal outcome, no flip back to "delivered", and the send
        # that would have resurrected it never happened.
        assert _status_of(coord, "s1", item_comm.comm_id) == "cancelled"
        coord.send_message.assert_not_called()
        gate.stop_when_idle("s1")


class TestPivotStaleNote:
    """Issue #2193 AC5 — "queued before the pivot, by whom" note."""

    @pytest.mark.asyncio
    async def test_stale_item_dispatch_carries_note_not_persisted_to_ledger(self, tmp_path):
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
        coord = _make_coordinator(info, tmp_path)

        captured_messages = []

        async def fake_send_message(session_id, message, metadata=None, inject_timestamp=True):
            captured_messages.append(message)
            return True

        coord.send_message = AsyncMock(side_effect=fake_send_message)
        gate = DeliveryGate(coord)

        stale = make_comm(
            "s1", content="original content", delivery=DeliveryPolicy.WHEN_IDLE,
            from_user=False, from_minion_id="late-sender",
        )
        await coord.pending_store.enqueue("s1", tmp_path, stale)

        pivot = make_comm(
            "s1", content="pivot message", delivery=DeliveryPolicy.PIVOT,
            from_user=False, from_minion_id="other-sender", from_minion_name="OtherSender",
        )

        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            pivot_result = await gate.deliver(pivot)
            assert pivot_result.success is True

            gate.ensure_when_idle_running("s1")
            for _ in range(50):
                await asyncio.sleep(0)
                if len(captured_messages) >= 2:
                    break

        assert len(captured_messages) == 2
        assert captured_messages[0] == "pivot message"  # no self-referential note
        assert "original content" in captured_messages[1]
        assert "queued before a pivot from OtherSender" in captured_messages[1]

        # The ledger's persisted copy must stay the original, un-annotated content.
        ledger_item = coord.pending_store.get_item("s1", stale.comm_id)
        assert ledger_item.comm.content == "original content"
        assert ledger_item.status == "delivered"
        gate.stop_when_idle("s1")

    @pytest.mark.asyncio
    async def test_failed_pivot_send_does_not_stamp_last_pivot(self, tmp_path):
        """Found during review: _last_pivot must only be stamped after the pivot's
        own send has actually succeeded — a failed PIVOT send must not poison a
        later WHEN_IDLE item's dispatch with a note about a pivot that never took
        effect."""
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False)
        coord = _make_coordinator(info, tmp_path)

        captured_messages = []

        async def fake_send_message(session_id, message, metadata=None, inject_timestamp=True):
            # Fail only the pivot's own content; succeed for everything else.
            if message == "pivot message":
                return False
            captured_messages.append(message)
            return True

        coord.send_message = AsyncMock(side_effect=fake_send_message)
        gate = DeliveryGate(coord)

        other = make_comm(
            "s1", content="other item", delivery=DeliveryPolicy.WHEN_IDLE,
            from_user=False, from_minion_id="late-sender",
        )
        await coord.pending_store.enqueue("s1", tmp_path, other)

        pivot = make_comm(
            "s1", content="pivot message", delivery=DeliveryPolicy.PIVOT,
            from_user=False, from_minion_id="other-sender", from_minion_name="OtherSender",
        )

        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            pivot_result = await gate.deliver(pivot)
            assert pivot_result.success is False

            gate.ensure_when_idle_running("s1")
            for _ in range(50):
                await asyncio.sleep(0)
                if captured_messages:
                    break

        assert captured_messages == ["other item"]  # no stale-pivot note appended
        gate.stop_when_idle("s1")


class TestAuthorityGate:
    """Issue #2193 AC7 — gate-level HALT/PIVOT authority (inert until PR5 wires the
    gate into production; test_legion_mcp_tools.py covers the live tool-layer path)."""

    @pytest.mark.asyncio
    async def test_halt_without_authority_fails_without_interrupting(self, tmp_path):
        info = _make_session_info(state=SessionState.ACTIVE)
        coord = _make_coordinator(info, tmp_path)
        coord.legion_system.comm_router.is_authorized_sender = AsyncMock(return_value=False)
        gate = DeliveryGate(coord)

        # from_user=False/from_minion_id set: _is_authorized() always returns True
        # for from_user=True (the user is always authorized), so an "unauthorized
        # sender" case must be a minion sender, not the make_comm default.
        comm = make_comm(
            "s1", content="halt now", delivery=DeliveryPolicy.HALT,
            from_user=False, from_minion_id="sibling-a",
        )
        result = await gate.deliver(comm)

        assert result.success is False
        assert "authority" in result.error.lower()
        coord.interrupt_session.assert_not_called()
        coord.send_message.assert_not_called()
        assert _status_of(coord, "s1", comm.comm_id) == "failed"

    @pytest.mark.asyncio
    async def test_pivot_without_authority_fails_without_interrupting_or_sending(self, tmp_path):
        info = _make_session_info(state=SessionState.ACTIVE)
        coord = _make_coordinator(info, tmp_path)
        coord.legion_system.comm_router.is_authorized_sender = AsyncMock(return_value=False)
        gate = DeliveryGate(coord)

        comm = make_comm(
            "s1", content="pivot now", delivery=DeliveryPolicy.PIVOT,
            from_user=False, from_minion_id="sibling-a",
        )
        result = await gate.deliver(comm)

        assert result.success is False
        assert "authority" in result.error.lower()
        coord.interrupt_session.assert_not_called()
        coord.send_message.assert_not_called()
        assert _status_of(coord, "s1", comm.comm_id) == "failed"

    @pytest.mark.asyncio
    async def test_user_sent_halt_succeeds_even_without_legion_system(self, tmp_path):
        """Found during review: _is_authorized() must check comm.from_user before
        the legion_system=None guard — the user is always authorized regardless of
        whether a CommRouter is attached, per is_authorized_sender()'s own rule."""
        info = _make_session_info(state=SessionState.ACTIVE)
        coord = _make_coordinator(info, tmp_path)
        coord.legion_system = None
        gate = DeliveryGate(coord)

        comm = make_comm("s1", content="halt now", delivery=DeliveryPolicy.HALT, from_user=True)
        result = await gate.deliver(comm)

        assert result.success is True
        coord.interrupt_session.assert_awaited_once()


class TestQueuePausedGate:
    """Issue #2193 AC6 — up-front queue_paused gate."""

    @staticmethod
    def _wire_stateful_update_session(coord, info):
        async def fake_update_session(session_id, **kwargs):
            if "queue_paused" in kwargs:
                info.queue_paused = kwargs["queue_paused"]
            return True
        coord.session_manager.update_session = AsyncMock(side_effect=fake_update_session)

    @pytest.mark.asyncio
    async def test_paused_session_queues_immediate_comm_without_sending(self, tmp_path):
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False, queue_paused=True)
        coord = _make_coordinator(info, tmp_path)
        gate = DeliveryGate(coord)

        comm = make_comm("s1", content="hi", delivery=DeliveryPolicy.IMMEDIATE)
        result = await gate.deliver(comm)

        assert result.success is True
        coord.send_message.assert_not_called()
        assert _status_of(coord, "s1", comm.comm_id) == "pending"
        assert info.queue_paused is True

    @pytest.mark.asyncio
    async def test_authorized_task_while_paused_clears_pause_and_delivers(self, tmp_path):
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False, queue_paused=True)
        coord = _make_coordinator(info, tmp_path)
        self._wire_stateful_update_session(coord, info)
        gate = DeliveryGate(coord)

        comm = make_comm(
            "s1", content="hi", delivery=DeliveryPolicy.IMMEDIATE, comm_type=CommType.TASK,
        )
        result = await gate.deliver(comm)

        assert result.success is True
        coord.send_message.assert_awaited_once()
        assert info.queue_paused is False
        assert _status_of(coord, "s1", comm.comm_id) == "delivered"

    @pytest.mark.asyncio
    async def test_non_resume_trigger_type_while_paused_still_queues(self, tmp_path):
        """REPORT is not an implicit-resume-trigger comm_type — stays queued even
        from an authorized sender."""
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False, queue_paused=True)
        coord = _make_coordinator(info, tmp_path)
        self._wire_stateful_update_session(coord, info)
        gate = DeliveryGate(coord)

        comm = make_comm(
            "s1", content="hi", delivery=DeliveryPolicy.IMMEDIATE, comm_type=CommType.REPORT,
        )
        result = await gate.deliver(comm)

        assert result.success is True
        coord.send_message.assert_not_called()
        assert info.queue_paused is True
        assert _status_of(coord, "s1", comm.comm_id) == "pending"

    @pytest.mark.asyncio
    async def test_unauthorized_task_while_paused_still_queues(self, tmp_path):
        """Authority applies to the implicit-resume trigger too, not just HALT/PIVOT."""
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False, queue_paused=True)
        coord = _make_coordinator(info, tmp_path)
        self._wire_stateful_update_session(coord, info)
        coord.legion_system.comm_router.is_authorized_sender = AsyncMock(return_value=False)
        gate = DeliveryGate(coord)

        # from_user=False/from_minion_id set: _is_authorized() always returns True
        # for from_user=True (the user is always authorized), so an "unauthorized
        # sender" case must be a minion sender, not the make_comm default.
        comm = make_comm(
            "s1", content="hi", delivery=DeliveryPolicy.IMMEDIATE, comm_type=CommType.TASK,
            from_user=False, from_minion_id="sibling-a",
        )
        result = await gate.deliver(comm)

        assert result.success is True
        coord.send_message.assert_not_called()
        assert info.queue_paused is True
        assert _status_of(coord, "s1", comm.comm_id) == "pending"

    @pytest.mark.asyncio
    async def test_successful_halt_sets_queue_paused(self, tmp_path):
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False, queue_paused=False)
        coord = _make_coordinator(info, tmp_path)
        self._wire_stateful_update_session(coord, info)
        gate = DeliveryGate(coord)

        comm = make_comm("s1", content="halt now", delivery=DeliveryPolicy.HALT)
        result = await gate.deliver(comm)

        assert result.success is True
        assert info.queue_paused is True

    @pytest.mark.asyncio
    async def test_authorized_halt_with_non_resume_trigger_comm_type_still_takes_effect(self, tmp_path):
        """Found during review: HALT/PIVOT must bypass the comm_type-based implicit-
        resume check entirely — comm_type and the HALT/PIVOT delivery policy are
        independent fields on the public send_comm tool, so an authorized HALT sent
        with e.g. comm_type=REPORT must still interrupt, not silently queue."""
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False, queue_paused=True)
        coord = _make_coordinator(info, tmp_path)
        self._wire_stateful_update_session(coord, info)
        gate = DeliveryGate(coord)

        comm = make_comm(
            "s1", content="halt now", delivery=DeliveryPolicy.HALT, comm_type=CommType.REPORT,
        )
        result = await gate.deliver(comm)

        assert result.success is True
        coord.interrupt_session.assert_awaited_once()
        assert info.queue_paused is True
        assert _status_of(coord, "s1", comm.comm_id) == "delivered"

    @pytest.mark.asyncio
    async def test_when_idle_item_queued_while_paused_still_starts_drain_loop(self, tmp_path):
        """Found during review: a WHEN_IDLE comm that stays queued because it isn't
        an implicit-resume trigger must still start ensure_when_idle_running(), or
        the item is orphaned forever (nothing else would ever start the loop for it)."""
        info = _make_session_info(state=SessionState.ACTIVE, is_processing=False, queue_paused=True)
        coord = _make_coordinator(info, tmp_path)
        self._wire_stateful_update_session(coord, info)
        gate = DeliveryGate(coord)

        comm = make_comm(
            "s1", content="hi", delivery=DeliveryPolicy.WHEN_IDLE, comm_type=CommType.REPORT,
        )

        with patch("backend.delivery_gate.asyncio.sleep", new=AsyncMock(side_effect=_fast_sleep)):
            result = await gate.deliver(comm)
            assert result.success is True
            assert gate.is_when_idle_running("s1")
            assert _status_of(coord, "s1", comm.comm_id) == "pending"
            coord.send_message.assert_not_called()

            # Once the session unpauses, the already-running loop picks the item up.
            info.queue_paused = False
            for _ in range(50):
                await asyncio.sleep(0)
                if coord.send_message.await_count:
                    break

        coord.send_message.assert_awaited_once()
        assert _status_of(coord, "s1", comm.comm_id) == "delivered"
        gate.stop_when_idle("s1")
