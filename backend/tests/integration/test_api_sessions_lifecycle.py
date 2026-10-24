"""
Stage 2b: Integration tests for Session Lifecycle & Messages endpoints.

Tests:
- POST /api/sessions/{session_id}/start — start session (SDK init)
- POST /api/sessions/{session_id}/pause — pause session
- POST /api/sessions/{session_id}/terminate — terminate session
- POST /api/sessions/{session_id}/restart — restart (disconnect + resume)
- POST /api/sessions/{session_id}/reset — reset (clear + fresh start)
- POST /api/sessions/{session_id}/disconnect — disconnect SDK
- POST /api/sessions/{session_id}/permission-mode — set permission mode
- POST /api/sessions/{session_id}/messages — send message
- GET /api/sessions/{session_id}/messages — get messages (paginated)
"""

import asyncio


async def _create_named_session(env, name):
    """Helper to create a session with a specific fixture name."""
    project = await env["create_test_project"]("Lifecycle Project")
    session = await env["create_test_session"](
        project["project_id"], name
    )
    return session


async def _wait_for_state(client, sid, target_state, timeout=10):
    """Poll until session reaches target state."""
    for _ in range(int(timeout / 0.2)):
        resp = await client.get(f"/api/sessions/{sid}")
        if resp.status_code == 200:
            state = resp.json()["session"]["state"]
            if state == target_state:
                return True
        await asyncio.sleep(0.2)
    return False


class TestStartSession:
    async def test_start_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Session should be in CREATED state
        resp = await client.get(f"/api/sessions/{sid}")
        assert resp.json()["session"]["state"] == "created"

        # Start the session
        resp = await client.post(f"/api/sessions/{sid}/start")
        assert resp.status_code == 200

        # Wait for ACTIVE state
        reached = await _wait_for_state(client, sid, "active")
        assert reached, "Session did not become active"


class TestTerminateSession:
    async def test_terminate_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start then terminate
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(f"/api/sessions/{sid}/terminate")
        assert resp.status_code == 200

        reached = await _wait_for_state(client, sid, "terminated")
        assert reached, "Session did not terminate"


class TestDisconnectSession:
    async def test_disconnect_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start then disconnect
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(f"/api/sessions/{sid}/disconnect")
        assert resp.status_code == 200


class TestPermissionMode:
    async def test_set_permission_mode_on_created_session(self, api_integration_env):
        """Setting permission mode on a non-active (created) session persists the mode."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/permission-mode",
            json={"mode": "bypassPermissions"},
        )
        # Non-running sessions can have their permission mode changed (persisted to disk)
        assert resp.status_code == 200
        # Verify the mode is reflected in the session state
        session_resp = await client.get(f"/api/sessions/{sid}")
        assert session_resp.status_code == 200
        assert session_resp.json()["session"]["current_permission_mode"] == "bypassPermissions"

    async def test_set_permission_mode_invalid(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/permission-mode",
            json={"mode": "invalid_mode"},
        )
        assert resp.status_code == 400


class TestModel:
    async def test_set_model_on_created_session(self, api_integration_env):
        """Setting model on a non-active (created) session persists the model."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/model",
            json={"model": "opus"},
        )
        # Non-running sessions can have their model changed (persisted to disk)
        assert resp.status_code == 200
        # Verify the model is reflected in the session state
        session_resp = await client.get(f"/api/sessions/{sid}")
        assert session_resp.status_code == 200
        assert session_resp.json()["session"]["current_model"] == "opus"

    async def test_set_model_on_active_session(self, api_integration_env):
        """Setting model on an active session live-switches without restarting it."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(
            f"/api/sessions/{sid}/model",
            json={"model": "opus"},
        )
        assert resp.status_code == 200

        session_resp = await client.get(f"/api/sessions/{sid}")
        assert session_resp.status_code == 200
        assert session_resp.json()["session"]["current_model"] == "opus"
        # Session should remain active — no restart triggered by a live model switch
        assert session_resp.json()["session"]["state"] == "active"

    async def test_set_model_arbitrary_on_created_session(self, api_integration_env):
        """Setting an arbitrary (non-alias) model on a created session is accepted (issue #1919)."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/model",
            json={"model": "claude-sonnet-4-5-20250929"},
        )
        assert resp.status_code == 200

        session_resp = await client.get(f"/api/sessions/{sid}")
        assert session_resp.status_code == 200
        assert session_resp.json()["session"]["current_model"] == "claude-sonnet-4-5-20250929"

    async def test_set_model_arbitrary_on_active_session(self, api_integration_env):
        """Setting an arbitrary (non-alias) model on an active session is accepted (issue #1919)."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(
            f"/api/sessions/{sid}/model",
            json={"model": "claude-sonnet-4-5-20250929"},
        )
        assert resp.status_code == 200

        session_resp = await client.get(f"/api/sessions/{sid}")
        assert session_resp.status_code == 200
        assert session_resp.json()["session"]["current_model"] == "claude-sonnet-4-5-20250929"

    async def test_set_model_empty_rejected(self, api_integration_env):
        """Empty model is rejected with 422 — raised by ModelRequest's field_validator during
        request parsing, before the route body runs, unlike the 400s elsewhere in this file."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/model",
            json={"model": ""},
        )
        assert resp.status_code == 422


