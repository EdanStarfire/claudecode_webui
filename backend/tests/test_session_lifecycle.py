"""Tests for session_lifecycle module (epic #1990 stage 6a, issue #2159).

T1: the full transition table (parametrized over every SessionState x LifecycleEvent
pair) plus predicate unit tests — both pure, no I/O. A separate set of integration
tests confirms SessionManager's wrapper methods emit exactly one state_change per
real transition (not per no-op), and that ERROR_OCCURRED resolves a dangling pending
permission Future rather than merely being asserted to call something.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

import pytest

from backend.session_config import SessionConfig
from backend.session_lifecycle import (
    AUTO_START_STATES,
    RUNNABLE_STATES,
    STARTABLE_STATES,
    LifecycleEvent,
    SessionState,
    is_active,
    is_auto_startable,
    is_deliverable,
    is_errored,
    is_interruptible,
    is_live,
    is_not_terminated,
    is_paused,
    is_startable,
    is_terminal,
    transition,
    transition_processing,
)
from backend.session_manager import SessionManager

ALL_STATES = list(SessionState)
ALL_EVENTS = list(LifecycleEvent)

# Full transition table: (state, event) -> expected resulting state.
# PERMISSION_OPENED is exercised with open_permission_count=1 (the only value a
# real caller ever passes — pause_session() only calls it when a permission just
# became the first one open for the session); PERMISSION_CLOSED with
# open_permission_count=0 (resolve_permission_closed() only calls it once no
# request remains open). The computed-guard behavior itself (what happens at
# other counts) is covered by dedicated tests below.
EXPECTED_TRANSITIONS: dict[tuple[SessionState, LifecycleEvent], SessionState] = {}


def _fill_expected_transitions() -> None:
    for state in ALL_STATES:
        # START_REQUESTED
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.START_REQUESTED)] = (
            SessionState.STARTING if state in STARTABLE_STATES else state
        )
        # SDK_READY
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.SDK_READY)] = (
            SessionState.ACTIVE if state is SessionState.STARTING else state
        )
        # PERMISSION_OPENED (open_permission_count=1)
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.PERMISSION_OPENED)] = (
            SessionState.PAUSED if state is SessionState.ACTIVE else state
        )
        # PERMISSION_CLOSED (open_permission_count=0)
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.PERMISSION_CLOSED)] = (
            SessionState.ACTIVE if state is SessionState.PAUSED else state
        )
        # TERMINATE_REQUESTED
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.TERMINATE_REQUESTED)] = (
            SessionState.TERMINATED if state is SessionState.TERMINATED else SessionState.TERMINATING
        )
        # TERMINATED
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.TERMINATED)] = (
            SessionState.TERMINATED if state is SessionState.TERMINATING else state
        )
        # ERROR_OCCURRED — unconditional
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.ERROR_OCCURRED)] = SessionState.ERROR
        # RECOVERY_REQUESTED — unconditional, single-hop to TERMINATED
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.RECOVERY_REQUESTED)] = SessionState.TERMINATED
        # BOOT_NORMALIZED
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.BOOT_NORMALIZED)] = (
            SessionState.CREATED if state in RUNNABLE_STATES else state
        )
        # TURN_STARTED/TURN_ENDED don't affect SessionState, only is_processing.
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.TURN_STARTED)] = state
        EXPECTED_TRANSITIONS[(state, LifecycleEvent.TURN_ENDED)] = state


_fill_expected_transitions()


class TestTransitionTable:
    """T1: every (SessionState, LifecycleEvent) pair, pure function."""

    @pytest.mark.parametrize(
        "state,event",
        [(s, e) for s in ALL_STATES for e in ALL_EVENTS],
        ids=[f"{s.value}+{e.value}" for s in ALL_STATES for e in ALL_EVENTS],
    )
    def test_transition_table(self, state: SessionState, event: LifecycleEvent):
        expected = EXPECTED_TRANSITIONS[(state, event)]
        open_permission_count = 1 if event is LifecycleEvent.PERMISSION_OPENED else 0
        result = transition(state, event, open_permission_count=open_permission_count)
        assert result == expected, (
            f"transition({state}, {event}) = {result}, expected {expected}"
        )

    def test_every_state_and_event_covered(self):
        """Sanity check the table itself isn't missing a combination."""
        assert len(EXPECTED_TRANSITIONS) == len(ALL_STATES) * len(ALL_EVENTS)

    def test_permission_opened_requires_open_count(self):
        """PERMISSION_OPENED from ACTIVE only pauses when the count is actually > 0 —
        the guard isn't just "event fired," it's computed."""
        assert transition(
            SessionState.ACTIVE, LifecycleEvent.PERMISSION_OPENED, open_permission_count=0
        ) == SessionState.ACTIVE
        assert transition(
            SessionState.ACTIVE, LifecycleEvent.PERMISSION_OPENED, open_permission_count=1
        ) == SessionState.PAUSED

    def test_permission_closed_requires_zero_open_count(self):
        """PERMISSION_CLOSED from PAUSED only resumes when the count has reached
        zero — a sibling request still open must not resume the session."""
        assert transition(
            SessionState.PAUSED, LifecycleEvent.PERMISSION_CLOSED, open_permission_count=1
        ) == SessionState.PAUSED
        assert transition(
            SessionState.PAUSED, LifecycleEvent.PERMISSION_CLOSED, open_permission_count=0
        ) == SessionState.ACTIVE

    def test_unknown_event_is_a_noop(self):
        """transition() never raises for a (state, event) pair with no effect."""
        for state in ALL_STATES:
            for event in ALL_EVENTS:
                # Should never raise.
                transition(state, event)


