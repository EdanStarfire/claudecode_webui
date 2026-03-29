"""
Regression tests for issue #952.

Context window usage is now sourced from ClaudeSDKClient.get_context_usage()
instead of being inferred from stop-message usage metadata. This removes the
halving workaround (#944) and the _session_models cache (#938).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result_parsed_message():
    """Build a ParsedMessage-like mock with type='result'."""
    msg = MagicMock()
    type_mock = MagicMock()
    type_mock.value = "result"
    msg.type = type_mock
    msg.metadata = {}
    return msg


def _make_non_result_parsed_message():
    """Build a ParsedMessage-like mock with type='assistant'."""
    msg = MagicMock()
    type_mock = MagicMock()
    type_mock.value = "assistant"
    msg.type = type_mock
    msg.metadata = {}
    return msg


def _make_webui(tmp_path):
    from src.web_server import ClaudeWebUI

    webui = ClaudeWebUI(data_dir=tmp_path)
    processor = MagicMock()
    processor.prepare_for_websocket.return_value = {"type": "result"}
    webui._message_processor = processor
    webui._emit_tool_call_updates = AsyncMock()
    return webui


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_952_context_update_emitted_with_sdk_data(tmp_path):
    """
    When get_context_usage() returns valid data, a context_update event must
    be appended to the session queue with the SDK-provided values.
    """
    session_id = "sess-952a"

    webui = _make_webui(tmp_path)
    webui.coordinator = MagicMock()
    webui.coordinator.get_context_usage = AsyncMock(return_value={
        "totalTokens": 50_000,
        "maxTokens": 200_000,
        "percentage": 25.0,
        "model": "claude-sonnet-4-6",
    })
    webui.session_queues[session_id] = []

    callback = webui._create_message_callback(session_id)
    parsed = _make_result_parsed_message()
    await callback(session_id, parsed)

    context_events = [e for e in webui.session_queues[session_id] if e.get("type") == "context_update"]
    assert context_events, "No context_update event emitted"

    event = context_events[0]
    assert event["input_tokens"] == 50_000
    assert event["context_window"] == 200_000
    assert event["context_pct"] == 25.0
    assert event["session_id"] == session_id
    assert "timestamp" in event


@pytest.mark.asyncio
async def test_issue_952_no_context_update_when_sdk_returns_empty(tmp_path):
    """
    When get_context_usage() returns {}, no context_update event must be emitted.
    """
    session_id = "sess-952b"

    webui = _make_webui(tmp_path)
    webui.coordinator = MagicMock()
    webui.coordinator.get_context_usage = AsyncMock(return_value={})
    webui.session_queues[session_id] = []

    callback = webui._create_message_callback(session_id)
    parsed = _make_result_parsed_message()
    await callback(session_id, parsed)

    context_events = [e for e in webui.session_queues[session_id] if e.get("type") == "context_update"]
    assert not context_events, "context_update must not be emitted when SDK returns empty"


@pytest.mark.asyncio
async def test_issue_952_no_context_update_on_non_result_message(tmp_path):
    """
    context_update must not be emitted for non-result messages (e.g., assistant).
    """
    session_id = "sess-952c"

    webui = _make_webui(tmp_path)
    webui.coordinator = MagicMock()
    webui.coordinator.get_context_usage = AsyncMock(return_value={
        "totalTokens": 10_000,
        "maxTokens": 200_000,
        "percentage": 5.0,
    })

    # Override prepare_for_websocket to return non-result type
    webui._message_processor.prepare_for_websocket.return_value = {"type": "assistant"}
    webui.session_queues[session_id] = []

    callback = webui._create_message_callback(session_id)
    parsed = _make_non_result_parsed_message()
    await callback(session_id, parsed)

    context_events = [e for e in webui.session_queues[session_id] if e.get("type") == "context_update"]
    assert not context_events, "context_update must not be emitted for non-result messages"


@pytest.mark.asyncio
async def test_issue_952_context_pct_is_rounded(tmp_path):
    """
    context_pct must be rounded to 1 decimal place.
    """
    session_id = "sess-952d"

    webui = _make_webui(tmp_path)
    webui.coordinator = MagicMock()
    webui.coordinator.get_context_usage = AsyncMock(return_value={
        "totalTokens": 33_333,
        "maxTokens": 200_000,
        "percentage": 16.6665,
    })
    webui.session_queues[session_id] = []

    callback = webui._create_message_callback(session_id)
    parsed = _make_result_parsed_message()
    await callback(session_id, parsed)

    context_events = [e for e in webui.session_queues[session_id] if e.get("type") == "context_update"]
    assert context_events
    assert context_events[0]["context_pct"] == 16.7
