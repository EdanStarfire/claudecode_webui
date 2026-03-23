"""
Regression tests for issue #944.

SDK double-counts tokens in usage reporting, causing context % to read ~2x
the actual value. The workaround halves input_tokens before computing the
percentage. This test verifies the halved output.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers (shared with test_issue_938 pattern)
# ---------------------------------------------------------------------------


def _make_parsed_message(model_type_str: str = "result", usage: dict | None = None):
    msg = MagicMock()
    type_mock = MagicMock()
    type_mock.value = model_type_str
    msg.type = type_mock
    msg.metadata = {"usage": usage or {"input_tokens": 1000}}
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
async def test_issue_944_context_pct_halved(tmp_path):
    """
    context_pct must equal round(((input_tokens / 2) / context_window) * 100, 1).

    With input_tokens=20_000 and a known context_window, the reported percentage
    must be exactly half of what a naive (un-halved) calculation would produce.
    """
    from src.model_limits import get_context_window

    session_id = "sess-944"
    model_name = "claude-sonnet-4-6"
    input_tokens = 20_000

    webui = _make_webui(tmp_path)
    webui._session_models[session_id] = model_name

    webui.coordinator = MagicMock()
    webui.coordinator.session_manager.get_session_info = AsyncMock()

    webui.session_queues[session_id] = []

    callback = webui._create_message_callback(session_id)
    parsed = _make_parsed_message(usage={"input_tokens": input_tokens})
    await callback(session_id, parsed)

    context_events = [e for e in webui.session_queues[session_id] if e.get("type") == "context_update"]
    assert context_events, "No context_update event emitted"

    event = context_events[0]
    context_window = get_context_window(model_name)

    expected_halved = round(((input_tokens / 2) / context_window) * 100, 1)
    expected_naive = round((input_tokens / context_window) * 100, 1)

    assert event["context_pct"] == expected_halved, (
        f"Expected halved pct {expected_halved}, got {event['context_pct']}"
    )
    assert event["context_pct"] < expected_naive, (
        "context_pct must be less than the un-halved value"
    )
