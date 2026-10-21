"""
Tests for Legion data models.

Tests the core data structures in src/models/legion_models.py:
- Comm dataclass (validation, serialization)
- CommType enum
- DeliveryPolicy enum
- SenderType enum
- Constants (USER_MINION_ID, SYSTEM_MINION_ID, RESERVED_MINION_IDS)
"""

import uuid
from datetime import UTC, datetime

import pytest

from backend.models.legion_models import (
    RESERVED_MINION_IDS,
    SYSTEM_MINION_ID,
    SYSTEM_MINION_NAME,
    USER_MINION_ID,
    Comm,
    CommType,
    DeliveryPolicy,
    SenderType,
    default_delivery_for_type,
)


class TestCommType:
    """Tests for CommType enum."""

    def test_all_values_exist(self):
        """Verify all expected CommType values are defined."""
        expected = {
            "TASK", "QUESTION", "REPORT", "INFO", "HALT",
            "PIVOT", "THOUGHT", "SPAWN", "DISPOSE", "REPARENT", "SYSTEM"
        }
        actual = {ct.name for ct in CommType}
        assert actual == expected

    def test_value_strings(self):
        """Verify enum values are lowercase strings."""
        assert CommType.TASK.value == "task"
        assert CommType.QUESTION.value == "question"
        assert CommType.REPORT.value == "report"
        assert CommType.SYSTEM.value == "system"

    def test_from_string(self):
        """Test creating CommType from string value."""
        assert CommType("task") == CommType.TASK
        assert CommType("question") == CommType.QUESTION


class TestDeliveryPolicy:
    """Tests for DeliveryPolicy enum."""

    def test_all_values_exist(self):
        """Verify all expected DeliveryPolicy values are defined."""
        expected = {"IMMEDIATE", "WHEN_IDLE", "PIVOT", "HALT"}
        actual = {dp.name for dp in DeliveryPolicy}
        assert actual == expected

    def test_value_strings(self):
        """Verify enum values are lowercase strings."""
        assert DeliveryPolicy.IMMEDIATE.value == "immediate"
        assert DeliveryPolicy.WHEN_IDLE.value == "when_idle"
        assert DeliveryPolicy.PIVOT.value == "pivot"
        assert DeliveryPolicy.HALT.value == "halt"

    def test_from_string(self):
        """Test creating DeliveryPolicy from string value."""
        assert DeliveryPolicy("immediate") == DeliveryPolicy.IMMEDIATE
        assert DeliveryPolicy("when_idle") == DeliveryPolicy.WHEN_IDLE
        assert DeliveryPolicy("halt") == DeliveryPolicy.HALT
        assert DeliveryPolicy("pivot") == DeliveryPolicy.PIVOT


class TestSenderType:
    """Tests for SenderType enum and Comm.__post_init__ inference."""

    def test_all_values_exist(self):
        """Verify all expected SenderType values are defined."""
        expected = {"USER", "MINION", "SCHEDULE", "EXTERNAL", "SYSTEM"}
        actual = {st.name for st in SenderType}
        assert actual == expected

    def test_infers_user(self):
        """from_user=True infers SenderType.USER."""
        comm = Comm(comm_id="test", from_user=True, to_minion_id="minion-123")
        assert comm.sender_type == SenderType.USER

    def test_infers_system(self):
        """from_minion_id == SYSTEM_MINION_ID infers SenderType.SYSTEM."""
        comm = Comm(comm_id="test", from_minion_id=SYSTEM_MINION_ID, to_minion_id="minion-456")
        assert comm.sender_type == SenderType.SYSTEM

    def test_infers_minion(self):
        """Any other from_minion_id infers SenderType.MINION."""
        comm = Comm(comm_id="test", from_minion_id="minion-123", to_minion_id="minion-456")
        assert comm.sender_type == SenderType.MINION

    def test_explicit_sender_type_not_overridden(self):
        """An explicitly-supplied sender_type/sender_id passes through untouched."""
        comm = Comm(
            comm_id="test",
            from_minion_id="minion-123",
            to_minion_id="minion-456",
            sender_type=SenderType.MINION,
            sender_id="x",
        )
        assert comm.sender_type == SenderType.MINION
        assert comm.sender_id == "x"


