"""
Regression tests for issue #1972.

System lifecycle messages (client_launched, interrupt, session_failed) previously
reached the live callback without a `message_id`, tripping web_server.py's
"no record_id" fail-loud error on every session launch/interrupt/restart. Root
cause: `SessionCoordinator._store_processed_message()` stamped `message_id` only
onto an internal storage-format copy of the message and never propagated it back
onto the caller's original dict — the same dict subsequently handed to the live
callback.

Covers:
- The three coordinator-side lifecycle senders now share one `message_id` between
  their live-delivered and stored copies (Story 1 acceptance criteria).
- `_store_processed_message` mints an id even when no storage manager is
  registered for the session, so the live dict is never identity-less.
- The "no record_id" safeguard still fires for a genuinely identity-less message
  (Story 2 acceptance criteria / Test Scenario 4).

Superseded by issue #2007: `interrupt_success` was removed entirely (both the
reachable direct-interrupt send site and a second, confirmed-dead queue-driven
send site), rather than being left permanently unstored per this file's original
"confirmed Option A" decision. Deletion satisfies the same constraint Option A
was solving for (avoid duplicating the already-stored `interrupt` message on
every interrupt) by a different route: the stored `interrupt` message becomes
the sole live+reload confirmation. See `TestIssue1972InterruptSuccessRecordIdentity`
in `test_claude_sdk.py` (removed by #2007 — it tested message constructions that
no longer exist).
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.message_parser import MessageParser, MessageProcessor
from backend.session_coordinator import SessionCoordinator


def _make_webui(tmp_path):
    from backend.web_server import BackendApp

    webui = BackendApp(data_dir=tmp_path)
    webui._message_processor = MessageProcessor(MessageParser())
    return webui


def _wire_coordinator_to_webui(tmp_path, session_id: str):
    """Wire a real SessionCoordinator to a real BackendApp callback, exactly as
    production connects them, with a mocked storage manager for the session."""
    coord = SessionCoordinator(data_dir=tmp_path)

    webui = _make_webui(tmp_path)
    webui.session_queues[session_id] = []
    webui.coordinator = MagicMock()

    webui_callback = webui._create_message_callback(session_id)
    coord.add_message_callback(session_id, webui_callback)

    async def _append_message(data):
        # Mirrors DataStorageManager.append_message()'s real id-stamping behavior
        # (backend/data_storage.py) without touching the filesystem.
        data.setdefault("message_id", str(uuid.uuid4()))

    storage_manager = MagicMock()
    storage_manager.append_message = AsyncMock(side_effect=_append_message)
    coord._storage_managers[session_id] = storage_manager

    return coord, webui, storage_manager


@pytest.mark.asyncio
async def test_client_launched_live_id_matches_stored():
    session_id = "sess-1972-launched"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        coord, webui, storage_manager = _wire_coordinator_to_webui(tmp_path, session_id)

        await coord._send_client_launched_message(session_id)

        queue = webui.session_queues[session_id]
        assert len(queue) == 1
        live_id = queue[0]["data"].get("message_id")
        stored_id = storage_manager.append_message.call_args[0][0].get("message_id")

        assert live_id, "Live client_launched message must carry a message_id"
        assert live_id == stored_id, "Live and stored message_id must be identical"


@pytest.mark.asyncio
async def test_interrupt_message_live_id_matches_stored():
    session_id = "sess-1972-interrupt"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        coord, webui, storage_manager = _wire_coordinator_to_webui(tmp_path, session_id)

        await coord._send_interrupt_message(session_id)

        queue = webui.session_queues[session_id]
        assert len(queue) == 1
        live_id = queue[0]["data"].get("message_id")
        stored_id = storage_manager.append_message.call_args[0][0].get("message_id")

        assert live_id, "Live interrupt message must carry a message_id"
        assert live_id == stored_id, "Live and stored message_id must be identical"


@pytest.mark.asyncio
async def test_session_failure_live_id_matches_stored():
    session_id = "sess-1972-failure"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        coord, webui, storage_manager = _wire_coordinator_to_webui(tmp_path, session_id)

        await coord._send_session_failure_message(session_id, "boom", raw_error="boom traceback")

        queue = webui.session_queues[session_id]
        assert len(queue) == 1
        live_id = queue[0]["data"].get("message_id")
        stored_id = storage_manager.append_message.call_args[0][0].get("message_id")

        assert live_id, "Live session_failed message must carry a message_id"
        assert live_id == stored_id, "Live and stored message_id must be identical"


@pytest.mark.asyncio
async def test_lifecycle_message_stamped_without_storage_manager():
    """Mirrors test_issue_1958_stamp_timing_unconditional_without_storage_manager:
    when no storage manager is registered for the session, _store_processed_message
    must still mint an id onto the caller's dict rather than leaving it identity-less."""
    session_id = "sess-1972-no-storage"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        coord = SessionCoordinator(data_dir=tmp_path)
        assert session_id not in coord._storage_managers

        webui = _make_webui(tmp_path)
        webui.session_queues[session_id] = []
        webui.coordinator = MagicMock()
        coord.add_message_callback(session_id, webui._create_message_callback(session_id))

        await coord._send_client_launched_message(session_id)

        queue = webui.session_queues[session_id]
        assert len(queue) == 1
        assert queue[0]["data"].get("message_id"), (
            "message_id must be minted even when no storage manager is registered"
        )


@pytest.mark.asyncio
async def test_safeguard_still_fires_for_genuinely_identity_less_message(caplog):
    """Test Scenario 4 / Story 2: a message that bypasses every sender and reaches
    the live callback with no message_id at all must still trip the "no record_id"
    error — the fix must not silently swallow genuine anomalies."""
    session_id = "sess-1972-genuine-anomaly"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        webui = _make_webui(tmp_path)
        webui.session_queues[session_id] = []
        webui.coordinator = MagicMock()

        parsed_message = MagicMock()
        parsed_message.type = MagicMock(value="system")
        parsed_message.record_id = None
        parsed_message.turn_id = None
        parsed_message.metadata = {}

        callback = webui._create_message_callback(session_id)
        with caplog.at_level("ERROR", logger="backend.web_server"):
            await callback(session_id, parsed_message)

        assert any("record_id" in r.message for r in caplog.records), (
            "A genuinely identity-less live message must still produce a logged error"
        )
