"""Issue #2305: Backend-side pieces — session-stream epoch
(process epoch + queue generation), and session reset clearing the session queue."""



async def _poll_session(client, sid, **query):
    resp = await client.post("/api/poll", json={"timeout": 0, "sessions": {sid: query}})
    assert resp.status_code == 200
    return resp.json()["sessions"][sid]


class TestSessionReset:
    async def test_reset_drops_pre_reset_events_and_changes_epoch(self, api_integration_env):
        client = api_integration_env["client"]
        webui = api_integration_env["webui"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]
        queue = webui.session_queues[sid]
        for i in range(5):
            queue.append({"type": "message", "i": i})
        before = await _poll_session(client, sid, since=0)

        await webui._on_session_reset(sid)

        assert webui.session_queues[sid] is queue  # reset in place, not replaced
        queue.append({"type": "message", "i": "post"})
        for since in (0, 3, before["next_cursor"]):
            after = await _poll_session(client, sid, since=since)
            assert [e["i"] for e in after["events"]] == ["post"]
            assert after["epoch"] != before["epoch"]

    async def test_get_messages_epoch_matches_poll_epoch(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]
        polled = await _poll_session(client, sid, since=0)
        resp = await client.get(f"/api/sessions/{sid}/messages")
        assert resp.json()["epoch"] == polled["epoch"]
