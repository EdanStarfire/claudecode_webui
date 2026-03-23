"""
Regression tests for issue #938.

Context window percentage was wrong after server restart because
_session_models cache was empty — the model was never looked up from
session_manager, so get_context_window("") returned the fallback and
produced an incorrect percentage.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parsed_message(model_type_str: str = "result", usage: dict | None = None):
    """Build a minimal ParsedMessage-like mock with result type and usage metadata."""
    msg = MagicMock()
    type_mock = MagicMock()
    type_mock.value = model_type_str
    msg.type = type_mock
    msg.metadata = {"usage": usage or {"input_tokens": 1000}}
    return msg


def _make_webui(tmp_path: Path):
    """Create a minimal ClaudeWebUI with mocked coordinator and message processor."""
    from src.web_server import ClaudeWebUI

    webui = ClaudeWebUI(data_dir=tmp_path)

    # Stub message processor so prepare_for_websocket doesn't blow up
    processor = MagicMock()
    processor.prepare_for_websocket.return_value = {"type": "result"}
    webui._message_processor = processor

    # Stub _emit_tool_call_updates so the callback doesn't need a full coordinator
    webui._emit_tool_call_updates = AsyncMock()

    return webui


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_938_context_pct_after_restart(tmp_path):
    """
    After a server restart _session_models is empty.  When the first result
    message arrives, the callback must lazily fetch the model from
    session_manager and compute a non-zero context percentage.
    """
    from src.web_server import ClaudeWebUI

    session_id = "sess-938"
    model_name = "claude-opus-4-6"

    webui = _make_webui(tmp_path)

    # Simulate the cache being empty (fresh server restart)
    assert session_id not in webui._session_models

    # Mock coordinator → session_manager → get_session_info
    session_info = MagicMock()
    session_info.model = model_name
    webui.coordinator = MagicMock()
    webui.coordinator.session_manager.get_session_info = AsyncMock(return_value=session_info)

    # Register the session queue
    webui.session_queues[session_id] = []

    # Build and invoke the callback with a result message carrying token usage
    callback = webui._create_message_callback(session_id)
    parsed = _make_parsed_message(usage={"input_tokens": 5000})
    await callback(session_id, parsed)

    # Model cache should now be populated
    assert webui._session_models.get(session_id) == model_name

    # A context_update event should have been appended to the queue
    context_events = [e for e in webui.session_queues[session_id] if e.get("type") == "context_update"]
    assert context_events, "No context_update event emitted"

    event = context_events[0]
    assert event["input_tokens"] == 5000
    assert event["context_window"] > 0
    assert event["context_pct"] > 0


@pytest.mark.asyncio
async def test_issue_938_context_pct_uses_cached_model(tmp_path):
    """
    When _session_models is already populated (normal flow / no restart),
    the coordinator must NOT be called and the percentage must still be correct.
    """
    session_id = "sess-938b"
    model_name = "claude-sonnet-4-6"

    webui = _make_webui(tmp_path)
    webui._session_models[session_id] = model_name

    webui.coordinator = MagicMock()
    webui.coordinator.session_manager.get_session_info = AsyncMock()

    webui.session_queues[session_id] = []

    callback = webui._create_message_callback(session_id)
    parsed = _make_parsed_message(usage={"input_tokens": 2000})
    await callback(session_id, parsed)

    # get_session_info must NOT have been called — model was already cached
    webui.coordinator.session_manager.get_session_info.assert_not_called()

    context_events = [e for e in webui.session_queues[session_id] if e.get("type") == "context_update"]
    assert context_events
    assert context_events[0]["context_pct"] > 0