class TestSendMessage:
    async def test_send_message_to_active_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(
            f"/api/sessions/{sid}/messages",
            json={"message": "Hello, how are you?"},
        )
        assert resp.status_code == 200

        # Wait briefly for message processing
        await asyncio.sleep(1)

    async def test_send_message_to_paused_session_is_accepted(self, api_integration_env):
        """Issue #1746 (stage: permissions): a session PAUSED for a pending permission
        decision must still accept a new message — it gets queued for delivery once the
        current turn resolves, same as an ordinary busy/is_processing session already does.
        Only genuinely non-running states (created/starting/terminating/terminated/error)
        should reject with 409."""
        client = api_integration_env["client"]
        coordinator = api_integration_env["coordinator"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        paused = await coordinator.session_manager.pause_session(sid)
        assert paused, "Test setup failed: could not pause session"
        await _wait_for_state(client, sid, "paused")

        resp = await client.post(
            f"/api/sessions/{sid}/messages",
            json={"message": "Sent while a permission is pending"},
        )
        assert resp.status_code == 200


class TestAutoResumeCancellationOnManualSend:
    """Issue #1572 decision #4: a manual user send through the REST message
    endpoint cancels any pending auto-resume schedule for that session — the
    user has already taken over, so a later "continue" nudge would be a
    duplicate. Scoped to this REST-driven path only; other delivery paths
    (queue processor, other schedules) must not cancel it."""

    async def test_manual_send_cancels_pending_auto_resume(self, api_integration_env):
        client = api_integration_env["client"]
        coordinator = api_integration_env["coordinator"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        scheduler = coordinator.legion_system.scheduler_service
        schedule = await scheduler.upsert_auto_resume_schedule(
            legion_id=session["project_id"],
            session_id=sid,
            session_name=session["name"],
            fire_at=9999999999.0,
        )
        # Issue #2190: simulate the queue-hold detection would have set alongside
        # this schedule — the manual send below must clear it too, since
        # cancel_auto_resume_schedule() returns True here (a pending schedule
        # actually existed).
        await coordinator.pause_queue(sid, True)

        resp = await client.post(
            f"/api/sessions/{sid}/messages",
            json={"message": "I'm back, taking over"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        remaining = await scheduler.get_schedule(schedule.schedule_id)
        assert remaining is None

        info = await coordinator.session_manager.get_session_info(sid)
        assert info.queue_paused is False

    async def test_manual_send_without_pending_auto_resume_does_not_clobber_manual_pause(
        self, api_integration_env
    ):
        """Issue #2190: the unpause-on-cancel must be scoped to exactly the case
        where cancel_auto_resume_schedule() actually cancelled something. A queue
        the user paused manually for unrelated reasons (no pending auto_resume
        schedule to cancel) must survive an ordinary manual send untouched."""
        client = api_integration_env["client"]
        coordinator = api_integration_env["coordinator"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        await coordinator.pause_queue(sid, True)

        resp = await client.post(
            f"/api/sessions/{sid}/messages",
            json={"message": "an ordinary message, no auto-resume involved"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        info = await coordinator.session_manager.get_session_info(sid)
        assert info.queue_paused is True, (
            "a manual send with no pending auto-resume schedule must not clear a "
            "queue pause the user set for unrelated reasons"
        )

    async def test_non_rest_delivery_path_does_not_cancel_auto_resume(self, api_integration_env):
        """coordinator.send_message() is also the delivery primitive used by the
        queue processor / comm router / MCP tool sends — none of those paths go
        through the REST router's cancellation call, so a pending auto-resume
        survives a non-REST send."""
        api_integration_env["client"]
        coordinator = api_integration_env["coordinator"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        await coordinator.start_session(sid)
        for _ in range(50):
            info = await coordinator.session_manager.get_session_info(sid)
            if info.state.value == "active":
                break
            import asyncio
            await asyncio.sleep(0.1)

        scheduler = coordinator.legion_system.scheduler_service
        schedule = await scheduler.upsert_auto_resume_schedule(
            legion_id=session["project_id"],
            session_id=sid,
            session_name=session["name"],
            fire_at=9999999999.0,
        )

        success = await coordinator.send_message(sid, "delivered via a non-REST path")
        assert success is True

        remaining = await scheduler.get_schedule(schedule.schedule_id)
        assert remaining is not None, "non-REST send_message() must not cancel the auto-resume"


class TestIssue2190RateLimitResultFixtureReplay:
    """AC4/T1: replay the real production usage-limit signal shape — a synthetic
    AssistantMessage (model '<synthetic>') immediately followed by an ordinary
    (non-exception) ResultMessage with is_error + api_error_status == 429 — through
    the real pipeline via the committed raw fixture
    backend/tests/fixtures/raw/rate_limit_result/. Proves end-to-end that detection
    fires for this exact signal shape, not just via a hand-constructed dict in
    test_session_coordinator.py's unit-level tests."""

    async def test_replay_schedules_auto_resume_at_parsed_reset_time_and_pauses_queue(
        self, api_integration_env
    ):
        from datetime import UTC, datetime

        from backend.timestamp_utils import parse_rate_limit_reset_time

        client = api_integration_env["client"]
        coordinator = api_integration_env["coordinator"]
        session = await _create_named_session(api_integration_env, "raw/rate_limit_result")
        sid = session["session_id"]

        # The fixture's own recorded "timestamp" field is decorative only —
        # MessageRecord.from_sdk_message() stamps real wall-clock time.time() on
        # replay (same as live production), so the reference for recomputing the
        # expected fire_at is "now", not the fixture's authored timestamp.
        before = datetime.now(UTC).timestamp()
        resp = await client.post(f"/api/sessions/{sid}/start")
        assert resp.status_code == 200, f"start session failed: {resp.text}"
        after = datetime.now(UTC).timestamp()

        # Option B: this signal must never force an ERROR transition.
        session_info = await coordinator.session_manager.get_session_info(sid)
        assert session_info.state.value != "error"
        assert session_info.queue_paused is True

        scheduler = coordinator.legion_system.scheduler_service
        schedules = await scheduler.list_schedules(
            legion_id=session_info.project_id, minion_id=sid
        )
        auto_resume_schedules = [s for s in schedules if s.schedule_type == "auto_resume"]
        assert len(auto_resume_schedules) == 1
        schedule = auto_resume_schedules[0]
        assert schedule.fire_at_source == "synthetic_text"

        # Independently recompute the expected fire_at from the fixture's own
        # recorded synthetic text, using the real wall-clock window the replay ran
        # in as the reference — same resolver the production code calls — rather
        # than hardcoding an absolute number.
        parsed = parse_rate_limit_reset_time(
            "You've hit your session limit · resets 6:10am (UTC)", before
        )
        assert parsed is not None
        expected_fire_at = parsed + 300  # AUTO_RESUME_GRACE_PERIOD_SECONDS
        assert abs(schedule.next_run - expected_fire_at) < max(after - before, 1.0) + 2.0


class TestGetMessages:
    async def test_get_messages_empty(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/messages")
        assert resp.status_code == 200
        body = resp.json()
        assert "messages" in body

    async def test_get_messages_with_pagination(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session to generate some messages
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")
        await asyncio.sleep(0.5)

        # Get with limit
        resp = await client.get(f"/api/sessions/{sid}/messages?limit=2&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert "messages" in body
        assert "total" in body or "pagination" in body or len(body["messages"]) <= 2

    async def test_get_messages_offset(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session to generate messages
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")
        await asyncio.sleep(0.5)

        # Get all messages first
        resp_all = await client.get(f"/api/sessions/{sid}/messages?limit=100&offset=0")
        all_msgs = resp_all.json()["messages"]

        if len(all_msgs) > 1:
            # Get with offset
            resp_offset = await client.get(f"/api/sessions/{sid}/messages?limit=100&offset=1")
            offset_msgs = resp_offset.json()["messages"]
            assert len(offset_msgs) == len(all_msgs) - 1


class TestRestartSession:
    async def test_restart_active_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(f"/api/sessions/{sid}/restart")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_restart_created_session(self, api_integration_env):
        """Restart on a non-active session."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(f"/api/sessions/{sid}/restart")
        # Issue #2293: CREATED is deliberately not restartable (Start is the right
        # action), so it gets a specific 409.
        assert resp.status_code == 409


class TestResetSession:
    async def test_reset_active_session(self, api_integration_env):
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        # Start session
        await client.post(f"/api/sessions/{sid}/start")
        await _wait_for_state(client, sid, "active")

        resp = await client.post(f"/api/sessions/{sid}/reset")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_reset_created_session(self, api_integration_env):
        """Reset on a non-active session."""
        client = api_integration_env["client"]
        session = await _create_named_session(api_integration_env, "single_turn")
        sid = session["session_id"]

        resp = await client.post(f"/api/sessions/{sid}/reset")
        assert resp.status_code in (200, 400, 500)
