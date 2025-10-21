"""
Tests for ProjectInfo backward compatibility with is_multi_agent flag.
"""

import pytest
from datetime import datetime, timezone
from src.project_manager import ProjectInfo


def test_project_info_defaults_is_multi_agent_false():
    """Test that new ProjectInfo instances default is_multi_agent to False."""
    project = ProjectInfo(
        project_id="proj-123",
        name="Test Project",
        working_directory="/path/to/project",
        session_ids=[]
    )

    assert project.is_multi_agent is False


def test_project_info_can_be_legion():
    """Test that ProjectInfo can be created as a Legion."""
    legion = ProjectInfo(
        project_id="legion-123",
        name="Test Legion",
        working_directory="/path/to/project",
        session_ids=[],
        is_multi_agent=True
    )

    assert legion.is_multi_agent is True


def test_project_info_serialization_includes_is_multi_agent():
    """Test that is_multi_agent is included in serialization."""
    project = ProjectInfo(
        project_id="proj-123",
        name="Test Project",
        working_directory="/path",
        session_ids=[],
        is_multi_agent=False
    )

    data = project.to_dict()
    assert "is_multi_agent" in data
    assert data["is_multi_agent"] is False


def test_project_info_migration_old_format():
    """Test that old ProjectInfo JSON without is_multi_agent loads correctly."""
    # Simulate old project JSON without is_multi_agent field
    old_data = {
        "project_id": "proj-old",
        "name": "Old Project",
        "working_directory": "/path",
        "session_ids": ["session-1"],
        "is_expanded": True,
        "created_at": "2025-01-01T12:00:00+00:00",
        "updated_at": "2025-01-01T12:00:00+00:00",
        "order": 0
        # Note: is_multi_agent is intentionally missing
    }

    # Should load without error and default is_multi_agent to False
    project = ProjectInfo.from_dict(old_data)

    assert project.project_id == "proj-old"
    assert project.name == "Old Project"
    assert project.is_multi_agent is False  # Migrated default
    assert project.session_ids == ["session-1"]


def test_project_info_roundtrip_with_is_multi_agent():
    """Test serialize and deserialize preserves is_multi_agent."""
    legion = ProjectInfo(
        project_id="legion-123",
        name="Test Legion",
        working_directory="/path",
        session_ids=["minion-1", "minion-2"],
        is_multi_agent=True,
        is_expanded=False,
        order=5
    )

    # Serialize
    data = legion.to_dict()

    # Deserialize
    restored = ProjectInfo.from_dict(data)

    assert restored.project_id == legion.project_id
    assert restored.name == legion.name
    assert restored.is_multi_agent is True
    assert restored.session_ids == legion.session_ids
    assert restored.is_expanded == legion.is_expanded
    assert restored.order == legion.order


def test_project_info_migration_with_session_ids_missing():
    """Test migration handles both missing is_multi_agent and session_ids."""
    old_data = {
        "project_id": "proj-very-old",
        "name": "Very Old Project",
        "working_directory": "/path",
        "is_expanded": True,
        "created_at": "2025-01-01T12:00:00+00:00",
        "updated_at": "2025-01-01T12:00:00+00:00",
        "order": 0
        # Both is_multi_agent and session_ids missing
    }

    project = ProjectInfo.from_dict(old_data)

    assert project.is_multi_agent is False
    assert project.session_ids == []  # Migrated from existing code
