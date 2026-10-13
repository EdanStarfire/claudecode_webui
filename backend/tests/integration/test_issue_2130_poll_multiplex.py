"""Issue #2130 (epic #1990 stage 5c), phase 1: integration tests for the new
multiplexed `POST /api/poll` endpoint on the Backend tier. Legacy
`/api/poll/ui`/`/api/poll/session/{id}` endpoints are untouched by this stage and
keep their own test coverage (test_issue_782_long_poll.py et al.) — these tests
cover only the new endpoint.

T1 (US1 — fair delivery): sustained delta load on one subscribed session does not
delay UI-stream events for another stream in the same multiplexed response.
"""

import asyncio


class TestPollMultiplexBasics:
    async def test_ui_only_subscription_returns_ui_events(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        webui.ui_queue.append({"type": "test_event", "value": 1})

        resp = await client.post("/api/poll", json={"timeout": 0, "ui": {"since": 0}})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["ui"]["events"]) >= 1
        assert data["sessions"] == {}

    async def test_session_subscription_returns_session_events(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]
        webui.session_queues[sid].append({"type": "message", "value": "hi"})

        resp = await client.post(
            "/api/poll", json={"timeout": 0, "sessions": {sid: {"since": 0}}}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "ui" not in data
        assert len(data["sessions"][sid]["events"]) >= 1

    async def test_combined_ui_and_session_subscription(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]
        webui.ui_queue.append({"type": "ui_event"})
        webui.session_queues[sid].append({"type": "session_event"})

        resp = await client.post(
            "/api/poll",
            json={"timeout": 0, "ui": {"since": 0}, "sessions": {sid: {"since": 0}}},
        )
        data = resp.json()
        assert len(data["ui"]["events"]) >= 1
        assert len(data["sessions"][sid]["events"]) >= 1

    async def test_unknown_session_id_omitted_not_errored(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await client.post(
            "/api/poll",
            json={"timeout": 0, "ui": {"since": 0}, "sessions": {"does-not-exist": {"since": 0}}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "does-not-exist" not in data["sessions"]

    async def test_cursors_advance_independently_per_stream(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]
        webui.ui_queue.append({"type": "ui_event"})

        resp = await client.post(
            "/api/poll",
            json={"timeout": 0, "ui": {"since": 0}, "sessions": {sid: {"since": 0}}},
        )
        data = resp.json()
        assert data["ui"]["next_cursor"] >= 1
        assert data["sessions"][sid]["next_cursor"] == 0

    async def test_mark_viewed_called_for_subscribed_session(self, api_integration_env):
        """Issue #1598 parity: the multiplexed endpoint must mark a subscribed
        session viewed, same as the legacy single-stream endpoint did."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        calls = []
        original_mark_viewed = webui.coordinator.session_manager.mark_viewed

        async def patched(session_id):
            calls.append(session_id)
            return await original_mark_viewed(session_id)

        webui.coordinator.session_manager.mark_viewed = patched
        try:
            resp = await client.post(
                "/api/poll", json={"timeout": 0, "sessions": {sid: {"since": 0}}}
            )
            assert resp.status_code == 200
            assert calls == [sid]
        finally:
            webui.coordinator.session_manager.mark_viewed = original_mark_viewed


class TestPollMultiplexFairness:
    async def test_t1_sustained_session_load_does_not_delay_ui_event(self, api_integration_env):
        """T1: a sustained stream of events on one subscribed session must not
        delay UI-stream event delivery. A real client re-polls immediately after
        every response (never blocking on a stream with pending data), so the
        guarantee under test is bounded wall-clock latency across a tight re-poll
        loop — not that a single in-flight long-poll call freezes at the instant
        the UI event is appended. A noisy session must never push that latency
        anywhere near the per-call long-poll timeout."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        stop = asyncio.Event()

        async def noisy_session():
            while not stop.is_set():
                webui.session_queues[sid].append({"type": "assistant_delta", "text": "x"})
                await asyncio.sleep(0.005)

        noise_task = asyncio.create_task(noisy_session())
        try:
            await asyncio.sleep(0.03)  # let the noise start flowing first

            async def emit_ui_event_soon():
                await asyncio.sleep(0.1)
                webui.ui_queue.append({"type": "notification"})

            emitter = asyncio.create_task(emit_ui_event_soon())
            ui_cursor = webui.ui_queue.current_cursor
            session_cursor = webui.session_queues[sid].current_cursor
            found_at = None
            start = asyncio.get_event_loop().time()
            try:
                # Tight client re-poll loop, exactly as the browser does: never
                # idle-sleeps between polls, always resumes from the last
                # next_cursor of each stream.
                while asyncio.get_event_loop().time() - start < 2.0:
                    resp = await asyncio.wait_for(
                        client.post(
                            "/api/poll",
                            json={
                                "timeout": 1,
                                "ui": {"since": ui_cursor},
                                "sessions": {sid: {"since": session_cursor}},
                            },
                        ),
                        timeout=2.0,
                    )
                    data = resp.json()
                    ui_cursor = data["ui"]["next_cursor"]
                    session_cursor = data["sessions"][sid]["next_cursor"]
                    if any(e["type"] == "notification" for e in data["ui"]["events"]):
                        found_at = asyncio.get_event_loop().time()
                        break
            finally:
                await emitter
            assert found_at is not None, "UI notification never arrived"
            # Emitted at +0.1s after the loop's `start`; a healthy raced-wait
            # design delivers it within a small bound, never anywhere near this
            # call's own 1s per-call timeout (which an un-raced/serialized
            # implementation could get starved behind).
            assert found_at - start < 0.6
        finally:
            stop.set()
            noise_task.cancel()
            try:
                await noise_task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_quota_truncation_does_not_block_other_subscribed_stream(self, api_integration_env):
        """A session backlogged well beyond the per-stream quota must still be
        truncated independently of the UI stream's own (small) delivery, and
        fully drainable over successive polls with no gap or duplicate."""
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        for i in range(500):
            webui.session_queues[sid].append({"type": "assistant_delta", "i": i})
        webui.ui_queue.append({"type": "notification"})

        resp = await client.post(
            "/api/poll",
            json={"timeout": 0, "ui": {"since": 0}, "sessions": {sid: {"since": 0}}},
        )
        data = resp.json()
        assert len(data["ui"]["events"]) >= 1
        session_events = list(data["sessions"][sid]["events"])
        assert len(session_events) < 500  # truncated below the full backlog

        # Drain the remainder over successive polls — next_cursor must be
        # resumable with no gap and no re-delivery of any already-seen event.
        cursor = data["sessions"][sid]["next_cursor"]
        for _ in range(10):
            if len(session_events) >= 500:
                break
            resp = await client.post(
                "/api/poll", json={"timeout": 0, "sessions": {sid: {"since": cursor}}}
            )
            batch = resp.json()["sessions"][sid]
            session_events.extend(batch["events"])
            cursor = batch["next_cursor"]

        assert len(session_events) == 500
        assert [e["i"] for e in session_events] == list(range(500))
