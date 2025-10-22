"""
Tests for Legion data models.
"""

import pytest
from datetime import datetime
from src.models.legion_models import (
    LegionInfo,
    MinionInfo,
    MinionState,
    Horde,
    Channel,
    Comm,
    CommType,
    InterruptPriority,
)


def test_legion_info_creation():
    """Test LegionInfo creation with defaults."""
    legion = LegionInfo(
        legion_id="legion-123",
        name="Test Legion",
        working_directory="/path/to/project"
    )

    assert legion.legion_id == "legion-123"
    assert legion.name == "Test Legion"
    assert legion.is_multi_agent is True
    assert legion.max_concurrent_minions == 20
    assert legion.active_minion_count == 0
    assert len(legion.horde_ids) == 0
    assert len(legion.minion_ids) == 0


def test_legion_info_serialization():
    """Test LegionInfo to_dict and from_dict."""
    legion = LegionInfo(
        legion_id="legion-123",
        name="Test Legion",
        working_directory="/path/to/project",
        horde_ids=["horde-1", "horde-2"],
        minion_ids=["minion-1"]
    )

    # Serialize
    data = legion.to_dict()
    assert data["legion_id"] == "legion-123"
    assert data["name"] == "Test Legion"
    assert data["horde_ids"] == ["horde-1", "horde-2"]

    # Deserialize
    restored = LegionInfo.from_dict(data)
    assert restored.legion_id == legion.legion_id
    assert restored.horde_ids == legion.horde_ids
    assert restored.created_at == legion.created_at


def test_minion_info_creation():
    """Test MinionInfo creation with defaults."""
    minion = MinionInfo(
        minion_id="minion-123",
        name="TestMinion",
        role="Test Expert"
    )

    assert minion.minion_id == "minion-123"
    assert minion.name == "TestMinion"
    assert minion.state == MinionState.CREATED
    assert minion.is_overseer is False
    assert minion.overseer_level == 0
    assert minion.parent_overseer_id is None
    assert len(minion.child_minion_ids) == 0


def test_minion_info_serialization():
    """Test MinionInfo to_dict and from_dict."""
    minion = MinionInfo(
        minion_id="minion-123",
        name="TestMinion",
        role="Test Expert",
        is_overseer=True,
        child_minion_ids=["child-1", "child-2"],
        capabilities=["testing", "validation"]
    )

    # Serialize
    data = minion.to_dict()
    assert data["minion_id"] == "minion-123"
    assert data["is_overseer"] is True
    assert data["child_minion_ids"] == ["child-1", "child-2"]
    assert data["state"] == "created"

    # Deserialize
    restored = MinionInfo.from_dict(data)
    assert restored.minion_id == minion.minion_id
    assert restored.is_overseer == minion.is_overseer
    assert restored.state == MinionState.CREATED
    assert restored.capabilities == minion.capabilities


def test_horde_creation():
    """Test Horde creation."""
    horde = Horde(
        horde_id="horde-123",
        legion_id="legion-123",
        name="Test Horde",
        root_overseer_id="minion-1"
    )

    assert horde.horde_id == "horde-123"
    assert horde.root_overseer_id == "minion-1"
    assert horde.created_by == "user"
    assert len(horde.all_minion_ids) == 0


def test_horde_serialization():
    """Test Horde to_dict and from_dict."""
    horde = Horde(
        horde_id="horde-123",
        legion_id="legion-123",
        name="Test Horde",
        root_overseer_id="minion-1",
        all_minion_ids=["minion-1", "minion-2"]
    )

    data = horde.to_dict()
    restored = Horde.from_dict(data)

    assert restored.horde_id == horde.horde_id
    assert restored.all_minion_ids == horde.all_minion_ids


def test_channel_creation():
    """Test Channel creation."""
    channel = Channel(
        channel_id="channel-123",
        legion_id="legion-123",
        name="Test Channel",
        description="For testing",
        purpose="coordination"
    )

    assert channel.channel_id == "channel-123"
    assert channel.name == "Test Channel"
    assert channel.purpose == "coordination"
    assert len(channel.member_minion_ids) == 0


def test_channel_serialization():
    """Test Channel to_dict and from_dict."""
    channel = Channel(
        channel_id="channel-123",
        legion_id="legion-123",
        name="Test Channel",
        description="For testing",
        purpose="coordination",
        member_minion_ids=["minion-1", "minion-2"]
    )

    data = channel.to_dict()
    restored = Channel.from_dict(data)

    assert restored.channel_id == channel.channel_id
    assert restored.member_minion_ids == channel.member_minion_ids


def test_comm_creation():
    """Test Comm creation."""
    comm = Comm(
        comm_id="comm-123",
        from_user=True,
        to_minion_id="minion-1",
        content="Test message",
        comm_type=CommType.TASK
    )

    assert comm.comm_id == "comm-123"
    assert comm.from_user is True
    assert comm.to_minion_id == "minion-1"
    assert comm.comm_type == CommType.TASK
    assert comm.visible_to_user is True


def test_comm_validation_success():
    """Test Comm validation passes with valid routing."""
    comm = Comm(
        comm_id="comm-123",
        from_user=True,
        to_minion_id="minion-1",
        content="Test"
    )

    assert comm.validate() is True


def test_comm_validation_multiple_destinations():
    """Test Comm validation fails with multiple destinations."""
    comm = Comm(
        comm_id="comm-123",
        from_user=True,
        to_minion_id="minion-1",
        to_channel_id="channel-1",  # Invalid: two destinations
        content="Test"
    )

    with pytest.raises(ValueError, match="exactly one destination"):
        comm.validate()


def test_comm_validation_no_source():
    """Test Comm validation fails with no source."""
    comm = Comm(
        comm_id="comm-123",
        to_minion_id="minion-1",
        content="Test"
    )

    with pytest.raises(ValueError, match="exactly one source"):
        comm.validate()


def test_comm_serialization():
    """Test Comm to_dict and from_dict."""
    comm = Comm(
        comm_id="comm-123",
        from_minion_id="minion-1",
        to_user=True,
        content="Test report",
        comm_type=CommType.REPORT,
        interrupt_priority=InterruptPriority.ROUTINE
    )

    data = comm.to_dict()
    restored = Comm.from_dict(data)

    assert restored.comm_id == comm.comm_id
    assert restored.comm_type == CommType.REPORT
    assert restored.interrupt_priority == InterruptPriority.ROUTINE
    assert restored.from_minion_id == comm.from_minion_id


def test_comm_types():
    """Test all CommType enum values."""
    assert CommType.TASK.value == "task"
    assert CommType.QUESTION.value == "question"
    assert CommType.REPORT.value == "report"
    assert CommType.HALT.value == "halt"
    assert CommType.PIVOT.value == "pivot"
    assert CommType.SPAWN.value == "spawn"


def test_minion_states():
    """Test all MinionState enum values."""
    assert MinionState.CREATED.value == "created"
    assert MinionState.ACTIVE.value == "active"
    assert MinionState.PAUSED.value == "paused"
    assert MinionState.TERMINATED.value == "terminated"
    assert MinionState.ERROR.value == "error"
