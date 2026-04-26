"""
Unit and integration tests for SessionWatchdogService (issues #1130 + #1131).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.config_manager import (
    AppConfig,
    ErrorRateWatchdogConfig,
    IdleWatchdogConfig,
    WatchdogConfig,
)
from src.session_watchdog import SessionWatchdogService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    enabled: bool = True,
    idle_enabled: bool = True,
    idle_timeout: int = 300,
    error_enabled: bool = True,
    min_calls: int = 10,
    threshold: float = 0.6,
    poll_interval: int = 60,
) -> AppConfig:
    return AppConfig(
        watchdog=WatchdogConfig(
            enabled=enabled,
            poll_interval_seconds=poll_interval,
            idle=IdleWatchdogConfig(enabled=idle_enabled, timeout_seconds=idle_timeout),
            error_rate=ErrorRateWatchdogConfig(
                enabled=error_enabled, min_calls=min_calls, threshold=threshold
            ),
        )
    )


def _make_session(
    session_id: str = "sess-1",
    state_value: str = "active",
    last_activity_at: datetime | None = None,
    template_id: str | None = None,
    project_id: str | None = None,
    name: str = "Test Session",
):
    from src.session_manager import SessionState

    session = MagicMock()
    session.session_id = session_id
    session.state = SessionState(state_value)
    session.last_activity_at = last_activity_at
    session.template_id = template_id
    session.project_id = project_id
    session.name = name
    return session


def _make_watchdog(config: AppConfig | None = None) -> tuple[SessionWatchdogService, list]:
    if config is None:
        config = _make_config()

    session_manager = MagicMock()
    template_manager = MagicMock()
    template_manager.templates = {}

    alerts: list[dict] = []

    class _FakeQueue:
        def append(self, event):
            alerts.append(event)

    watchdog = SessionWatchdogService(
        session_manager=session_manager,
        template_manager=template_manager,
        app_config=config,
        ui_queue=_FakeQueue(),
    )
    return watchdog, alerts


# ---------------------------------------------------------------------------
# Idle watchdog tests
# ---------------------------------------------------------------------------

class TestIdleAlertFires:
    @pytest.mark.asyncio
    async def test_idle_alert_fires_after_timeout(self):
        """Alert fires when session is ACTIVE and idle beyond timeout."""
        watchdog, alerts = _make_watchdog(_make_config(idle_timeout=300))
        session = _make_session(
            last_activity_at=datetime.now(UTC) - timedelta(seconds=400),
        )
        await watchdog._evaluate_session(session)
        assert len(alerts) == 1
        assert alerts[0]["watchdog"] == "idle"
        assert alerts[0]["details"]["idle_seconds"] > 300

    @pytest.mark.asyncio
    async def test_idle_alert_not_fired_before_timeout(self):
        """Alert does NOT fire when idle time is below threshold."""
        watchdog, alerts = _make_watchdog(_make_config(idle_timeout=300))
        session = _make_session(
            last_activity_at=datetime.now(UTC) - timedelta(seconds=100),
        )
        await watchdog._evaluate_session(session)
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_idle_alert_silenced_when_paused(self):
        """PAUSED sessions are exempt from idle alerts."""
        watchdog, alerts = _make_watchdog(_make_config(idle_timeout=10))
        session = _make_session(
            state_value="paused",
            last_activity_at=datetime.now(UTC) - timedelta(seconds=600),
        )
        await watchdog._evaluate_session(session)
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_idle_alert_silenced_when_disabled(self):
        """No alert if idle watchdog is disabled."""
        watchdog, alerts = _make_watchdog(_make_config(idle_enabled=False))
        session = _make_session(
            last_activity_at=datetime.now(UTC) - timedelta(seconds=600),
        )
        await watchdog._evaluate_session(session)
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_idle_alert_no_fire_when_no_activity_yet(self):
        """If last_activity_at is None, idle alert does not fire (no baseline)."""
        watchdog, alerts = _make_watchdog(_make_config(idle_timeout=1))
        session = _make_session(last_activity_at=None)
        await watchdog._evaluate_session(session)
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_idle_re_arms_after_activity(self):
        """Alert re-arms after activity resumes and condition clears."""
        watchdog, alerts = _make_watchdog(_make_config(idle_timeout=300))
        session_id = "sess-rearm"

        # First episode: fire alert
        session = _make_session(
            session_id=session_id,
            last_activity_at=datetime.now(UTC) - timedelta(seconds=400),
        )
        await watchdog._evaluate_session(session)
        assert len(alerts) == 1
        assert watchdog._alert_states[session_id].idle_alerted is True

        # Activity resumes — re-arm on next tick
        session.last_activity_at = datetime.now(UTC)
        await watchdog._evaluate_session(session)
        assert watchdog._alert_states[session_id].idle_alerted is False

        # Goes idle again — second alert fires
        session.last_activity_at = datetime.now(UTC) - timedelta(seconds=400)
        await watchdog._evaluate_session(session)
        assert len(alerts) == 2


# ---------------------------------------------------------------------------
# Error-rate watchdog tests
# ---------------------------------------------------------------------------

class TestErrorRateAlert:
    @pytest.mark.asyncio
    async def test_error_rate_under_min_calls_no_fire(self):
        """No alert when fewer calls than min_calls, even if all failed."""
        watchdog, alerts = _make_watchdog(_make_config(min_calls=10, threshold=0.6))
        for i in range(5):
            watchdog.record_tool_outcome("sess-1", f"t{i}", "failed")
        session = _make_session()
        await watchdog._evaluate_session(session)
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_error_rate_fires_at_threshold(self):
        """Alert fires when 6 of 10 tool calls are failed/denied."""
        watchdog, alerts = _make_watchdog(_make_config(min_calls=10, threshold=0.6))
        for i in range(6):
            watchdog.record_tool_outcome("sess-1", f"fail-{i}", "failed")
        for i in range(4):
            watchdog.record_tool_outcome("sess-1", f"ok-{i}", "completed")
        session = _make_session()
        await watchdog._evaluate_session(session)
        assert len(alerts) == 1
        assert alerts[0]["watchdog"] == "error_rate"
        assert alerts[0]["details"]["error_rate"] == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_error_rate_denied_counts_as_error(self):
        """DENIED outcomes count toward error total."""
        watchdog, alerts = _make_watchdog(_make_config(min_calls=10, threshold=0.6))
        for i in range(3):
            watchdog.record_tool_outcome("sess-1", f"denied-{i}", "denied")
        for i in range(3):
            watchdog.record_tool_outcome("sess-1", f"failed-{i}", "failed")
        for i in range(4):
            watchdog.record_tool_outcome("sess-1", f"ok-{i}", "completed")
        session = _make_session()
        await watchdog._evaluate_session(session)
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_error_rate_skips_interrupted(self):
        """INTERRUPTED outcomes are not counted at all."""
        watchdog, alerts = _make_watchdog(_make_config(min_calls=10, threshold=0.6))
        # 10 interrupted calls — none counted, deque stays empty
        for i in range(10):
            watchdog.record_tool_outcome("sess-1", f"int-{i}", "interrupted")
        session = _make_session()
        await watchdog._evaluate_session(session)
        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_error_rate_skips_interrupted_in_denominator(self):
        """Interrupted calls don't inflate denominator, only real calls count."""
        watchdog, alerts = _make_watchdog(_make_config(min_calls=5, threshold=0.6))
        # 5 real failures + 100 interrupted → only 5 in deque, below min_calls=5 exactly
        # So 5 failed / 5 total = 1.0 ≥ 0.6 → alert fires
        for i in range(5):
            watchdog.record_tool_outcome("sess-1", f"fail-{i}", "failed")
        for i in range(100):
            watchdog.record_tool_outcome("sess-1", f"int-{i}", "interrupted")
        session = _make_session()
        await watchdog._evaluate_session(session)
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_error_rate_re_arms_after_recovery(self):
        """Alert re-arms after enough successful calls push ratio below threshold."""
        watchdog, alerts = _make_watchdog(_make_config(min_calls=10, threshold=0.6))
        session_id = "sess-recover"

        # Fill deque: 8 failures, 2 successes — fires alert
        for i in range(8):
            watchdog.record_tool_outcome(session_id, f"fail-{i}", "failed")
        for i in range(2):
            watchdog.record_tool_outcome(session_id, f"ok-{i}", "completed")
        session = _make_session(session_id=session_id)
        await watchdog._evaluate_session(session)
        assert len(alerts) == 1

        # Push 8 more successes — deque rotates, ratio drops below 0.6
        for i in range(10, 18):
            watchdog.record_tool_outcome(session_id, f"ok2-{i}", "completed")
        await watchdog._evaluate_session(session)
        assert watchdog._alert_states[session_id].error_rate_alerted is False

        # More failures → re-fires
        for i in range(100, 106):
            watchdog.record_tool_outcome(session_id, f"fail2-{i}", "failed")
        for i in range(200, 204):
            watchdog.record_tool_outcome(session_id, f"ok3-{i}", "completed")
        await watchdog._evaluate_session(session)
        assert len(alerts) == 2

    @pytest.mark.asyncio
    async def test_error_rate_disabled(self):
        """No alert when error-rate watchdog is disabled."""
        watchdog, alerts = _make_watchdog(_make_config(error_enabled=False))
        for i in range(10):
            watchdog.record_tool_outcome("sess-1", f"fail-{i}", "failed")
        session = _make_session()
        await watchdog._evaluate_session(session)
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# Per-session isolation
# ---------------------------------------------------------------------------

