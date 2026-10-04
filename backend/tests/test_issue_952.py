"""
Regression tests for issue #952.

Context window usage is now sourced from ClaudeSDKClient.get_context_usage()
instead of being inferred from stop-message usage metadata. This removes the
halving workaround (#944) and the _session_models cache (#938).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.event_queue import EventQueue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result_parsed_message():
    """Issue #2084 (stage 3-B, §4): the callback now receives the canonical
    MessageRecord.to_dict() shape directly, not a ParsedMessage object."""
    return {"type": "result", "metadata": {}}


def _make_non_result_parsed_message():
    return {"type": "assistant", "metadata": {}}


def _make_webui(tmp_path):
    from backend.web_server import BackendApp

    webui = BackendApp(data_dir=tmp_path)
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
    webui.session_queues[session_id] = EventQueue()

    callback = webui._create_message_callback(session_id)
    parsed = _make_result_parsed_message()
    await callback(session_id, parsed)

    events, _, _ = webui.session_queues[session_id].events_since(0)
    context_events = [e for e in events if e.get("type") == "context_update"]
    assert context_events, "No context_update event emitted"

    event = context_events[0]
    assert event["data"]["input_tokens"] == 50_000
    assert event["data"]["context_window"] == 200_000
    assert event["data"]["context_pct"] == 25.0
    assert event["data"]["session_id"] == session_id
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
    webui.session_queues[session_id] = EventQueue()

    callback = webui._create_message_callback(session_id)
    parsed = _make_result_parsed_message()
    await callback(session_id, parsed)

    events, _, _ = webui.session_queues[session_id].events_since(0)
    context_events = [e for e in events if e.get("type") == "context_update"]
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
    webui.session_queues[session_id] = EventQueue()

    callback = webui._create_message_callback(session_id)
    parsed = _make_non_result_parsed_message()
    await callback(session_id, parsed)

    events, _, _ = webui.session_queues[session_id].events_since(0)
    context_events = [e for e in events if e.get("type") == "context_update"]
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
    webui.session_queues[session_id] = EventQueue()

    callback = webui._create_message_callback(session_id)
    parsed = _make_result_parsed_message()
    await callback(session_id, parsed)

    events, _, _ = webui.session_queues[session_id].events_since(0)
    context_events = [e for e in events if e.get("type") == "context_update"]
    assert context_events
    assert context_events[0]["data"]["context_pct"] == 16.7
