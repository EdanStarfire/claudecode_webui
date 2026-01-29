"""
Tests for ProjectInfo backward compatibility.

Issue #359: Removed is_multi_agent field - all projects now support minions.
This test file verifies backward compatibility with old state.json files
that still contain the is_multi_agent field.
"""

from src.project_manager import ProjectInfo


def test_project_info_serialization():
    """Test that ProjectInfo serialization works without is_multi_agent."""
    project = ProjectInfo(
        project_id="proj-123",
        name="Test Project",
        working_directory="/path",
        session_ids=[]
    )

    data = project.to_dict()
    assert "is_multi_agent" not in data
    assert "project_id" in data
    assert "name" in data


def test_project_info_migration_old_format_with_is_multi_agent():
    """Test that old ProjectInfo JSON with is_multi_agent loads correctly."""
    # Simulate old project JSON with is_multi_agent field
    old_data = {
        "project_id": "proj-old",
        "name": "Old Project",
        "working_directory": "/path",
        "session_ids": ["session-1"],
        "is_expanded": True,
        "created_at": "2025-01-01T12:00:00+00:00",
        "updated_at": "2025-01-01T12:00:00+00:00",
        "order": 0,
        "is_multi_agent": True  # Old field - should be silently discarded
    }

    # Should load without error, silently discarding is_multi_agent
    project = ProjectInfo.from_dict(old_data)

    assert project.project_id == "proj-old"
    assert project.name == "Old Project"
    assert project.session_ids == ["session-1"]
    # Verify is_multi_agent is not an attribute (field was removed)
    assert not hasattr(project, "is_multi_agent")


def test_project_info_migration_old_format_without_is_multi_agent():
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
    }

    # Should load without error
    project = ProjectInfo.from_dict(old_data)

    assert project.project_id == "proj-old"
    assert project.name == "Old Project"
    assert project.session_ids == ["session-1"]


def test_project_info_roundtrip():
    """Test serialize and deserialize preserves all fields."""
    project = ProjectInfo(
        project_id="proj-123",
        name="Test Project",
        working_directory="/path",
        session_ids=["session-1", "session-2"],
        is_expanded=False,
        order=5,
        max_concurrent_minions=30
    )

    # Serialize
    data = project.to_dict()

    # Deserialize
    restored = ProjectInfo.from_dict(data)

    assert restored.project_id == project.project_id
    assert restored.name == project.name
    assert restored.session_ids == project.session_ids
    assert restored.is_expanded == project.is_expanded
    assert restored.order == project.order
    assert restored.max_concurrent_minions == project.max_concurrent_minions


def test_project_info_migration_with_session_ids_missing():
    """Test migration handles missing session_ids."""
    old_data = {
        "project_id": "proj-very-old",
        "name": "Very Old Project",
        "working_directory": "/path",
        "is_expanded": True,
        "created_at": "2025-01-01T12:00:00+00:00",
        "updated_at": "2025-01-01T12:00:00+00:00",
        "order": 0
        # session_ids missing
    }

    project = ProjectInfo.from_dict(old_data)

    assert project.session_ids == []  # Migrated default