class TestPerSessionIsolation:
    @pytest.mark.asyncio
    async def test_per_session_isolation(self):
        """Two sessions are evaluated independently — one alerts, other does not."""
        watchdog, alerts = _make_watchdog(_make_config(min_calls=10, threshold=0.6))

        # sess-a: 10 failures → should alert
        for i in range(10):
            watchdog.record_tool_outcome("sess-a", f"fail-{i}", "failed")

        # sess-b: all successes → should not alert
        for i in range(10):
            watchdog.record_tool_outcome("sess-b", f"ok-{i}", "completed")

        session_a = _make_session(session_id="sess-a")
        session_b = _make_session(session_id="sess-b")
        await watchdog._evaluate_session(session_a)
        await watchdog._evaluate_session(session_b)

        assert len(alerts) == 1
        assert alerts[0]["session_id"] == "sess-a"


# ---------------------------------------------------------------------------
# Reset / lifecycle
# ---------------------------------------------------------------------------

class TestResetClearsState:
    @pytest.mark.asyncio
    async def test_reset_clears_deque_and_alert_state(self):
        """reset_session clears deque and WatchdogAlertState."""
        watchdog, alerts = _make_watchdog(_make_config(min_calls=10, threshold=0.6))
        session_id = "sess-reset"

        for i in range(10):
            watchdog.record_tool_outcome(session_id, f"fail-{i}", "failed")
        session = _make_session(session_id=session_id)
        await watchdog._evaluate_session(session)
        assert len(alerts) == 1
        assert session_id in watchdog._tool_outcomes
        assert session_id in watchdog._alert_states

        # Reset
        watchdog.reset_session(session_id)
        assert session_id not in watchdog._tool_outcomes
        assert session_id not in watchdog._alert_states
        assert session_id not in watchdog._recorded_tool_ids

    def test_reset_idempotent(self):
        """reset_session is safe to call for unknown sessions."""
        watchdog, _ = _make_watchdog()
        watchdog.reset_session("unknown-session")  # must not raise