class TestDefaultDeliveryForType:
    """Tests for default_delivery_for_type() (issue #2193 AC2)."""

    @pytest.mark.parametrize(
        ("comm_type", "expected"),
        [
            (CommType.TASK, DeliveryPolicy.IMMEDIATE),
            (CommType.QUESTION, DeliveryPolicy.IMMEDIATE),
            (CommType.REPORT, DeliveryPolicy.WHEN_IDLE),
            (CommType.INFO, DeliveryPolicy.WHEN_IDLE),
            (CommType.HALT, DeliveryPolicy.HALT),
            (CommType.PIVOT, DeliveryPolicy.PIVOT),
            (CommType.THOUGHT, DeliveryPolicy.IMMEDIATE),
            (CommType.SPAWN, DeliveryPolicy.IMMEDIATE),
            (CommType.DISPOSE, DeliveryPolicy.IMMEDIATE),
            (CommType.REPARENT, DeliveryPolicy.IMMEDIATE),
            (CommType.SYSTEM, DeliveryPolicy.IMMEDIATE),
        ],
    )
    def test_default_per_type(self, comm_type, expected):
        assert default_delivery_for_type(comm_type) == expected


class TestConstants:
    """Tests for module-level constants."""

    def test_user_minion_id_format(self):
        """USER_MINION_ID should be all-zeros UUID."""
        assert USER_MINION_ID == "00000000-0000-0000-0000-000000000000"

    def test_system_minion_id_format(self):
        """SYSTEM_MINION_ID should be all-F's UUID."""
        assert SYSTEM_MINION_ID == "ffffffff-ffff-ffff-ffff-ffffffffffff"

    def test_system_minion_name(self):
        """SYSTEM_MINION_NAME should be 'system'."""
        assert SYSTEM_MINION_NAME == "system"

    def test_reserved_minion_ids_contains_both(self):
        """RESERVED_MINION_IDS should contain user and system IDs."""
        assert USER_MINION_ID in RESERVED_MINION_IDS
        assert SYSTEM_MINION_ID in RESERVED_MINION_IDS
        assert len(RESERVED_MINION_IDS) == 2