class TestPredicates:
    """Predicate unit tests — is_startable/is_auto_startable/is_deliverable/is_interruptible."""

    def test_is_startable(self):
        for state in ALL_STATES:
            assert is_startable(state) == (state in STARTABLE_STATES)

    def test_is_auto_startable(self):
        for state in ALL_STATES:
            assert is_auto_startable(state) == (state in AUTO_START_STATES)

    def test_is_deliverable(self):
        expected = {SessionState.ACTIVE, SessionState.PAUSED}
        for state in ALL_STATES:
            assert is_deliverable(state) == (state in expected)

    def test_is_interruptible(self):
        expected = {SessionState.ACTIVE, SessionState.PAUSED}
        for state in ALL_STATES:
            assert is_interruptible(state) == (state in expected)

    def test_is_live(self):
        for state in ALL_STATES:
            assert is_live(state) == (state in RUNNABLE_STATES)

    def test_is_not_terminated(self):
        for state in ALL_STATES:
            assert is_not_terminated(state) == (state is not SessionState.TERMINATED)

    def test_is_active(self):
        for state in ALL_STATES:
            assert is_active(state) == (state is SessionState.ACTIVE)

    def test_is_paused(self):
        for state in ALL_STATES:
            assert is_paused(state) == (state is SessionState.PAUSED)

    def test_is_errored(self):
        for state in ALL_STATES:
            assert is_errored(state) == (state is SessionState.ERROR)

    def test_is_terminal(self):
        expected = {SessionState.TERMINATED, SessionState.ERROR}
        for state in ALL_STATES:
            assert is_terminal(state) == (state in expected)


class TestTransitionProcessing:
    """transition_processing() — parallel pure function for is_processing, driven
    by the same LifecycleEvent vocabulary as transition()."""

    def test_turn_started_sets_true(self):
        assert transition_processing(False, LifecycleEvent.TURN_STARTED) is True
        assert transition_processing(True, LifecycleEvent.TURN_STARTED) is True

    @pytest.mark.parametrize(
        "event",
        [
            LifecycleEvent.TURN_ENDED,
            LifecycleEvent.ERROR_OCCURRED,
            LifecycleEvent.RECOVERY_REQUESTED,
            LifecycleEvent.TERMINATED,
            LifecycleEvent.BOOT_NORMALIZED,
        ],
        ids=lambda e: e.value,
    )
    def test_terminal_events_set_false(self, event):
        assert transition_processing(True, event) is False
        assert transition_processing(False, event) is False

    @pytest.mark.parametrize(
        "event",
        [
            LifecycleEvent.START_REQUESTED,
            LifecycleEvent.SDK_READY,
            LifecycleEvent.PERMISSION_OPENED,
            LifecycleEvent.PERMISSION_CLOSED,
            LifecycleEvent.TERMINATE_REQUESTED,
        ],
        ids=lambda e: e.value,
    )
    def test_unrelated_events_are_noop(self, event):
        assert transition_processing(True, event) is True
        assert transition_processing(False, event) is False


# ---------------------------------------------------------------------------
# Single-emission integration tests (SessionManager wrapper methods)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_session_config():
    return SessionConfig(
        working_directory="/test/project",
        permission_mode="acceptEdits",
        system_prompt="Test system prompt",
        allowed_tools=["bash", "edit", "read"],
        model="claude-3-sonnet-20241022",
    )


