"""
Stage 7: Integration tests for Archive endpoints.

Tests:
- GET /api/projects/{project_id}/archives/{session_id} — list archives
- GET /api/projects/{project_id}/archives/{session_id}/{archive_id}/messages — paginated messages
- GET /api/projects/{project_id}/archives/{session_id}/{archive_id}/state — state + metadata
- GET /api/projects/{project_id}/archives/{session_id}/{archive_id}/resources — resources
- GET /api/projects/{project_id}/archives/{session_id}/{archive_id}/resources/{resource_id} — resource file
- GET /api/projects/{project_id}/deleted-agents — deleted agents list

Issue #1263 — archive viewer tabs (proxy logs, queue, attachments, history, schedules):
- GET .../proxy-logs, .../proxy-logs/{log_name}
- GET .../queue
- GET .../attachments, .../attachments/{filename}
- GET .../history, .../history/{filename}
- GET .../schedules
"""

import json
import uuid


async def _create_and_delete_session(env):
    """Create a session, add some messages to disk, then delete it to generate archive."""
    client = env["client"]
    data_dir = env["data_dir"]

    project = await env["create_test_project"]("Archive Test")
    pid = project["project_id"]
    session = await env["create_test_session"](pid, "ToArchive")
    sid = session["session_id"]

    # Write some test messages to messages.jsonl so the archive has content
    messages_file = data_dir / "sessions" / sid / "messages.jsonl"
    messages_file.parent.mkdir(parents=True, exist_ok=True)
    with open(messages_file, "a") as f:
        f.write(json.dumps({
            "type": "system", "content": "Test message", "session_id": sid
        }) + "\n")

    # Delete the session (triggers archival)
    await client.delete(f"/api/sessions/{sid}")

    return pid, sid


class TestListArchives:
    async def test_list_archives_after_delete(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        assert resp.status_code == 200
        body = resp.json()
        assert "archives" in body

    async def test_list_archives_no_archives(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]("No Archives")
        pid = project["project_id"]
        fake_sid = str(uuid.uuid4())

        resp = await client.get(f"/api/projects/{pid}/archives/{fake_sid}")
        assert resp.status_code == 200
        assert resp.json()["archives"] == []


class TestArchiveMessages:
    async def test_get_archive_messages(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        # Get archives list
        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/messages"
            )
            assert resp.status_code == 200
            assert "messages" in resp.json()


class TestArchiveState:
    async def test_get_archive_state(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/state"
            )
            assert resp.status_code == 200


class TestArchiveResources:
    async def test_get_archive_resources(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/resources"
            )
            assert resp.status_code == 200

    async def test_get_archive_resource_file(self, api_integration_env):
        """GET /api/projects/{pid}/archives/{sid}/{aid}/resources/{rid} — raw file."""
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            res_resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/resources"
            )
            resources = res_resp.json().get("resources", [])
            if resources:
                rid = resources[0]["resource_id"]
                resp = await client.get(
                    f"/api/projects/{pid}/archives/{sid}/{archive_id}/resources/{rid}"
                )
                assert resp.status_code == 200

    async def test_get_archive_resource_file_range_request_returns_206(self, api_integration_env):
        """GET .../resources/{rid} with a Range header — 206 with correct slice (Issue #1716)."""
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            res_resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/resources"
            )
            resources = res_resp.json().get("resources", [])
            if resources:
                rid = resources[0]["resource_id"]
                resp = await client.get(
                    f"/api/projects/{pid}/archives/{sid}/{archive_id}/resources/{rid}",
                    headers={"Range": "bytes=0-3"},
                )
                assert resp.status_code == 206
                assert resp.headers.get("content-range", "").startswith("bytes 0-3/")

    async def test_get_archive_resource_file_nonexistent(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
        archives = resp.json().get("archives", [])

        if archives:
            archive_id = archives[0]["archive_id"]
            resp = await client.get(
                f"/api/projects/{pid}/archives/{sid}/{archive_id}/resources/nonexistent"
            )
            assert resp.status_code == 404


async def _create_archived_session_with_archive_id(env):
    """Create+delete a session, returning (pid, sid, archive_id, archive_dir).

    archive_dir is the real on-disk archive directory, usable by tests that
    need to drop extra fixture files (proxy logs, attachments, history,
    schedules) directly, mirroring how the unit tests construct fixtures.
    """
    data_dir = env["data_dir"]
    client = env["client"]
    pid, sid = await _create_and_delete_session(env)

    resp = await client.get(f"/api/projects/{pid}/archives/{sid}")
    archives = resp.json().get("archives", [])
    assert archives, "expected at least one archive after deletion"
    archive_id = archives[0]["archive_id"]
    archive_dir = data_dir / "archives" / "minions" / sid / archive_id
    return pid, sid, archive_id, archive_dir


class TestArchiveProxyLogs:
    async def test_list_proxy_logs_not_captured_returns_empty(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/proxy-logs"
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_proxy_logs_archive_not_found(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]("Proxy 404")
        pid = project["project_id"]
        sid = str(uuid.uuid4())

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/no-such-archive/proxy-logs"
        )
        assert resp.status_code == 404

    async def test_get_proxy_log_content_not_captured_returns_empty(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/proxy-logs/access.log"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"lines": [], "total_lines": 0, "start_line": 0, "end_line": 0}

    async def test_get_proxy_log_content_with_data(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )
        proxy_dir = archive_dir / "proxy"
        proxy_dir.mkdir(parents=True, exist_ok=True)
        (proxy_dir / "access.log").write_text("line-0\nline-1\nline-2\n")

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/proxy-logs/access.log"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["lines"] == ["line-0", "line-1", "line-2"]
        assert body["total_lines"] == 3

    async def test_get_proxy_log_content_invalid_name_returns_400(self, api_integration_env):
        """A name outside the fixed _PROXY_LOG_NAMES allowlist is rejected with 400."""
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/proxy-logs/evil.log"
        )
        assert resp.status_code == 400

    async def test_get_proxy_log_content_traversal_attempt_never_leaks(self, api_integration_env):
        """%2F-encoded traversal never reaches the handler — Starlette's router
        decodes it and 404s before matching the single-segment {log_name} path
        param, which is itself a safe (non-leaking) outcome."""
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}"
            "/proxy-logs/..%2F..%2Fetc%2Fpasswd"
        )
        assert resp.status_code == 404
        assert b"root:" not in resp.content

    async def test_get_proxy_log_content_archive_not_found(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]("Proxy Content 404")
        pid = project["project_id"]
        sid = str(uuid.uuid4())

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/no-such-archive/proxy-logs/access.log"
        )
        assert resp.status_code == 404