class TestCommValidation:
    """Tests for Comm.validate() method."""

    def test_valid_user_to_minion(self):
        """Valid Comm from user to minion."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="minion-123",
            content="Hello"
        )
        assert comm.validate() is True

    def test_valid_minion_to_minion(self):
        """Valid Comm from minion to minion."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="minion-123",
            to_minion_id="minion-456",
            content="Hello"
        )
        assert comm.validate() is True

    def test_invalid_no_destination(self):
        """Comm with no destination minion should fail validation (issue #2259:
        there is no longer a to-user destination to fall back to)."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            content="Hello"
        )
        with pytest.raises(ValueError, match="destination minion"):
            comm.validate()

    def test_invalid_no_source(self):
        """Comm with no source should fail validation."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            to_minion_id="minion-123",
            content="Hello"
        )
        with pytest.raises(ValueError, match="exactly one source"):
            comm.validate()

    def test_invalid_multiple_sources(self):
        """Comm with multiple sources should fail validation."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            from_minion_id="minion-123",
            to_minion_id="minion-456",
            content="Hello"
        )
        with pytest.raises(ValueError, match="exactly one source"):
            comm.validate()


class TestCommSerialization:
    """Tests for Comm.to_dict() and Comm.from_dict()."""

    def test_to_dict_basic(self):
        """Test basic to_dict conversion."""
        comm_id = str(uuid.uuid4())
        comm = Comm(
            comm_id=comm_id,
            from_user=True,
            to_minion_id="minion-123",
            summary="Test summary",
            content="Test content",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.IMMEDIATE
        )

        data = comm.to_dict()

        assert data["comm_id"] == comm_id
        assert data["from_user"] is True
        assert data["from_minion_id"] is None
        assert data["to_minion_id"] == "minion-123"
        assert "to_user" not in data
        assert data["summary"] == "Test summary"
        assert data["content"] == "Test content"
        assert data["comm_type"] == "task"
        assert data["delivery"] == "immediate"

    def test_to_dict_all_fields(self):
        """Test to_dict includes all fields."""
        comm = Comm(
            comm_id="test-id",
            from_minion_id="minion-1",
            from_minion_name="Worker1",
            to_minion_id="minion-2",
            to_minion_name="Worker2",
            summary="Summary",
            content="Content",
            comm_type=CommType.REPORT,
            delivery=DeliveryPolicy.HALT,
            in_reply_to="prev-comm-id",
            related_task_id="task-123",
            metadata={"key": "value"},
            timestamp=1234567890.0
        )

        data = comm.to_dict()

        assert data["from_minion_name"] == "Worker1"
        assert data["to_minion_name"] == "Worker2"
        assert data["in_reply_to"] == "prev-comm-id"
        assert data["related_task_id"] == "task-123"
        assert data["metadata"] == {"key": "value"}
        assert "visible_to_user" not in data
        assert data["timestamp"] == 1234567890.0

    def test_from_dict_basic(self):
        """Test basic from_dict conversion."""
        data = {
            "comm_id": "test-id",
            "from_user": True,
            "from_minion_id": None,
            "from_minion_name": None,
            "to_minion_id": "minion-123",
            "to_user": False,
            "to_minion_name": None,
            "summary": "Test",
            "content": "Content",
            "comm_type": "task",
            "delivery": "immediate",
            "in_reply_to": None,
            "related_task_id": None,
            "metadata": {},
            "visible_to_user": True,
            "timestamp": 1234567890.0
        }

        comm = Comm.from_dict(data)

        assert comm.comm_id == "test-id"
        assert comm.from_user is True
        assert comm.to_minion_id == "minion-123"
        assert comm.comm_type == CommType.TASK
        assert comm.delivery == DeliveryPolicy.IMMEDIATE

    def test_roundtrip_serialization(self):
        """Test that to_dict -> from_dict preserves all data."""
        original = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="minion-1",
            from_minion_name="Worker",
            to_minion_id="minion-2",
            summary="Important report",
            content="Detailed content here",
            comm_type=CommType.REPORT,
            delivery=DeliveryPolicy.HALT,
            in_reply_to="prev-id",
            related_task_id="task-id",
            metadata={"foo": "bar", "count": 42},
            timestamp=1700000000.0
        )

        data = original.to_dict()
        restored = Comm.from_dict(data)

        assert restored.comm_id == original.comm_id
        assert restored.from_minion_id == original.from_minion_id
        assert restored.from_minion_name == original.from_minion_name
        assert restored.to_minion_id == original.to_minion_id
        assert restored.summary == original.summary
        assert restored.content == original.content
        assert restored.comm_type == original.comm_type
        assert restored.delivery == original.delivery
        assert restored.in_reply_to == original.in_reply_to
        assert restored.related_task_id == original.related_task_id
        assert restored.metadata == original.metadata
        assert restored.timestamp == original.timestamp

    def test_from_dict_timestamp_normalization_float(self):
        """Test that float timestamps are preserved."""
        data = {
            "comm_id": "test",
            "from_user": True,
            "from_minion_id": None,
            "from_minion_name": None,
            "to_minion_id": "minion-1",
            "to_user": False,
            "to_minion_name": None,
            "summary": "",
            "content": "",
            "comm_type": "task",
            "delivery": "immediate",
            "in_reply_to": None,
            "related_task_id": None,
            "metadata": {},
            "visible_to_user": True,
            "timestamp": 1700000000.5
        }

        comm = Comm.from_dict(data)
        assert comm.timestamp == 1700000000.5

    def test_from_dict_timestamp_normalization_string(self):
        """Test that ISO string timestamps are converted to float."""
        data = {
            "comm_id": "test",
            "from_user": True,
            "from_minion_id": None,
            "from_minion_name": None,
            "to_minion_id": "minion-1",
            "to_user": False,
            "to_minion_name": None,
            "summary": "",
            "content": "",
            "comm_type": "task",
            "delivery": "immediate",
            "in_reply_to": None,
            "related_task_id": None,
            "metadata": {},
            "visible_to_user": True,
            "timestamp": "2024-01-15T10:30:00Z"
        }

        comm = Comm.from_dict(data)
        # Should be converted to Unix timestamp
        assert isinstance(comm.timestamp, float)
        assert comm.timestamp > 0

    def test_from_dict_invalid_timestamp_fallback(self):
        """Test that invalid timestamps fall back to current time."""
        data = {
            "comm_id": "test",
            "from_user": True,
            "from_minion_id": None,
            "from_minion_name": None,
            "to_minion_id": "minion-1",
            "to_user": False,
            "to_minion_name": None,
            "summary": "",
            "content": "",
            "comm_type": "task",
            "delivery": "immediate",
            "in_reply_to": None,
            "related_task_id": None,
            "metadata": {},
            "visible_to_user": True,
            "timestamp": "not-a-valid-timestamp"
        }

        before = datetime.now(UTC).timestamp()
        comm = Comm.from_dict(data)
        after = datetime.now(UTC).timestamp()

        # Should fall back to current time
        assert before <= comm.timestamp <= after

    def test_from_dict_does_not_mutate_input(self):
        """Test that from_dict doesn't modify the input dictionary."""
        data = {
            "comm_id": "test",
            "from_user": True,
            "from_minion_id": None,
            "from_minion_name": None,
            "to_minion_id": "minion-1",
            "to_user": False,
            "to_minion_name": None,
            "summary": "",
            "content": "",
            "comm_type": "task",
            "delivery": "immediate",
            "in_reply_to": None,
            "related_task_id": None,
            "metadata": {},
            "visible_to_user": True,
            "timestamp": 1234567890.0
        }

        original_type = data["comm_type"]
        Comm.from_dict(data)

        # Input should not be mutated
        assert data["comm_type"] == original_type
        assert isinstance(data["comm_type"], str)

    @pytest.mark.parametrize(
        ("old_value", "expected"),
        [
            ("none", DeliveryPolicy.IMMEDIATE),
            ("halt", DeliveryPolicy.HALT),
            ("pivot", DeliveryPolicy.PIVOT),
        ],
    )
    def test_from_dict_interrupt_priority_backward_compat(self, old_value, expected):
        """Old persisted comms keyed on interrupt_priority (pre-#2193) still deserialize."""
        data = {
            "comm_id": "test",
            "from_user": True,
            "from_minion_id": None,
            "from_minion_name": None,
            "to_minion_id": "minion-1",
            "to_user": False,
            "to_minion_name": None,
            "summary": "",
            "content": "",
            "comm_type": "task",
            "interrupt_priority": old_value,
            "in_reply_to": None,
            "related_task_id": None,
            "metadata": {},
            "visible_to_user": True,
            "timestamp": 1234567890.0,
        }

        comm = Comm.from_dict(data)
        assert comm.delivery == expected

    def test_from_dict_drops_legacy_to_user_and_visible_to_user_keys(self):
        """Issue #2259 AC5: every Comm persisted before this change serialized
        visible_to_user (default True on ALL comms, not just to_user ones), and
        to_user comms also serialized to_user. Both fields are gone from the
        dataclass now — from_dict() must pop them rather than raise TypeError,
        since PendingStore replays persisted JSONL through this exact path."""
        data = {
            "comm_id": "test",
            "from_user": False,
            "from_minion_id": "minion-1",
            "from_minion_name": None,
            "to_minion_id": None,
            "to_user": True,
            "to_minion_name": None,
            "summary": "",
            "content": "",
            "comm_type": "task",
            "delivery": "immediate",
            "in_reply_to": None,
            "related_task_id": None,
            "metadata": {},
            "visible_to_user": True,
            "timestamp": 1234567890.0,
        }

        comm = Comm.from_dict(data)

        assert comm.comm_id == "test"
        assert not hasattr(comm, "to_user")
        assert not hasattr(comm, "visible_to_user")


class TestCommDefaults:
    """Tests for Comm default field values."""

    def test_default_comm_type(self):
        """Default comm_type should be SYSTEM."""
        comm = Comm(comm_id="test")
        assert comm.comm_type == CommType.SYSTEM

    def test_default_delivery(self):
        """Default delivery should be IMMEDIATE."""
        comm = Comm(comm_id="test")
        assert comm.delivery == DeliveryPolicy.IMMEDIATE

    def test_default_metadata_is_empty_dict(self):
        """Default metadata should be empty dict."""
        comm = Comm(comm_id="test")
        assert comm.metadata == {}

    def test_default_timestamp_is_current(self):
        """Default timestamp should be current time."""
        before = datetime.now(UTC).timestamp()
        comm = Comm(comm_id="test")
        after = datetime.now(UTC).timestamp()

        assert before <= comm.timestamp <= after

    def test_metadata_default_factory_isolation(self):
        """Each Comm should have its own metadata dict."""
        comm1 = Comm(comm_id="test1")
        comm2 = Comm(comm_id="test2")

        comm1.metadata["key"] = "value"

        assert "key" not in comm2.metadata
