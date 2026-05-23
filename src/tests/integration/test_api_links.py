"""Integration tests for the links REST endpoint — issue #1530.

Tests:
  GET /api/sessions/{session_id}/links — empty, populated, 404
  Direct coordinator upsert → endpoint returns the link
  Links survive a message-clearing (session reset simulation)
"""

import uuid


async def _create_session(env):
    project = await env["create_test_project"]("Links Test")
    session = await env["create_test_session"](project["project_id"], "LinksSession")
    return session


class TestGetSessionLinks:
    async def test_issue_1530_get_links_empty_when_none(self, api_integration_env):
        """GET /api/sessions/{id}/links returns empty list for a new session."""
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        resp = await client.get(f"/api/sessions/{sid}/links")
        assert resp.status_code == 200
        assert resp.json() == {"links": []}

    async def test_issue_1530_get_links_returns_registered(self, api_integration_env):
        """After upsert_link on the coordinator, GET returns the link."""
        client = api_integration_env["client"]
        coordinator = api_integration_env["session_coordinator"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        await coordinator.session_manager.upsert_link(
            sid, "GH Issue", "https://github.com/issues/1530"
        )

        resp = await client.get(f"/api/sessions/{sid}/links")
        assert resp.status_code == 200
        links = resp.json()["links"]
        assert len(links) == 1
        assert links[0]["label"] == "GH Issue"
        assert links[0]["url"] == "https://github.com/issues/1530"

    async def test_issue_1530_get_links_404_for_missing_session(self, api_integration_env):
        client = api_integration_env["client"]
        resp = await client.get(f"/api/sessions/{uuid.uuid4()}/links")
        assert resp.status_code == 404

    async def test_issue_1530_links_survive_message_clear(self, api_integration_env):
        """Links on session_info are not affected by clearing messages.jsonl directly."""
        coordinator = api_integration_env["session_coordinator"]
        client = api_integration_env["client"]
        session = await _create_session(api_integration_env)
        sid = session["session_id"]

        # Register a link
        await coordinator.session_manager.upsert_link(
            sid, "Docs", "https://docs.example.com"
        )

        # Simulate message clear: truncate messages.jsonl
        session_dir = coordinator.data_dir / "sessions" / sid
        (session_dir / "messages.jsonl").write_text("")

        # Links endpoint still returns the link
        resp = await client.get(f"/api/sessions/{sid}/links")
        assert resp.status_code == 200
        links = resp.json()["links"]
        assert len(links) == 1
        assert links[0]["label"] == "Docs"

    async def test_issue_1530_reload_from_disk_returns_links(self, api_integration_env):
        """Links persisted in state.json are returned after a coordinator reload."""
        import tempfile
        from pathlib import Path

        from src.session_config import SessionConfig
        from src.session_manager import SessionManager

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            manager1 = SessionManager(data_dir)
            await manager1.initialize()

            from src.session_coordinator import SessionCoordinator
            coord = SessionCoordinator(data_dir=data_dir)
            await coord.initialize()

            # Create session via coordinator
            await coord.project_manager.create_project(
                name="Test",
                working_directory="/tmp",
            )
            session_config = SessionConfig(
                working_directory="/tmp",
                permission_mode="acceptEdits",
            )
            sid = str(uuid.uuid4())
            await coord.session_manager.create_session(sid, config=session_config)

            # Register link
            await coord.session_manager.upsert_link(sid, "Test", "https://test.example.com")

            # Reload session manager from disk
            manager2 = SessionManager(data_dir)
            await manager2.initialize()

            session = await manager2.get_session_info(sid)
            assert len(session.links) == 1
            assert session.links[0]["label"] == "Test"