@pytest.fixture
async def temp_session_manager():
    with tempfile.TemporaryDirectory() as temp_dir:
        manager = SessionManager(Path(temp_dir))
        await manager.initialize()
        yield manager


class TestSingleEmissionPerTransition:
    """Each SessionManager wrapper method fires exactly one state_change callback
    per actual transition — never zero (silently dropped) and never more than one
    (the redundant-emission bug this stage fixes)."""

    @pytest.mark.asyncio
    async def test_start_session_emits_once(self, temp_session_manager, sample_session_config):
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)

        emissions = []

        async def _capture(session_id, new_state, is_processing, **kwargs):
            emissions.append((session_id, new_state))

        manager.add_state_change_callback(_capture)

        result = await manager.start_session(sid)

        assert result is True
        assert emissions == [(sid, SessionState.STARTING)]

    @pytest.mark.asyncio
    async def test_mark_session_active_emits_once(self, temp_session_manager, sample_session_config):
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)
        await manager.start_session(sid)

        emissions = []

        async def _capture(session_id, new_state, is_processing, **kwargs):
            emissions.append((session_id, new_state))

        manager.add_state_change_callback(_capture)

        result = await manager.mark_session_active(sid)

        assert result is True
        assert emissions == [(sid, SessionState.ACTIVE)]

    @pytest.mark.asyncio
    async def test_pause_session_emits_once(self, temp_session_manager, sample_session_config):
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)
        await manager.start_session(sid)
        await manager.mark_session_active(sid)

        emissions = []

        async def _capture(session_id, new_state, is_processing, **kwargs):
            emissions.append((session_id, new_state))

        manager.add_state_change_callback(_capture)

        result = await manager.pause_session(sid)

        assert result is True
        assert emissions == [(sid, SessionState.PAUSED)]

    @pytest.mark.asyncio
    async def test_pause_session_noop_emits_nothing(self, temp_session_manager, sample_session_config):
        """Calling pause_session() on a non-ACTIVE session is a no-op — must not
        silently emit anyway."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)  # CREATED

        emissions = []

        async def _capture(session_id, new_state, is_processing, **kwargs):
            emissions.append((session_id, new_state))

        manager.add_state_change_callback(_capture)

        result = await manager.pause_session(sid)

        assert result is False
        assert emissions == []

    @pytest.mark.asyncio
    async def test_resolve_permission_closed_emits_once(self, temp_session_manager, sample_session_config):
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)
        await manager.start_session(sid)
        await manager.mark_session_active(sid)
        await manager.pause_session(sid)

        emissions = []

        async def _capture(session_id, new_state, is_processing, **kwargs):
            emissions.append((session_id, new_state))

        manager.add_state_change_callback(_capture)

        result = await manager.resolve_permission_closed(sid)

        assert result is True
        assert emissions == [(sid, SessionState.ACTIVE)]

    @pytest.mark.asyncio
    async def test_resolve_permission_closed_noop_emits_nothing(
        self, temp_session_manager, sample_session_config
    ):
        """Calling resolve_permission_closed() on a non-PAUSED session is a no-op."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)  # CREATED

        emissions = []

        async def _capture(session_id, new_state, is_processing, **kwargs):
            emissions.append((session_id, new_state))

        manager.add_state_change_callback(_capture)

        result = await manager.resolve_permission_closed(sid)

        assert result is True  # success, but a true no-op
        assert emissions == []

    @pytest.mark.asyncio
    async def test_terminate_session_emits_exactly_twice(
        self, temp_session_manager, sample_session_config
    ):
        """terminate_session() is TWO real transitions (TERMINATING, then
        TERMINATED) — exactly two emissions, not three (the old coordinator-level
        redundant third emission this stage removes) and not one (that would mean
        one of the two real transitions got silently dropped)."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)
        await manager.start_session(sid)
        await manager.mark_session_active(sid)

        emissions = []

        async def _capture(session_id, new_state, is_processing, **kwargs):
            emissions.append((session_id, new_state))

        manager.add_state_change_callback(_capture)

        result = await manager.terminate_session(sid)

        assert result is True
        assert emissions == [
            (sid, SessionState.TERMINATING),
            (sid, SessionState.TERMINATED),
        ]

    @pytest.mark.asyncio
    async def test_transition_to_error_mid_turn_emits_exactly_once(
        self, temp_session_manager, sample_session_config
    ):
        """Issue #2184 (AC1/AC5): ERROR_OCCURRED mid-turn is the exact bug this
        stage fixes — the old _transition_to_error() made two separate manager
        calls (update_session_state() then update_processing_state()), each firing
        its own state_change. transition_to_error() collapses the state AND the
        processing reset into one call, one emission."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)
        await manager.start_session(sid)
        await manager.mark_session_active(sid)
        await manager.start_turn(sid)

        emissions = []

        async def _capture(session_id, new_state, is_processing, **kwargs):
            emissions.append((session_id, new_state, is_processing))

        manager.add_state_change_callback(_capture)

        result = await manager.transition_to_error(sid, "simulated critical SDK error")

        assert result is True
        assert emissions == [(sid, SessionState.ERROR, False)]

        session_info = await manager.get_session_info(sid)
        assert session_info.state == SessionState.ERROR
        assert session_info.is_processing is False

    @pytest.mark.asyncio
    async def test_recover_session_from_active_emits_once(
        self, temp_session_manager, sample_session_config
    ):
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)
        await manager.start_session(sid)
        await manager.mark_session_active(sid)

        emissions = []

        async def _capture(session_id, new_state, is_processing, **kwargs):
            emissions.append((session_id, new_state))

        manager.add_state_change_callback(_capture)

        result = await manager.recover_session(sid)

        assert result is True
        assert emissions == [(sid, SessionState.TERMINATED)]

    @pytest.mark.asyncio
    async def test_recover_session_from_error_emits_once(
        self, temp_session_manager, sample_session_config
    ):
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)
        await manager.transition_to_error(sid, "boom")

        emissions = []

        async def _capture(session_id, new_state, is_processing, **kwargs):
            emissions.append((session_id, new_state))

        manager.add_state_change_callback(_capture)

        result = await manager.recover_session(sid)

        assert result is True
        assert emissions == [(sid, SessionState.TERMINATED)]

    @pytest.mark.asyncio
    async def test_lifecycle_record_fires_once_per_logical_action(
        self, temp_session_manager, sample_session_config
    ):
        """Issue #2184 (AC7): the lifecycle-record callback fires exactly once per
        logical action, matching the existing recorded-fixture vocabulary
        (start/restart/reset/pause/resume/terminate) — never per sub-transition
        (terminate_session()'s TERMINATING+TERMINATED pair must still yield ONE
        "terminate" record, not two)."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)

        actions = []

        async def _capture(session_id, action):
            actions.append(action)

        manager.add_lifecycle_record_callback(_capture)

        await manager.start_session(sid)  # default action="start"
        await manager.mark_session_active(sid)
        await manager.pause_session(sid)
        await manager.resolve_permission_closed(sid)
        await manager.terminate_session(sid)

        assert actions == ["start", "pause", "resume", "terminate"]

        # restart_session()/reset_session() pass an explicit action through
        # start_session(); recover_session() itself (their own preceding TERMINATED
        # bookkeeping step) must stay silent on this channel — otherwise reusing an
        # already-recorder-backed session on restart/reset would double-record.
        actions.clear()
        await manager.recover_session(sid)
        await manager.start_session(sid, action="restart")
        assert actions == ["restart"]

    @pytest.mark.asyncio
    async def test_metadata_setter_emits_metadata_change_not_state_change(
        self, temp_session_manager, sample_session_config
    ):
        """A metadata-only setter (update_session_name) fires the separate
        metadata-change callback, never state_change — the SessionState itself
        never changed."""
        manager = temp_session_manager
        sid = str(uuid.uuid4())
        await manager.create_session(sid, config=sample_session_config)

        state_changes = []
        metadata_changes = []

        async def _capture_state(session_id, new_state, is_processing, **kwargs):
            state_changes.append(session_id)

        async def _capture_metadata(session_id):
            metadata_changes.append(session_id)

        manager.add_state_change_callback(_capture_state)
        manager.add_metadata_change_callback(_capture_metadata)

        result = await manager.update_session_name(sid, "Renamed")

        assert result is True
        assert state_changes == []
        assert metadata_changes == [sid]


# ---------------------------------------------------------------------------
# Boot normalization (UC3 fix)
# ---------------------------------------------------------------------------


class TestBootNormalization:
    """Issue #2159 (UC3): PAUSED stays PAUSED at boot; RUNNABLE_STATES still reset
    to CREATED; ERROR stays ERROR. See test_session_manager.py for the
    tool-repair-scan-surfacing half of this; this file covers the state itself."""

    @pytest.mark.asyncio
    async def test_paused_stays_paused_across_restart(self, sample_session_config):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            manager1 = SessionManager(temp_path)
            await manager1.initialize()
            sid = str(uuid.uuid4())
            await manager1.create_session(sid, config=sample_session_config)
            manager1._active_sessions[sid].state = SessionState.PAUSED
            await manager1._persist_session_state(sid)

            manager2 = SessionManager(temp_path)
            await manager2.initialize()

            assert manager2._active_sessions[sid].state == SessionState.PAUSED

    @pytest.mark.asyncio
    async def test_active_resets_to_created_across_restart(self, sample_session_config):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            manager1 = SessionManager(temp_path)
            await manager1.initialize()
            sid = str(uuid.uuid4())
            await manager1.create_session(sid, config=sample_session_config)
            manager1._active_sessions[sid].state = SessionState.ACTIVE
            await manager1._persist_session_state(sid)

            manager2 = SessionManager(temp_path)
            await manager2.initialize()

            assert manager2._active_sessions[sid].state == SessionState.CREATED

    @pytest.mark.asyncio
    async def test_error_stays_error_across_restart(self, sample_session_config):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            manager1 = SessionManager(temp_path)
            await manager1.initialize()
            sid = str(uuid.uuid4())
            await manager1.create_session(sid, config=sample_session_config)
            manager1._active_sessions[sid].state = SessionState.ERROR
            await manager1._persist_session_state(sid)

            manager2 = SessionManager(temp_path)
            await manager2.initialize()

            assert manager2._active_sessions[sid].state == SessionState.ERROR
            assert sid in manager2.get_sessions_needing_tool_repair_scan()


# ---------------------------------------------------------------------------
# ERROR_OCCURRED denies/resolves the dangling permission Future
# ---------------------------------------------------------------------------


class TestErrorOccurredResolvesDanglingPermission:
    """Issue #2159: a critical SDK error must resolve/deny this session's own
    dangling pending permission Future(s) — proven by actually awaiting the
    Future and observing it resolved, not merely asserting a method was called.
    """

    @pytest.mark.asyncio
    async def test_dangling_future_actually_resolves_on_error(self):
        from backend.permission_service import PermissionService
        from backend.session_coordinator import SessionCoordinator

        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = SessionCoordinator(Path(temp_dir))
            await coordinator.session_manager.initialize()
            permission_service = PermissionService(coordinator, session_queues={})
            coordinator.set_permission_service(permission_service)

            sid = str(uuid.uuid4())
            await coordinator.session_manager.create_session(sid, config=SessionConfig())
            await coordinator.session_manager.start_session(sid)
            await coordinator.session_manager.mark_session_active(sid)
            await coordinator.session_manager.pause_session(sid)

            # Simulate a pending permission request Future the real SDK would
            # otherwise never resolve once it has died.
            future = asyncio.get_running_loop().create_future()
            permission_service.pending_permissions["req-dangling"] = future
            permission_service.pending_by_session[sid] = {"req-dangling"}

            assert not future.done()

            await coordinator._transition_to_error(sid, "simulated critical SDK error")

            # The actual Future is resolved — not just "some method was called."
            assert future.done()
            assert future.result()["behavior"] == "deny"
            assert sid not in permission_service.pending_by_session

            session_info = await coordinator.session_manager.get_session_info(sid)
            assert session_info.state == SessionState.ERROR

    @pytest.mark.asyncio
    async def test_error_occurred_does_not_resume_to_active(self):
        """Denying the dangling permission must not accidentally resume the
        session to ACTIVE — it's now ERROR, not PAUSED, so the resume guard
        inside _maybe_resume_session must see that and do nothing."""
        from backend.permission_service import PermissionService
        from backend.session_coordinator import SessionCoordinator

        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = SessionCoordinator(Path(temp_dir))
            await coordinator.session_manager.initialize()
            permission_service = PermissionService(coordinator, session_queues={})
            coordinator.set_permission_service(permission_service)

            sid = str(uuid.uuid4())
            await coordinator.session_manager.create_session(sid, config=SessionConfig())
            await coordinator.session_manager.start_session(sid)
            await coordinator.session_manager.mark_session_active(sid)
            await coordinator.session_manager.pause_session(sid)

            future = asyncio.get_running_loop().create_future()
            permission_service.pending_permissions["req-dangling"] = future
            permission_service.pending_by_session[sid] = {"req-dangling"}

            await coordinator._transition_to_error(sid, "simulated critical SDK error")

            session_info = await coordinator.session_manager.get_session_info(sid)
            assert session_info.state == SessionState.ERROR, (
                "ERROR_OCCURRED's dangling-permission cleanup must not resume the "
                "session back to ACTIVE"
            )
