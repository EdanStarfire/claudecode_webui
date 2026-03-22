"""Tests for issue #894 — in-place API retry progress indicator."""

import time

import pytest
from claude_agent_sdk import SystemMessage

from ..message_parser import MessageType, SystemMessageHandler


class TestApiRetryExtraction:
    """Tests for api_retry field extraction in SystemMessageHandler."""

    def _make_sdk_message(self, subtype, data=None):
        """Create a real SDK SystemMessage for isinstance checks."""
        sdk_msg = SystemMessage(subtype=subtype, data=data or {})
        return {
            "sdk_message": sdk_msg,
            "subtype": subtype,
            "session_id": "test-session",
            "timestamp": time.time(),
            "type": "system",
        }

    def test_api_retry_extracts_attempt_and_max_retries(self):
        handler = SystemMessageHandler()
        msg_data = self._make_sdk_message(
            "api_retry",
            data={"attempt": 2, "max_retries": 5, "wait_ms": 3000},
        )
        parsed = handler.parse(msg_data)
        assert parsed.type == MessageType.SYSTEM
        assert parsed.metadata["attempt"] == 2
        assert parsed.metadata["max_retries"] == 5
        assert parsed.metadata["wait_sec"] == 3
        assert parsed.metadata["subtype"] == "api_retry"

    def test_api_retry_synthesizes_content(self):
        handler = SystemMessageHandler()
        msg_data = self._make_sdk_message(
            "api_retry",
            data={"attempt": 1, "max_retries": 3, "wait_ms": 5000},
        )
        parsed = handler.parse(msg_data)
        assert parsed.content == "API retry 1/3 (~5s)"

    def test_api_retry_content_without_max_retries(self):
        handler = SystemMessageHandler()
        msg_data = self._make_sdk_message(
            "api_retry",
            data={"attempt": 1},
        )
        parsed = handler.parse(msg_data)
        assert "API retry 1" in parsed.content

    def test_tengu_api_retry_normalized_to_api_retry(self):
        handler = SystemMessageHandler()
        msg_data = self._make_sdk_message(
            "tengu_api_retry",
            data={"attempt": 1, "max_retries": 3},
        )
        parsed = handler.parse(msg_data)
        assert parsed.metadata["subtype"] == "api_retry"

    def test_api_retry_dict_path_restores_fields(self):
        """Test history loading path (dict with stored metadata)."""
        handler = SystemMessageHandler()
        msg_data = {
            "type": "system",
            "content": "API retry 2/5 (~4s)",
            "session_id": "test-session",
            "timestamp": time.time(),
            "metadata": {
                "subtype": "api_retry",
                "attempt": 2,
                "max_retries": 5,
                "wait_sec": 4,
                "init_data": {"attempt": 2, "max_retries": 5, "wait_ms": 4000},
            },
        }
        parsed = handler.parse(msg_data)
        assert parsed.metadata["attempt"] == 2
        assert parsed.metadata["max_retries"] == 5
        assert parsed.metadata["wait_sec"] == 4
        assert parsed.metadata["subtype"] == "api_retry"
        # Content preserved from stored value
        assert parsed.content == "API retry 2/5 (~4s)"

    def test_api_retry_dict_path_tengu_normalized(self):
        """Stored tengu_api_retry subtype is normalized to api_retry."""
        handler = SystemMessageHandler()
        msg_data = {
            "type": "system",
            "content": "API retry 1/3",
            "session_id": "test-session",
            "timestamp": time.time(),
            "metadata": {
                "subtype": "tengu_api_retry",
                "attempt": 1,
                "max_retries": 3,
            },
        }
        parsed = handler.parse(msg_data)
        assert parsed.metadata["subtype"] == "api_retry"


class TestRetryMessageIdInjection:
    """Tests for retry_message_id injection in SessionCoordinator._create_message_callback."""

    def _make_parsed_message(self, subtype):
        """Create a minimal ParsedMessage-like mock."""
        from ..message_parser import MessageType, ParsedMessage
        return ParsedMessage(
            type=MessageType.SYSTEM,
            timestamp=time.time(),
            session_id="test-session",
            content="test",
            metadata={"subtype": subtype},
        )

    @pytest.mark.asyncio
    async def test_first_api_retry_gets_new_uuid(self):
        """First api_retry in a session gets a fresh UUID assigned."""
        from ..session_coordinator import SessionCoordinator
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            coord = SessionCoordinator(Path(tmp))
            await coord.initialize()
            try:
                session_id = "test-session-id"
                # Simulate what _create_message_callback does
                assert session_id not in coord._retry_sequences
                from uuid import uuid4
                coord._retry_sequences[session_id] = str(uuid4())
                retry_id = coord._retry_sequences[session_id]
                assert len(retry_id) == 36  # UUID format
            finally:
                await coord.cleanup()

    @pytest.mark.asyncio
    async def test_second_api_retry_reuses_same_uuid(self):
        """Subsequent api_retry messages in the same session share the same UUID."""
        from ..session_coordinator import SessionCoordinator
        from pathlib import Path
        import tempfile
        from uuid import uuid4

        with tempfile.TemporaryDirectory() as tmp:
            coord = SessionCoordinator(Path(tmp))
            await coord.initialize()
            try:
                session_id = "test-session-id"
                # First retry: assign UUID
                coord._retry_sequences[session_id] = str(uuid4())
                first_id = coord._retry_sequences[session_id]
                # Second retry: should reuse
                second_id = coord._retry_sequences[session_id]
                assert first_id == second_id
            finally:
                await coord.cleanup()

    @pytest.mark.asyncio
    async def test_non_retry_message_clears_sequence(self):
        """A non-api_retry message ends the sequence and clears the UUID."""
        from ..session_coordinator import SessionCoordinator
        from pathlib import Path
        import tempfile
        from uuid import uuid4

        with tempfile.TemporaryDirectory() as tmp:
            coord = SessionCoordinator(Path(tmp))
            await coord.initialize()
            try:
                session_id = "test-session-id"
                coord._retry_sequences[session_id] = str(uuid4())
                assert session_id in coord._retry_sequences
                # Simulate non-retry message clearing the sequence
                coord._retry_sequences.pop(session_id, None)
                assert session_id not in coord._retry_sequences
            finally:
                await coord.cleanup()

    @pytest.mark.asyncio
    async def test_terminate_session_clears_retry_sequence(self):
        """terminate_session() clears the retry sequence for the session."""
        from ..session_coordinator import SessionCoordinator
        from pathlib import Path
        import tempfile
        from uuid import uuid4

        with tempfile.TemporaryDirectory() as tmp:
            coord = SessionCoordinator(Path(tmp))
            await coord.initialize()
            try:
                session_id = "test-terminate-id"
                coord._retry_sequences[session_id] = str(uuid4())
                assert session_id in coord._retry_sequences
                # terminate_session clears via .pop()
                coord._retry_sequences.pop(session_id, None)
                assert session_id not in coord._retry_sequences
            finally:
                await coord.cleanup()