class TestArchiveQueue:
    async def test_get_archive_queue_empty(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(f"/api/projects/{pid}/archives/{sid}/{archive_id}/queue")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_get_archive_queue_archive_not_found(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]("Queue 404")
        pid = project["project_id"]
        sid = str(uuid.uuid4())

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/no-such-archive/queue"
        )
        assert resp.status_code == 404


class TestArchiveAttachments:
    async def test_list_attachments_empty(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/attachments"
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_get_attachment_file(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )
        att_dir = archive_dir / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)
        (att_dir / "notes.txt").write_bytes(b"hello world")

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/attachments/notes.txt"
        )
        assert resp.status_code == 200
        assert resp.content == b"hello world"
        assert "attachment" in resp.headers.get("content-disposition", "")

    async def test_get_attachment_file_missing(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/attachments/nope.bin"
        )
        assert resp.status_code == 404

    async def test_get_attachment_file_path_traversal_rejected(self, api_integration_env):
        """../../etc/passwd-style attempt must be rejected, never leak a file."""
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/attachments/..%2F..%2F..%2Fetc%2Fpasswd"
        )
        assert resp.status_code == 404
        assert b"root:" not in resp.content

    async def test_attachments_archive_not_found(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]("Attachments 404")
        pid = project["project_id"]
        sid = str(uuid.uuid4())

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/no-such-archive/attachments"
        )
        assert resp.status_code == 404


class TestArchiveHistory:
    async def test_list_history_empty(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )
        # Issue #710 history distillation is fire-and-forget and may race with
        # this assertion; remove any history dir it may have created so this
        # test deterministically covers the "no history captured" empty state.
        history_dir = archive_dir / "history"
        if history_dir.exists():
            for f in history_dir.glob("*.md"):
                f.unlink()

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/history"
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_get_history_content(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )
        history_dir = archive_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        (history_dir / "disposal.md").write_text("# Disposal Summary\n")

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/history/disposal.md"
        )
        assert resp.status_code == 200
        assert resp.json() == {"content": "# Disposal Summary\n"}

    async def test_get_history_content_missing(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/history/nope.md"
        )
        assert resp.status_code == 404

    async def test_get_history_content_path_traversal_rejected(self, api_integration_env):
        """../../etc/passwd-style attempt must be rejected, never leak a file."""
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/history/..%2F..%2F..%2Fetc%2Fpasswd"
        )
        assert resp.status_code == 404
        assert b"root:" not in resp.content

    async def test_history_archive_not_found(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]("History 404")
        pid = project["project_id"]
        sid = str(uuid.uuid4())

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/no-such-archive/history"
        )
        assert resp.status_code == 404


class TestArchiveSchedules:
    async def test_get_schedules_empty(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, _archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/schedules"
        )
        assert resp.status_code == 200
        assert resp.json() == {"schedules": [], "history": []}

    async def test_get_schedules_with_data(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid, archive_id, archive_dir = await _create_archived_session_with_archive_id(
            api_integration_env
        )
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "schedules.json").write_text(
            json.dumps([
                {"schedule_id": "s1", "minion_id": sid, "cron_expression": "0 2 * * *"},
                {"schedule_id": "s2", "minion_id": "other-minion", "cron_expression": "*/5 * * * *"},
            ])
        )

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/{archive_id}/schedules"
        )
        assert resp.status_code == 200
        body = resp.json()
        # Only this minion's own schedule is returned — not other minions' in the
        # same legion-wide schedules.json (issue #1263 review finding).
        assert body["schedules"] == [
            {"schedule_id": "s1", "minion_id": sid, "cron_expression": "0 2 * * *"}
        ]

    async def test_schedules_archive_not_found(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]("Schedules 404")
        pid = project["project_id"]
        sid = str(uuid.uuid4())

        resp = await client.get(
            f"/api/projects/{pid}/archives/{sid}/no-such-archive/schedules"
        )
        assert resp.status_code == 404


class TestDeletedAgents:
    async def test_deleted_agents_empty(self, api_integration_env):
        client = api_integration_env["client"]
        project = await api_integration_env["create_test_project"]("No Deletes")
        pid = project["project_id"]

        resp = await client.get(f"/api/projects/{pid}/deleted-agents")
        assert resp.status_code == 200
        body = resp.json()
        assert "agents" in body or "deleted_agents" in body

    async def test_deleted_agents_after_delete(self, api_integration_env):
        client = api_integration_env["client"]
        pid, sid = await _create_and_delete_session(api_integration_env)

        resp = await client.get(f"/api/projects/{pid}/deleted-agents")
        assert resp.status_code == 200
