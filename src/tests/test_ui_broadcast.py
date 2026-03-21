"""
Tests for UI event broadcasting helpers (issue #859).

Covers:
- LegionSystem.broadcast_ui_event()
- ClaudeWebUI._broadcast_project_updated()
- ClaudeWebUI._broadcast_project_deleted()
- ClaudeWebUI._broadcast_state_change()
- ClaudeWebUI._broadcast_server_restarting()
- ClaudeWebUI._broadcast_mcp_oauth_complete()
- Resilience when ui_queue.append() raises
"""

import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# LegionSystem.broadcast_ui_event()
# ---------------------------------------------------------------------------


def test_broadcast_ui_event_appends_to_queue():
    """broadcast_ui_event() appends the event to ui_queue when set."""
    from src.legion_system import LegionSystem

    queue = []
    system = LegionSystem(
        session_coordinator=Mock(),
        data_storage_manager=Mock(),
        template_manager=Mock(),
        ui_queue=queue,
    )

    event = {"type": "project_updated", "data": {"project": {"project_id": "p1"}}}
    system.broadcast_ui_event(event)

    assert queue == [event]


def test_broadcast_ui_event_safe_when_ui_queue_is_none():
    """broadcast_ui_event() does not raise when ui_queue is None."""
    from src.legion_system import LegionSystem

    system = LegionSystem(
        session_coordinator=Mock(),
        data_storage_manager=Mock(),
        template_manager=Mock(),
        ui_queue=None,
    )

    # Must not raise
    system.broadcast_ui_event({"type": "project_updated", "data": {}})


# ---------------------------------------------------------------------------
# ClaudeWebUI broadcast helpers
# ---------------------------------------------------------------------------


def _make_webui(tmp_path: Path):
    """Create a minimal ClaudeWebUI instance for unit testing the broadcast helpers."""
    from src.web_server import ClaudeWebUI

    webui = ClaudeWebUI(data_dir=tmp_path)
    # Replace ui_queue with a plain list so we can inspect appended events
    webui.ui_queue = []
    return webui


def test_broadcast_project_updated_event_shape(tmp_path):
    """_broadcast_project_updated() appends a correctly-shaped project_updated event."""
    webui = _make_webui(tmp_path)
    project = {"project_id": "proj-1", "name": "My Project"}

    webui._broadcast_project_updated(project)

    assert len(webui.ui_queue) == 1
    event = webui.ui_queue[0]
    assert event["type"] == "project_updated"
    assert event["data"]["project"] == project


def test_broadcast_project_deleted_event_shape(tmp_path):
    """_broadcast_project_deleted() appends a correctly-shaped project_deleted event."""
    webui = _make_webui(tmp_path)

    webui._broadcast_project_deleted("proj-2")

    assert len(webui.ui_queue) == 1
    event = webui.ui_queue[0]
    assert event["type"] == "project_deleted"
    assert event["data"]["project_id"] == "proj-2"


def test_broadcast_state_change_event_shape(tmp_path):
    """_broadcast_state_change() appends a correctly-shaped state_change event."""
    webui = _make_webui(tmp_path)
    session_dict = {"state": "active", "session_id": "sess-1"}

    webui._broadcast_state_change("sess-1", session_dict, "2026-01-01T00:00:00")

    assert len(webui.ui_queue) == 1
    event = webui.ui_queue[0]
    assert event["type"] == "state_change"
    assert event["data"]["session_id"] == "sess-1"
    assert event["data"]["session"] == session_dict
    assert event["data"]["timestamp"] == "2026-01-01T00:00:00"


def test_broadcast_server_restarting_event_shape(tmp_path):
    """_broadcast_server_restarting() appends a correctly-shaped server_restarting event."""
    webui = _make_webui(tmp_path)

    webui._broadcast_server_restarting("pull ok", "sync ok")

    assert len(webui.ui_queue) == 1
    event = webui.ui_queue[0]
    assert event["type"] == "server_restarting"
    assert event["pull_output"] == "pull ok"
    assert event["sync_output"] == "sync ok"
    assert "timestamp" in event


def test_broadcast_mcp_oauth_complete_event_shape(tmp_path):
    """_broadcast_mcp_oauth_complete() appends a correctly-shaped mcp_oauth_complete event."""
    webui = _make_webui(tmp_path)

    webui._broadcast_mcp_oauth_complete("my-server")

    assert len(webui.ui_queue) == 1
    event = webui.ui_queue[0]
    assert event["type"] == "mcp_oauth_complete"
    assert event["server_id"] == "my-server"


# ---------------------------------------------------------------------------
# Resilience: helpers must not propagate exceptions from ui_queue.append()
# ---------------------------------------------------------------------------


def test_broadcast_helpers_resilient_to_queue_append_error(tmp_path):
    """Each broadcast helper swallows exceptions from ui_queue.append()."""
    webui = _make_webui(tmp_path)

    # Replace ui_queue with a mock that raises on append
    bad_queue = MagicMock()
    bad_queue.append.side_effect = RuntimeError("queue full")
    webui.ui_queue = bad_queue

    # None of these should propagate the RuntimeError
    webui._broadcast_project_updated({"project_id": "p"})
    webui._broadcast_project_deleted("p")
    webui._broadcast_state_change("s", {}, None)
    webui._broadcast_server_restarting("", "")
    webui._broadcast_mcp_oauth_complete("srv")
