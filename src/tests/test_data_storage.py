"""Tests for data_storage module."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from ..data_storage import DataStorageManager


@pytest.fixture
async def temp_storage_manager():
    """Create a temporary data storage manager for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session_dir = Path(temp_dir) / "test-session"
        manager = DataStorageManager(session_dir)
        await manager.initialize()
        yield manager


@pytest.fixture
def sample_message():
    """Sample message data for testing."""
    return {
        "type": "user",
        "content": "Hello, Claude!",
        "session_id": "test-session-123"
    }


@pytest.fixture
def sample_messages():
    """Multiple sample messages for testing."""
    return [
        {
            "type": "user",
            "content": "First message",
            "session_id": "test-session-123"
        },
        {
            "type": "assistant",
            "content": "First response",
            "session_id": "test-session-123"
        },
        {
            "type": "user",
            "content": "Second message",
            "session_id": "test-session-123"
        }
    ]


class TestDataStorageManager:
    """Test DataStorageManager functionality."""

    @pytest.mark.asyncio
    async def test_initialization(self, temp_storage_manager):
        """Test storage manager initialization."""
        manager = temp_storage_manager

        # Check that directories and files were created
        assert manager.session_dir.exists()
        assert manager.messages_file.exists()
        # Integrity file removed - no longer used

    @pytest.mark.asyncio
    async def test_append_single_message(self, temp_storage_manager, sample_message):
        """Test appending a single message."""
        manager = temp_storage_manager

        await manager.append_message(sample_message)

        # Read back the message
        messages = await manager.read_messages()
        assert len(messages) == 1
        assert messages[0]["type"] == sample_message["type"]
        assert messages[0]["content"] == sample_message["content"]
        assert messages[0]["session_id"] == sample_message["session_id"]
        assert "timestamp" in messages[0]

    @pytest.mark.asyncio
    async def test_append_multiple_messages(self, temp_storage_manager, sample_messages):
        """Test appending multiple messages."""
        manager = temp_storage_manager

        for message in sample_messages:
            await manager.append_message(message)

        # Read back all messages
        messages = await manager.read_messages()
        assert len(messages) == len(sample_messages)

        for i, original in enumerate(sample_messages):
            assert messages[i]["type"] == original["type"]
            assert messages[i]["content"] == original["content"]

    @pytest.mark.asyncio
    async def test_read_messages_with_limit(self, temp_storage_manager, sample_messages):
        """Test reading messages with limit."""
        manager = temp_storage_manager

        # Add all messages
        for message in sample_messages:
            await manager.append_message(message)

        # Read with limit
        messages = await manager.read_messages(limit=2)
        assert len(messages) == 2

    @pytest.mark.asyncio
    async def test_read_messages_with_offset(self, temp_storage_manager, sample_messages):
        """Test reading messages with offset."""
        manager = temp_storage_manager

        # Add all messages
        for message in sample_messages:
            await manager.append_message(message)

        # Read with offset
        messages = await manager.read_messages(offset=1, limit=2)
        assert len(messages) == 2
        assert messages[0]["content"] == "First response"

    @pytest.mark.asyncio
    async def test_get_message_count(self, temp_storage_manager, sample_messages):
        """Test getting message count."""
        manager = temp_storage_manager

        # Initially should be 0
        count = await manager.get_message_count()
        assert count == 0

        # Add messages and check count
        for message in sample_messages:
            await manager.append_message(message)

        count = await manager.get_message_count()
        assert count == len(sample_messages)

    @pytest.mark.asyncio
    async def test_missing_file_handling(self, temp_storage_manager):
        """Test that reading messages and counting handles missing files gracefully."""
        manager = temp_storage_manager

        # Remove the messages file
        manager.messages_file.unlink()

        # Should handle missing file gracefully
        messages = await manager.read_messages()
        assert len(messages) == 0

        count = await manager.get_message_count()
        assert count == 0

    @pytest.mark.asyncio
    async def test_jsonl_format_integrity(self, temp_storage_manager, sample_messages):
        """Test that messages are stored in valid JSONL format."""
        manager = temp_storage_manager

        # Add messages
        for message in sample_messages:
            await manager.append_message(message)

        # Manually read the JSONL file
        with open(manager.messages_file) as f:
            lines = f.readlines()

        assert len(lines) == len(sample_messages)

        # Each line should be valid JSON
        for line in lines:
            line = line.strip()
            if line:  # Skip empty lines
                parsed = json.loads(line)
                assert isinstance(parsed, dict)
                assert "timestamp" in parsed

    @pytest.mark.asyncio
    async def test_unicode_content_handling(self, temp_storage_manager):
        """Test handling of Unicode content in messages."""
        manager = temp_storage_manager

        unicode_message = {
            "type": "user",
            "content": "Hello 世界! 🌍 Emoji test ñáéíóú",
            "session_id": "test-unicode"
        }

        await manager.append_message(unicode_message)

        # Read back and verify Unicode is preserved
        messages = await manager.read_messages()
        assert len(messages) == 1
        assert messages[0]["content"] == unicode_message["content"]

    @pytest.mark.asyncio
    async def test_concurrent_message_appends(self, temp_storage_manager):
        """Test concurrent message appends."""
        manager = temp_storage_manager

        # Create multiple messages to append concurrently
        messages = [
            {"type": "user", "content": f"Message {i}", "session_id": "test"}
            for i in range(10)
        ]

        # Append all messages concurrently
        tasks = [manager.append_message(msg) for msg in messages]
        await asyncio.gather(*tasks)

        # Verify all messages were stored
        stored_messages = await manager.read_messages()
        assert len(stored_messages) == 10

    @pytest.mark.asyncio
    async def test_large_message_handling(self, temp_storage_manager):
        """Test handling of large messages."""
        manager = temp_storage_manager

        # Create a large message
        large_content = "A" * 10000  # 10KB message
        large_message = {
            "type": "user",
            "content": large_content,
            "session_id": "test-large"
        }

        await manager.append_message(large_message)

        # Verify large message is stored and retrieved correctly
        messages = await manager.read_messages()
        assert len(messages) == 1
        assert len(messages[0]["content"]) == 10000
        assert messages[0]["content"] == large_content

    @pytest.mark.asyncio
    async def test_cleanup_functionality(self, temp_storage_manager, sample_message):
        """Test cleanup functionality."""
        manager = temp_storage_manager

        # Add a message
        await manager.append_message(sample_message)

        # Run cleanup
        await manager.cleanup()

        # Verify integrity file is updated
        # Integrity file removed - no longer used

    @pytest.mark.asyncio
    async def test_empty_file_handling(self, temp_storage_manager):
        """Test handling of empty files."""
        manager = temp_storage_manager

        # Read from empty files (should not cause errors)
        messages = await manager.read_messages()
        assert len(messages) == 0

        count = await manager.get_message_count()
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_timestamp_auto_generation(self, temp_storage_manager):
        """Test that timestamps are automatically added to messages."""
        manager = temp_storage_manager

        # Message without timestamp
        message = {
            "type": "user",
            "content": "Test message",
            "session_id": "test"
        }

        await manager.append_message(message)

        # Verify timestamp was added
        messages = await manager.read_messages()
        assert len(messages) == 1
        assert "timestamp" in messages[0]

        # Verify timestamp is a Unix timestamp (float)
        timestamp = messages[0]["timestamp"]
        assert isinstance(timestamp, (int, float))
        assert timestamp > 0  # Should be positive Unix timestamp
