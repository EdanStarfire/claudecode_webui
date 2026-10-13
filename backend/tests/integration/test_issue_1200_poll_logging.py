"""
Issue #1200: Integration tests verifying the polling logger fires on real poll
responses. Split from src/tests/test_issue_1200_poll_logging.py (issue #498) —
/api/poll is served by backend/routers/poll.py (Backend owns the EventQueues),
so this needs Backend's api_integration_env fixture. The pure
PollAccessLogFilter/configure_logging unit tests stayed in src/tests/ since they
have no coordinator/app dependency.

Epic #1990 stage 5c phase 5: migrated from the legacy single-stream
GET /api/poll/ui and GET /api/poll/session/{id} endpoints (removed this phase)
to POST /api/poll — the logger call sites moved with them (backend/routers/
poll.py's poll_multiplex(), log lines prefixed "poll multiplex ui"/"poll
multiplex session" to distinguish the per-stream block within one response).
"""

import logging
from pathlib import Path

import pytest

from shared.logging_config import PollAccessLogFilter, configure_logging


@pytest.fixture(autouse=True)
def reset_logging_after():
    yield
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    for name in ("polling", "polling_verbose", "sdk_debug", "coordinator",
                 "storage", "parser", "error_handler", "session_manager",
                 "legion", "template_manager"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.setLevel(logging.NOTSET)
        lg.propagate = True
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.filters = [
        f for f in uvicorn_access.filters if not isinstance(f, PollAccessLogFilter)
    ]


class TestPollEndpointLogging:
    """Integration tests verifying the polling logger fires on real poll responses."""

    async def test_empty_poll_no_log_entry(self, api_integration_env, tmp_path):
        """Empty POST /api/poll (ui, since=0) produces no polling.log line."""
        configure_logging(debug_polling=True, log_dir=str(tmp_path))

        client = api_integration_env["client"]
        resp = await client.post("/api/poll", json={"timeout": 0, "ui": {"since": 0}})
        assert resp.status_code == 200
        assert resp.json()["ui"]["events"] == []

        poll_log = Path(tmp_path) / "polling.log"
        if poll_log.exists():
            content = poll_log.read_text()
            assert "returned" not in content

    async def test_events_poll_emits_log_line(self, api_integration_env, tmp_path):
        """Poll returning events emits exactly one polling.log line."""
        configure_logging(debug_polling=True, log_dir=str(tmp_path))

        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        webui.ui_queue.append({"type": "test_evt"})
        webui.ui_queue.append({"type": "test_evt_2"})

        resp = await client.post("/api/poll", json={"timeout": 0, "ui": {"since": 0}})
        assert resp.status_code == 200
        assert len(resp.json()["ui"]["events"]) >= 2

        poll_log = Path(tmp_path) / "polling.log"
        assert poll_log.exists()
        content = poll_log.read_text()
        assert "poll multiplex ui returned" in content

    async def test_session_poll_log_includes_session_id(self, api_integration_env, tmp_path):
        """Session poll log line includes the session_id."""
        configure_logging(debug_polling=True, log_dir=str(tmp_path))

        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        webui.session_queues[sid].append({"type": "msg"})

        resp = await client.post(
            "/api/poll", json={"timeout": 0, "sessions": {sid: {"since": 0}}}
        )
        assert resp.status_code == 200
        assert len(resp.json()["sessions"][sid]["events"]) == 1

        poll_log = Path(tmp_path) / "polling.log"
        assert poll_log.exists()
        content = poll_log.read_text()
        assert "poll multiplex session" in content
        assert sid in content