# ---------------------------------------------------------------------------
# Config merge
# ---------------------------------------------------------------------------

class TestConfigResolution:
    def test_template_config_overrides_global(self):
        """Template watchdog block wins over AppConfig defaults."""
        config = _make_config(enabled=False)  # global OFF
        watchdog, _ = _make_watchdog(config)

        template = MagicMock()
        template.watchdog = {
            "enabled": True,
            "idle": {"enabled": True, "timeout_seconds": 60},
        }
        watchdog._template_manager.templates = {"tmpl-1": template}

        session = _make_session(template_id="tmpl-1")
        resolved = watchdog._resolve_config(session)

        assert resolved["enabled"] is True
        assert resolved["idle"]["enabled"] is True
        assert resolved["idle"]["timeout_seconds"] == 60

    def test_master_switch_disables_both(self):
        """watchdog.enabled=False disables both idle and error_rate regardless of children."""
        config = _make_config(enabled=False, idle_enabled=True, error_enabled=True)
        watchdog, _ = _make_watchdog(config)

        session = _make_session()
        resolved = watchdog._resolve_config(session)

        assert resolved["enabled"] is False
        assert resolved["idle"]["enabled"] is False
        assert resolved["error_rate"]["enabled"] is False

    def test_no_template_uses_global(self):
        """Session without template uses global AppConfig."""
        config = _make_config(idle_timeout=999)
        watchdog, _ = _make_watchdog(config)
        session = _make_session()
        resolved = watchdog._resolve_config(session)
        assert resolved["idle"]["timeout_seconds"] == 999


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_duplicate_tool_id_not_double_counted(self):
        """Same tool_use_id is only recorded once."""
        watchdog, _ = _make_watchdog()
        watchdog.record_tool_outcome("sess-1", "tool-abc", "failed")
        watchdog.record_tool_outcome("sess-1", "tool-abc", "failed")
        outcomes = watchdog._tool_outcomes.get("sess-1")
        assert outcomes is not None
        assert len(outcomes) == 1


