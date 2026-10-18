"""
Stage 5: Integration tests for Legion endpoints.

Tests:
- GET /api/legions/{legion_id}/timeline — paginated comm timeline
- GET /api/legions/{legion_id}/hierarchy — minion hierarchy tree
- POST /api/legions/{legion_id}/comms — send comm
- POST /api/legions/{legion_id}/minions — create minion
- POST /api/legions/{legion_id}/halt-all — emergency halt
- POST /api/legions/{legion_id}/resume-all — resume all
"""

import uuid


async def _setup_legion(env):
    """Create a legion project for testing."""
    project = await env["create_test_legion_project"]("Legion Test")
    return project


class TestLegionTimeline:
    async def test_timeline_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.get(f"/api/legions/{lid}/timeline")
        assert resp.status_code == 200
        body = resp.json()
        assert body["comms"] == []
        assert body["total"] == 0

    async def test_timeline_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        # Non-existent legion returns empty timeline (no 404)
        resp = await client.get(f"/api/legions/{fake_id}/timeline")
        assert resp.status_code == 200
        assert resp.json()["comms"] == []

    async def test_timeline_pagination(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.get(f"/api/legions/{lid}/timeline?limit=10&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert "limit" in body
        assert "offset" in body


class TestLegionHierarchy:
    async def test_hierarchy_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.get(f"/api/legions/{lid}/hierarchy")
        assert resp.status_code == 200

    async def test_hierarchy_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.get(f"/api/legions/{fake_id}/hierarchy")
        assert resp.status_code == 404


class TestSendComm:
    async def test_send_comm_to_minion(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        # Send comm to user (avoids need for active minion SDK session)
        resp = await client.post(
            f"/api/legions/{lid}/comms",
            json={
                "to_user": True,
                "content": "Status update for user",
                "comm_type": "info",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "comm" in resp.json()

    async def test_send_comm_nonexistent_legion(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post(
            f"/api/legions/{fake_id}/comms",
            json={"content": "Hello", "comm_type": "info"},
        )
        assert resp.status_code == 404

    async def test_send_comm_to_dormant_minion_auto_starts(self, api_integration_env):
        """Issue #2193 PR5 regression: this endpoint's Comm must set auto_start=True
        so a message to a dormant (CREATED) minion still triggers an auto-start
        attempt, matching the pre-AC8-enforcement behavior — a cutover-era
        regression caught in review (deliver()'s AC8 gate now fails fast on a
        dormant target unless auto_start is set, and this endpoint doesn't take a
        delivery/auto_start request field). Asserts the auto-start was actually
        attempted (state left "created" would mean AC8 blocked it outright) rather
        than asserting a full mock-SDK conversation succeeds, which is unrelated to
        this fix."""
        client = api_integration_env["client"]
        create_test_session = api_integration_env["create_test_session"]
        data_dir = api_integration_env["data_dir"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        session = await create_test_session(lid, name="Dormant")
        sid = session["session_id"]
        assert session["state"] == "created"
        # MockClaudeSDK's SessionRecording requires messages.jsonl to already
        # exist (even empty) in the session's working_directory (not its data
        # dir) when no fixture session_name is given — unrelated to this fix,
        # just a replay-engine precondition for a session with no recorded
        # fixture.
        (data_dir.parent / "messages.jsonl").touch()

        resp = await client.post(
            f"/api/legions/{lid}/comms",
            json={
                "to_minion_id": sid,
                "content": "wake up",
                "comm_type": "task",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        session_resp = await client.get(f"/api/sessions/{sid}")
        assert session_resp.json()["session"]["state"] == "active"


class TestCreateMinion:
    async def test_create_minion(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.post(
            f"/api/legions/{lid}/minions",
            json={
                "name": "Test Worker",
                "role": "Code Review",
                "permission_mode": "acceptEdits",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["minion_id"]
        assert body["minion"]["name"] == "Test Worker"

    async def test_create_minion_nonexistent_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post(
            f"/api/legions/{fake_id}/minions",
            json={"name": "Orphan", "role": "Worker"},
        )
        assert resp.status_code == 404

    async def test_create_minion_with_capabilities(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.post(
            f"/api/legions/{lid}/minions",
            json={
                "name": "Capable Worker",
                "role": "Specialist",
                "capabilities": ["python", "testing"],
            },
        )
        assert resp.status_code == 200


class TestHaltAll:
    async def test_halt_all_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.post(f"/api/legions/{lid}/halt-all")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["stopped_session_ids"] == []
        assert body["failed_sessions"] == []
        assert body["total_sessions"] == 0

    async def test_halt_all_response_shape(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.post(f"/api/legions/{lid}/halt-all")
        assert resp.status_code == 200
        body = resp.json()
        assert "stopped_session_ids" in body
        assert "failed_sessions" in body
        assert "total_sessions" in body
        assert isinstance(body["stopped_session_ids"], list)
        assert isinstance(body["failed_sessions"], list)
        assert isinstance(body["total_sessions"], int)

    async def test_halt_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post(f"/api/legions/{fake_id}/halt-all")
        assert resp.status_code == 404


class TestResumeAll:
    async def test_resume_all_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.post(f"/api/legions/{lid}/resume-all")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["resumed_count"] == 0

    async def test_resume_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post(f"/api/legions/{fake_id}/resume-all")
        assert resp.status_code == 404


class TestFleetSnapshot:
    """Issue #2159 (stage 6c): the persisted halt/resume snapshot, replacing
    frontend/src/utils/stoppedSet.js's sessionStorage-only bookkeeping."""

    async def test_snapshot_empty_for_fresh_project(self, api_integration_env):
        client = api_integration_env["client"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        resp = await client.get(f"/api/legions/{lid}/fleet-snapshot")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stopped_session_ids"] == []
        assert body["processing_session_ids"] == []

    async def test_snapshot_nonexistent_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.get(f"/api/legions/{fake_id}/fleet-snapshot")
        assert resp.status_code == 404

    async def test_halt_all_populates_snapshot(self, api_integration_env):
        """halt-all must persist what it stopped, survivable via the snapshot
        endpoint — not just returned once in the halt-all response body."""
        client = api_integration_env["client"]
        create_test_session = api_integration_env["create_test_session"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        session = await create_test_session(lid, name="to-be-halted")
        sid = session["session_id"]
        await client.post(f"/api/sessions/{sid}/start")

        halt_resp = await client.post(f"/api/legions/{lid}/halt-all")
        assert halt_resp.status_code == 200
        assert sid in halt_resp.json()["stopped_session_ids"]

        snap_resp = await client.get(f"/api/legions/{lid}/fleet-snapshot")
        assert snap_resp.status_code == 200
        assert sid in snap_resp.json()["stopped_session_ids"]

    async def test_record_then_clear_round_trip(self, api_integration_env):
        client = api_integration_env["client"]
        create_test_session = api_integration_env["create_test_session"]
        project = await _setup_legion(api_integration_env)
        lid = project["project_id"]

        session = await create_test_session(lid, name="stop-selected-target")
        sid = session["session_id"]

        record_resp = await client.post(
            f"/api/legions/{lid}/fleet-snapshot/record",
            json={"session_ids": [sid], "processing_session_ids": [sid]},
        )
        assert record_resp.status_code == 200
        assert record_resp.json()["stopped_session_ids"] == [sid]
        assert record_resp.json()["processing_session_ids"] == [sid]

        snap_resp = await client.get(f"/api/legions/{lid}/fleet-snapshot")
        assert snap_resp.json()["stopped_session_ids"] == [sid]

        clear_resp = await client.post(
            f"/api/legions/{lid}/fleet-snapshot/clear",
            json={"session_ids": [sid]},
        )
        assert clear_resp.status_code == 200
        assert clear_resp.json()["stopped_session_ids"] == []
        assert clear_resp.json()["processing_session_ids"] == []

    async def test_record_nonexistent_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post(
            f"/api/legions/{fake_id}/fleet-snapshot/record",
            json={"session_ids": ["s1"]},
        )
        assert resp.status_code == 404

    async def test_clear_nonexistent_project(self, api_integration_env):
        client = api_integration_env["client"]
        fake_id = str(uuid.uuid4())

        resp = await client.post(
            f"/api/legions/{fake_id}/fleet-snapshot/clear",
            json={"session_ids": ["s1"]},
        )
        assert resp.status_code == 404


class TestDisposeMinionHardDeleteBroadcast:
    """Issue #1986: OverseerController.dispose_minion()'s hard-delete path calls
    session_coordinator.delete_session() directly — bypassing the
    DELETE /api/sessions/{id} HTTP route entirely. This is the edge case the
    coordinator-level (rather than router-level) event design exists to cover:
    it must produce the same session_deleted broadcast as an HTTP delete, with
    no exception logged, even though no HTTP request is ever made."""

    async def test_dispose_minion_hard_delete_broadcasts_session_deleted(
        self, api_integration_env, caplog
    ):
        from backend.session_config import SessionConfig

        coordinator = api_integration_env["coordinator"]
        webui = api_integration_env["webui"]
        project = await _setup_legion(api_integration_env)
        legion_id = project["project_id"]

        parent_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=parent_id,
            project_id=legion_id,
            config=SessionConfig(),
            name="Parent",
        )

        child_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=child_id,
            project_id=legion_id,
            config=SessionConfig(),
            name="Child",
            parent_overseer_id=parent_id,
        )

        # Wire the parent/child relationship the way spawn_minion would (mirrors
        # TestIssue1722DeleteSessionKanbanCleanup's cascading-delete test setup).
        parent_info = await coordinator.session_manager.get_session_info(parent_id)
        parent_info.child_minion_ids = [child_id]

        _, cursor_before, _ = webui.ui_queue.events_since(0)

        with caplog.at_level("ERROR"):
            result = await coordinator.legion_system.overseer_controller.dispose_minion(
                parent_overseer_id=parent_id,
                child_minion_name="Child",
                delete_after_archive=True,
            )

        assert result["success"] is True
        assert result["deleted"] is True
        assert result["disposed_minion_id"] == child_id

        events, _, _ = webui.ui_queue.events_since(cursor_before)
        session_deleted_events = [e for e in events if e["type"] == "session_deleted"]
        assert len(session_deleted_events) == 1
        assert session_deleted_events[0]["data"] == {"session_id": child_id}

        assert not any(record.levelname == "ERROR" for record in caplog.records)
        assert not any(record.exc_info for record in caplog.records)
