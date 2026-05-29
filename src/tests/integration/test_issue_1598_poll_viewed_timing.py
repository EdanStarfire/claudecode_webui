"""
Issue #1598: Integration tests for poll_session() viewed-timestamp timing.

The fix moves mark_viewed() to BEFORE wait_for_events() so that any completion
arriving during the long-poll window is timestamped after last_viewed_at and
correctly surfaces as unread when the user navigates away.

Tests:
1. mark_viewed() is called before wait_for_events() (call-order guarantee)
2. Completion arriving during the poll window stays unread after the poll returns
3. Session with no prior completion: mark_viewed is a no-op (existing behavior)
"""

from datetime import UTC, datetime


class TestPollViewedTiming:
    async def test_mark_viewed_called_before_wait_for_events(self, api_integration_env):
        """Issue #1598: mark_viewed() must be called BEFORE wait_for_events()."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        call_order = []

        original_mark_viewed = webui.coordinator.session_manager.mark_viewed
        queue = webui.session_queues[sid]
        original_wait = queue.wait_for_events

        async def patched_mark_viewed(session_id):
            call_order.append("mark_viewed")
            return await original_mark_viewed(session_id)

        async def patched_wait(since, timeout):
            call_order.append("wait_for_events")
            return await original_wait(since, timeout)

        webui.coordinator.session_manager.mark_viewed = patched_mark_viewed
        queue.wait_for_events = patched_wait
        try:
            resp = await client.get(f"/api/poll/session/{sid}?since=0&timeout=0")
            assert resp.status_code == 200
            assert len(call_order) >= 2, f"Expected both calls, got: {call_order}"
            assert call_order[0] == "mark_viewed", (
                f"mark_viewed must be first, got order: {call_order}"
            )
            assert call_order[1] == "wait_for_events"
        finally:
            webui.coordinator.session_manager.mark_viewed = original_mark_viewed
            queue.wait_for_events = original_wait

    async def test_completion_during_poll_window_stays_unread(self, api_integration_env):
        """Issue #1598: Completion arriving after mark_viewed surfaces as unread."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        # Simulate: completion arrives AFTER mark_viewed runs but BEFORE wait returns.
        # Patch wait_for_events to inject the completion mid-poll.
        queue = webui.session_queues[sid]
        original_wait = queue.wait_for_events
        session_mgr = webui.coordinator.session_manager

        async def patched_wait_with_completion(since, timeout):
            # Record a completion now — after mark_viewed has already recorded viewed_at
            future_ts = datetime.now(UTC)
            await session_mgr.mark_completion(sid, future_ts)
            return await original_wait(since, timeout)

        queue.wait_for_events = patched_wait_with_completion
        try:
            resp = await client.get(f"/api/poll/session/{sid}?since=0&timeout=0")
            assert resp.status_code == 200

            info = session_mgr._active_sessions.get(sid)
            assert info is not None
            assert info.last_completion_at is not None, "Expected last_completion_at to be set"
            assert info.last_viewed_at is not None, "Expected last_viewed_at to be set"
            assert info.last_completion_at > info.last_viewed_at, (
                "Completion after mark_viewed must be unread: "
                f"last_completion_at={info.last_completion_at}, "
                f"last_viewed_at={info.last_viewed_at}"
            )
        finally:
            queue.wait_for_events = original_wait

    async def test_no_prior_completion_mark_viewed_is_noop(self, api_integration_env):
        """Issue #1598: Sessions with no completion don't have last_viewed_at written."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        # No completion has been recorded — mark_viewed should be a no-op
        resp = await client.get(f"/api/poll/session/{sid}?since=0&timeout=0")
        assert resp.status_code == 200

        info = webui.coordinator.session_manager._active_sessions.get(sid)
        assert info is not None
        # No completion → mark_viewed guard skips the write → last_viewed_at stays None
        assert info.last_completion_at is None
        assert info.last_viewed_at is None
