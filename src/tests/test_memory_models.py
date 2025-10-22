"""
Tests for Legion memory models.
"""

import pytest
from datetime import datetime
from src.models.memory_models import (
    MemoryEntry,
    MemoryType,
    MinionMemory,
    TaskMilestone,
)


def test_memory_entry_creation():
    """Test MemoryEntry creation with defaults."""
    entry = MemoryEntry(
        entry_id="entry-123",
        content="Test knowledge",
        entry_type=MemoryType.FACT
    )

    assert entry.entry_id == "entry-123"
    assert entry.content == "Test knowledge"
    assert entry.entry_type == MemoryType.FACT
    assert entry.quality_score == 0.5
    assert entry.times_used_successfully == 0
    assert entry.times_used_unsuccessfully == 0


def test_memory_entry_serialization():
    """Test MemoryEntry to_dict and from_dict."""
    entry = MemoryEntry(
        entry_id="entry-123",
        content="Test pattern",
        entry_type=MemoryType.PATTERN,
        quality_score=0.8,
        times_used_successfully=5,
        related_capabilities=["testing", "validation"]
    )

    data = entry.to_dict()
    restored = MemoryEntry.from_dict(data)

    assert restored.entry_id == entry.entry_id
    assert restored.entry_type == MemoryType.PATTERN
    assert restored.quality_score == 0.8
    assert restored.times_used_successfully == 5
    assert restored.related_capabilities == entry.related_capabilities


def test_minion_memory_creation():
    """Test MinionMemory creation."""
    memory = MinionMemory(
        minion_id="minion-123"
    )

    assert memory.minion_id == "minion-123"
    assert len(memory.short_term) == 0
    assert len(memory.long_term) == 0
    assert memory.capability_evidence == {}
    assert memory.last_distilled_at is None


def test_minion_memory_with_entries():
    """Test MinionMemory with memory entries."""
    entry1 = MemoryEntry(
        entry_id="entry-1",
        content="Fact 1",
        entry_type=MemoryType.FACT
    )
    entry2 = MemoryEntry(
        entry_id="entry-2",
        content="Pattern 1",
        entry_type=MemoryType.PATTERN
    )

    memory = MinionMemory(
        minion_id="minion-123",
        short_term=[entry1],
        long_term=[entry2]
    )

    assert len(memory.short_term) == 1
    assert len(memory.long_term) == 1
    assert memory.short_term[0].content == "Fact 1"
    assert memory.long_term[0].entry_type == MemoryType.PATTERN


def test_minion_memory_serialization():
    """Test MinionMemory to_dict and from_dict."""
    entry = MemoryEntry(
        entry_id="entry-1",
        content="Test",
        entry_type=MemoryType.RULE
    )

    memory = MinionMemory(
        minion_id="minion-123",
        short_term=[entry],
        capability_evidence={"testing": ["task-1", "task-2"]}
    )

    data = memory.to_dict()
    restored = MinionMemory.from_dict(data)

    assert restored.minion_id == memory.minion_id
    assert len(restored.short_term) == 1
    assert restored.short_term[0].entry_id == "entry-1"
    assert restored.capability_evidence == memory.capability_evidence


def test_task_milestone_creation():
    """Test TaskMilestone creation."""
    milestone = TaskMilestone(
        milestone_id="milestone-123",
        task_id="task-1",
        minion_id="minion-123",
        description="Completed analysis"
    )

    assert milestone.milestone_id == "milestone-123"
    assert milestone.task_id == "task-1"
    assert milestone.success is True
    assert milestone.distilled is False
    assert len(milestone.messages_to_distill) == 0


def test_task_milestone_serialization():
    """Test TaskMilestone to_dict and from_dict."""
    milestone = TaskMilestone(
        milestone_id="milestone-123",
        task_id="task-1",
        minion_id="minion-123",
        description="Completed task",
        success=True,
        messages_to_distill=["msg-1", "msg-2"],
        distilled=True,
        distilled_memory_ids=["mem-1"]
    )

    data = milestone.to_dict()
    restored = TaskMilestone.from_dict(data)

    assert restored.milestone_id == milestone.milestone_id
    assert restored.success is True
    assert restored.distilled is True
    assert restored.messages_to_distill == ["msg-1", "msg-2"]
    assert restored.distilled_memory_ids == ["mem-1"]


def test_memory_types():
    """Test all MemoryType enum values."""
    assert MemoryType.FACT.value == "fact"
    assert MemoryType.PATTERN.value == "pattern"
    assert MemoryType.RULE.value == "rule"
    assert MemoryType.RELATIONSHIP.value == "relationship"
    assert MemoryType.EVENT.value == "event"
