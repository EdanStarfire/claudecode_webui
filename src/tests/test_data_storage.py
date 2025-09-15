"""Tests for data_storage module."""

import asyncio
import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime, timezone

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
        assert manager.history_file.exists()
        assert manager.integrity_file.exists()

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
    async def test_write_and_read_history(self, temp_storage_manager):
        """Test writing and reading command history."""
        manager = temp_storage_manager

        history_data = [
            {
                "command": "ls -la",
                "timestamp": "2024-01-01T10:00:00Z"
            },
            {
                "command": "cd /home",
                "timestamp": "2024-01-01T10:01:00Z"
            }
        ]

        await manager.write_history(history_data)

        # Read back history
        read_history = await manager.read_history()
        assert len(read_history) == 2
        assert read_history[0]["command"] == "ls -la"
        assert read_history[1]["command"] == "cd /home"

    @pytest.mark.asyncio
    async def test_append_history_item(self, temp_storage_manager):
        """Test appending individual history items."""
        manager = temp_storage_manager

        history_item = {
            "command": "git status",
            "result": "On branch main"
        }

        await manager.append_history_item(history_item)

        history = await manager.read_history()
        assert len(history) == 1
        assert history[0]["command"] == "git status"
        assert "timestamp" in history[0]

    @pytest.mark.asyncio
    async def test_append_history_item_with_limit(self, temp_storage_manager):
        """Test that history items are limited to prevent unbounded growth."""
        manager = temp_storage_manager

        # Add more than 1000 items (the limit)
        for i in range(1005):
            await manager.append_history_item({
                "command": f"command_{i}",
                "index": i
            })

        history = await manager.read_history()
        # Should be limited to last 1000 items
        assert len(history) == 1000
        assert history[0]["index"] == 5  # First item should be index 5 (items 0-4 removed)
        assert history[-1]["index"] == 1004  # Last item should be index 1004

    @pytest.mark.asyncio
    async def test_integrity_verification(self, temp_storage_manager, sample_message):
        """Test data integrity verification."""
        manager = temp_storage_manager

        # Add a message
        await manager.append_message(sample_message)

        # Verify integrity
        corruption_report = await manager.detect_corruption()
        assert corruption_report["corrupted"] is False
        assert len(corruption_report["issues"]) == 0

    @pytest.mark.asyncio
    async def test_corruption_detection_invalid_json(self, temp_storage_manager, sample_message):
        """Test corruption detection with invalid JSON."""
        manager = temp_storage_manager

        # Add a valid message first
        await manager.append_message(sample_message)

        # Manually corrupt the messages file
        with open(manager.messages_file, 'a') as f:
            f.write('{"invalid": json content\n')

        # Detect corruption
        corruption_report = await manager.detect_corruption()
        assert corruption_report["corrupted"] is True
        assert any("Invalid JSON" in issue for issue in corruption_report["issues"])

    @pytest.mark.asyncio
    async def test_corruption_detection_missing_file(self, temp_storage_manager):
        """Test corruption detection with missing files."""
        manager = temp_storage_manager

        # Remove a file
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
        with open(manager.messages_file, 'r') as f:
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
        assert manager.integrity_file.exists()

    @pytest.mark.asyncio
    async def test_empty_file_handling(self, temp_storage_manager):
        """Test handling of empty files."""
        manager = temp_storage_manager

        # Read from empty files (should not cause errors)
        messages = await manager.read_messages()
        assert len(messages) == 0

        count = await manager.get_message_count()
        assert count == 0

        history = await manager.read_history()
        assert len(history) == 0

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

        # Parse timestamp to verify it's valid ISO format
        timestamp_str = messages[0]["timestamp"]
        datetime.fromisoformat(timestamp_str)  # Should not raise exception