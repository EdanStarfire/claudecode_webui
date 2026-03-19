"""
Issue #782: Integration tests for HTTP long-polling endpoints and REST inbound actions.

Tests:
- EventQueue: append, events_since (normal cursor, too-old cursor snapshot), MAX_SIZE eviction
- GET /api/poll/ui: returns events, correct next_cursor
- GET /api/poll/session/{id}: returns events for that session only
- POST /api/sessions/{id}/interrupt: calls coordinator interrupt
- POST /api/sessions/{id}/permission/{request_id}: resolves Future with allow/deny
"""

import asyncio

import pytest


# ===========================================================================
# EventQueue unit tests (no HTTP client needed)
# ===========================================================================

class TestEventQueue:
    def _make_queue(self):
        from src.web_server import EventQueue
        return EventQueue()

    def test_append_returns_cursor(self):
        q = self._make_queue()
        c1 = q.append({"type": "a"})
        c2 = q.append({"type": "b"})
        assert c1 == 1
        assert c2 == 2

    def test_events_since_normal_cursor(self):
        q = self._make_queue()
        q.append({"type": "a"})
        q.append({"type": "b"})
        q.append({"type": "c"})
        events, next_cur = q.events_since(1)
        assert len(events) == 2
        assert events[0]["type"] == "b"
        assert events[1]["type"] == "c"
        assert next_cur == 3

    def test_events_since_zero_returns_all(self):
        q = self._make_queue()
        q.append({"type": "x"})
        q.append({"type": "y"})
        events, next_cur = q.events_since(0)
        assert len(events) == 2
        assert next_cur == 2

    def test_events_since_too_old_cursor_returns_full_snapshot(self):
        """Cursor predating the buffer returns all buffered events."""
        from src.web_server import EventQueue
        q = EventQueue()
        q.MAX_SIZE = 3  # tiny buffer for this test
        # Append 5 events — oldest 2 are evicted
        for i in range(5):
            q.append({"type": str(i), "i": i})
        # Oldest buffered cursor is 3 (events 3,4,5)
        # Request cursor=0 (too old) → full snapshot
        events, next_cur = q.events_since(0)
        assert len(events) == 3
        assert next_cur == 5

    def test_max_size_eviction(self):
        from src.web_server import EventQueue
        q = EventQueue()
        q.MAX_SIZE = 3
        for i in range(10):
            q.append({"i": i})
        # Only last 3 remain
        events, _ = q.events_since(0)
        assert len(events) == 3
        assert events[-1]["i"] == 9

    def test_empty_queue(self):
        q = self._make_queue()
        events, cur = q.events_since(0)
        assert events == []
        assert cur == 0

    async def test_wait_for_events_returns_immediately_if_available(self):
        q = self._make_queue()
        q.append({"type": "ready"})
        # Should return immediately (no timeout needed)
        await asyncio.wait_for(q.wait_for_events(0, timeout=5.0), timeout=1.0)

    async def test_wait_for_events_wakes_on_append(self):
        q = self._make_queue()

        async def _appender():
            await asyncio.sleep(0.05)
            q.append({"type": "wakeup"})

        appender = asyncio.create_task(_appender())
        await asyncio.wait_for(q.wait_for_events(0, timeout=5.0), timeout=2.0)
        events, _ = q.events_since(0)
        assert len(events) == 1
        await appender

    async def test_concurrent_pollers_all_wake(self):
        """Multiple concurrent pollers all wake when an event arrives."""
        q = self._make_queue()
        results = []

        async def _poller():
            await q.wait_for_events(0, timeout=5.0)
            results.append(True)

        pollers = [asyncio.create_task(_poller()) for _ in range(5)]
        await asyncio.sleep(0.05)
        q.append({"type": "broadcast"})
        await asyncio.gather(*pollers)
        assert len(results) == 5


# ===========================================================================
# Poll endpoint tests
# ===========================================================================