# ---------------------------------------------------------------------------
# Integration: activity hooks update last_activity_at
# ---------------------------------------------------------------------------

class TestLastActivityAt:
    @pytest.mark.asyncio
    async def test_record_activity_updates_in_memory(self):
        """SessionManager.record_activity updates last_activity_at in memory."""
        import tempfile
        from pathlib import Path

        from src.session_config import SessionConfig
        from src.session_manager import SessionManager

        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = SessionManager(Path(tmpdir))
            await mgr.initialize()

            cfg = SessionConfig(working_directory=str(tmpdir), permission_mode="acceptEdits")
            session_id = "test-activity-session"
            await mgr.create_session(session_id=session_id, config=cfg, name="Test")

            session = await mgr.get_session_info(session_id)
            assert session.last_activity_at is None

            before = datetime.now(UTC)
            mgr.record_activity(session_id)
            after = datetime.now(UTC)

            session = await mgr.get_session_info(session_id)
            assert session.last_activity_at is not None
            assert before <= session.last_activity_at <= after


# ---------------------------------------------------------------------------
# Integration: alert event appears on UI queue
# ---------------------------------------------------------------------------

class TestAlertEventOnUIQueue:
    @pytest.mark.asyncio
    async def test_alert_event_appears_on_ui_poll(self):
        """Firing a watchdog alert pushes event to the ui_queue."""
        watchdog, alerts = _make_watchdog(_make_config(idle_timeout=10))
        session = _make_session(
            last_activity_at=datetime.now(UTC) - timedelta(seconds=60),
        )
        await watchdog._evaluate_session(session)
        assert len(alerts) == 1
        ev = alerts[0]
        assert ev["type"] == "session_watchdog_alert"
        assert ev["session_id"] == session.session_id
        assert ev["watchdog"] == "idle"
        assert "fired_at" in ev