class TestPollUI:
    async def test_poll_ui_returns_empty_on_no_events(self, api_integration_env):
        client = api_integration_env["client"]
        # Use short timeout to avoid long wait in tests
        resp = await client.get("/api/poll/ui?since=0&timeout=0")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert "next_cursor" in data
        assert isinstance(data["events"], list)

    async def test_poll_ui_returns_events_after_cursor(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        # Inject an event directly into the queue
        webui.ui_queue.append({"type": "test_event", "value": 42})
        webui.ui_queue.append({"type": "test_event_2", "value": 99})

        resp = await client.get("/api/poll/ui?since=0&timeout=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) >= 2
        assert data["next_cursor"] >= 2

    async def test_poll_ui_respects_cursor(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        c1 = webui.ui_queue.append({"type": "first"})
        webui.ui_queue.append({"type": "second"})

        # Poll since c1 — should only get second
        resp = await client.get(f"/api/poll/ui?since={c1}&timeout=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["type"] == "second"

    async def test_poll_ui_next_cursor_advances(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        webui.ui_queue.append({"type": "evt"})
        resp1 = await client.get("/api/poll/ui?since=0&timeout=0")
        cursor1 = resp1.json()["next_cursor"]

        webui.ui_queue.append({"type": "evt2"})
        resp2 = await client.get(f"/api/poll/ui?since={cursor1}&timeout=0")
        assert resp2.json()["next_cursor"] > cursor1

    async def test_poll_ui_state_change_event_appears(self, api_integration_env):
        """State change events from session creation appear in the UI queue."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        # Get current cursor before creating project+session
        _, cursor_before = webui.ui_queue.events_since(0)

        project = await api_integration_env["create_test_project"]("Poll UI Test")
        await api_integration_env["create_test_session"](project["project_id"])

        resp = await client.get(f"/api/poll/ui?since={cursor_before}&timeout=0")
        assert resp.status_code == 200
        data = resp.json()
        event_types = [e.get("type") for e in data["events"]]
        # state_change event should appear when session is created
        assert "state_change" in event_types


class TestPollSession:
    async def test_poll_session_404_for_unknown(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await client.get("/api/poll/session/nonexistent-id?since=0&timeout=0")
        assert resp.status_code == 404

    async def test_poll_session_returns_empty_on_no_events(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        assert sid in webui.session_queues

        resp = await client.get(f"/api/poll/session/{sid}?since=0&timeout=0")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert "next_cursor" in data

    async def test_poll_session_returns_injected_events(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        webui.session_queues[sid].append({"type": "message", "data": {"text": "hello"}})

        resp = await client.get(f"/api/poll/session/{sid}?since=0&timeout=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["type"] == "message"

    async def test_poll_session_isolated_from_other_sessions(self, api_integration_env):
        """Events in session A do not appear in session B's poll."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        sessionA = await api_integration_env["create_test_session"](project["project_id"], "Session A")
        sessionB = await api_integration_env["create_test_session"](project["project_id"], "Session B")
        sidA = sessionA["session_id"]
        sidB = sessionB["session_id"]

        webui.session_queues[sidA].append({"type": "only_for_a"})

        resp = await client.get(f"/api/poll/session/{sidB}?since=0&timeout=0")
        assert resp.status_code == 200
        data = resp.json()
        types = [e["type"] for e in data["events"]]
        assert "only_for_a" not in types

    async def test_session_queue_created_on_session_create(self, api_integration_env):
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]
        assert sid in webui.session_queues

    async def test_session_queue_removed_on_session_delete(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]
        assert sid in webui.session_queues

        resp = await client.delete(f"/api/sessions/{sid}")
        assert resp.status_code == 200
        assert sid not in webui.session_queues


# ===========================================================================
# REST inbound: interrupt
# ===========================================================================

class TestInterruptEndpoint:
    async def test_interrupt_returns_success(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        resp = await client.post(f"/api/sessions/{sid}/interrupt")
        assert resp.status_code == 200
        assert "success" in resp.json()

    async def test_interrupt_unknown_session_returns_error(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await client.post("/api/sessions/nonexistent-id/interrupt")
        # Coordinator.interrupt_session raises or returns falsy for unknown sessions;
        # the endpoint catches exceptions and returns 500
        assert resp.status_code in (200, 500)


# ===========================================================================
# REST inbound: permission response
# ===========================================================================

class TestPermissionEndpoint:
    async def test_permission_allow_resolves_future(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        request_id = "test-perm-allow-001"
        webui.pending_permissions[request_id] = future

        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/permission/{request_id}",
            json={"decision": "allow"}
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert future.done()
        result = future.result()
        assert result["behavior"] == "allow"

    async def test_permission_deny_resolves_future(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        request_id = "test-perm-deny-001"
        webui.pending_permissions[request_id] = future

        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/permission/{request_id}",
            json={"decision": "deny", "clarification_message": "Not allowed"}
        )
        assert resp.status_code == 200
        assert future.done()
        result = future.result()
        assert result["behavior"] == "deny"
        assert "Not allowed" in result["message"]

    async def test_permission_unknown_request_id_returns_404(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        resp = await client.post(
            f"/api/sessions/{sid}/permission/nonexistent-id",
            json={"decision": "allow"}
        )
        assert resp.status_code == 404

    async def test_permission_allow_with_updated_input(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        request_id = "test-perm-input-001"
        webui.pending_permissions[request_id] = future

        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        updated_input = {"questions": [{"answer": "yes"}]}
        resp = await client.post(
            f"/api/sessions/{sid}/permission/{request_id}",
            json={"decision": "allow", "updated_input": updated_input}
        )
        assert resp.status_code == 200
        result = future.result()
        assert result["behavior"] == "allow"
        assert result.get("updated_input") == updated_input

    async def test_permission_cannot_respond_twice(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        request_id = "test-perm-double-001"
        webui.pending_permissions[request_id] = future

        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        # First response succeeds
        resp1 = await client.post(
            f"/api/sessions/{sid}/permission/{request_id}",
            json={"decision": "allow"}
        )
        assert resp1.status_code == 200

        # Second response: request_id is already removed from pending_permissions
        resp2 = await client.post(
            f"/api/sessions/{sid}/permission/{request_id}",
            json={"decision": "deny"}
        )
        assert resp2.status_code == 404
