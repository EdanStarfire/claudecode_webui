"""
Tests for Project Manager
"""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from backend.project_manager import ProjectInfo, ProjectManager


@pytest.fixture
async def temp_data_dir():
    """Create a temporary data directory for testing"""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    # Cleanup
    if temp_dir.exists():
        shutil.rmtree(temp_dir)


@pytest.fixture
async def project_manager(temp_data_dir):
    """Create a ProjectManager instance for testing"""
    manager = ProjectManager(data_dir=temp_data_dir)
    await manager.initialize()
    return manager


@pytest.mark.asyncio
async def test_project_manager_initialization(temp_data_dir):
    """Test project manager initialization"""
    manager = ProjectManager(data_dir=temp_data_dir)
    await manager.initialize()

    assert manager.data_dir == temp_data_dir
    assert manager.projects_dir == temp_data_dir / "projects"
    assert manager.projects_dir.exists()
    assert len(manager._active_projects) == 0


@pytest.mark.asyncio
async def test_create_project(project_manager, temp_data_dir):
    """Test creating a new project"""
    project = await project_manager.create_project(
        name="Test Project",
        working_directory=str(temp_data_dir / "test_project")
    )

    assert project is not None
    assert project.name == "Test Project"
    assert Path(project.working_directory).is_absolute()
    assert project.session_ids == []
    assert project.is_expanded is True
    assert project.order == 0
    assert isinstance(project.created_at, datetime)
    assert isinstance(project.updated_at, datetime)

    # Verify persistence
    state_file = project_manager.projects_dir / project.project_id / "state.json"
    assert state_file.exists()


@pytest.mark.asyncio
async def test_create_project_with_relative_path(project_manager):
    """Test creating project converts relative paths to absolute"""
    project = await project_manager.create_project(
        name="Relative Path Project",
        working_directory="./relative/path"
    )

    assert Path(project.working_directory).is_absolute()


@pytest.mark.asyncio
async def test_get_project(project_manager, temp_data_dir):
    """Test retrieving a project"""
    created_project = await project_manager.create_project(
        name="Get Test",
        working_directory=str(temp_data_dir / "get_test")
    )

    retrieved_project = await project_manager.get_project(created_project.project_id)

    assert retrieved_project is not None
    assert retrieved_project.project_id == created_project.project_id
    assert retrieved_project.name == "Get Test"


@pytest.mark.asyncio
async def test_get_nonexistent_project(project_manager):
    """Test retrieving a non-existent project returns None"""
    project = await project_manager.get_project("nonexistent-id")
    assert project is None


@pytest.mark.asyncio
async def test_list_projects(project_manager, temp_data_dir):
    """Test listing projects in order"""
    project1 = await project_manager.create_project(
        name="Project 1",
        working_directory=str(temp_data_dir / "p1")
    )
    project2 = await project_manager.create_project(
        name="Project 2",
        working_directory=str(temp_data_dir / "p2")
    )

    projects = await project_manager.list_projects()

    assert len(projects) == 2
    # Newest projects have order 0, so project2 should be first
    assert projects[0].project_id == project2.project_id
    assert projects[1].project_id == project1.project_id


@pytest.mark.asyncio
async def test_update_project_name(project_manager, temp_data_dir):
    """Test updating project name"""
    project = await project_manager.create_project(
        name="Original Name",
        working_directory=str(temp_data_dir / "update_test")
    )

    success = await project_manager.update_project(
        project.project_id,
        name="Updated Name"
    )

    assert success is True

    updated_project = await project_manager.get_project(project.project_id)
    assert updated_project.name == "Updated Name"


@pytest.mark.asyncio
async def test_update_project_expansion(project_manager, temp_data_dir):
    """Test updating project expansion state"""
    project = await project_manager.create_project(
        name="Expansion Test",
        working_directory=str(temp_data_dir / "expansion")
    )

    success = await project_manager.update_project(
        project.project_id,
        is_expanded=False
    )

    assert success is True

    updated_project = await project_manager.get_project(project.project_id)
    assert updated_project.is_expanded is False


@pytest.mark.asyncio
async def test_toggle_expansion(project_manager, temp_data_dir):
    """Test toggling project expansion state"""
    project = await project_manager.create_project(
        name="Toggle Test",
        working_directory=str(temp_data_dir / "toggle")
    )

    # Initially expanded
    assert project.is_expanded is True

    # Toggle to collapsed
    success = await project_manager.toggle_expansion(project.project_id)
    assert success is True

    project = await project_manager.get_project(project.project_id)
    assert project.is_expanded is False

    # Toggle back to expanded
    success = await project_manager.toggle_expansion(project.project_id)
    assert success is True

    project = await project_manager.get_project(project.project_id)
    assert project.is_expanded is True


@pytest.mark.asyncio
async def test_add_session_to_project(project_manager, temp_data_dir):
    """Test adding sessions to project"""
    project = await project_manager.create_project(
        name="Session Test",
        working_directory=str(temp_data_dir / "sessions")
    )

    session_id = "test-session-123"
    success = await project_manager.add_session_to_project(project.project_id, session_id)

    assert success is True

    updated_project = await project_manager.get_project(project.project_id)
    assert session_id in updated_project.session_ids


@pytest.mark.asyncio
async def test_add_duplicate_session(project_manager, temp_data_dir):
    """Test adding duplicate session to project"""
    project = await project_manager.create_project(
        name="Duplicate Test",
        working_directory=str(temp_data_dir / "duplicate")
    )

    session_id = "duplicate-session"
    await project_manager.add_session_to_project(project.project_id, session_id)
    success = await project_manager.add_session_to_project(project.project_id, session_id)

    assert success is True

    updated_project = await project_manager.get_project(project.project_id)
    # Should only have one instance
    assert updated_project.session_ids.count(session_id) == 1


@pytest.mark.asyncio
async def test_remove_session_from_project(project_manager, temp_data_dir):
    """Test removing session from project (project has multiple sessions, should persist)"""
    project = await project_manager.create_project(
        name="Remove Test",
        working_directory=str(temp_data_dir / "remove")
    )

    # Add two sessions to the project
    session_id_1 = "remove-session-123"
    session_id_2 = "remove-session-456"
    await project_manager.add_session_to_project(project.project_id, session_id_1)
    await project_manager.add_session_to_project(project.project_id, session_id_2)

    # Remove one session - project should still exist
    removal_success, project_deleted = await project_manager.remove_session_from_project(project.project_id, session_id_1)

    assert removal_success is True
    assert project_deleted is False  # Project should NOT be deleted (still has session_id_2)

    updated_project = await project_manager.get_project(project.project_id)
    assert updated_project is not None  # Project still exists
    assert session_id_1 not in updated_project.session_ids
    assert session_id_2 in updated_project.session_ids  # Other session still there


@pytest.mark.asyncio
async def test_remove_last_session_keeps_empty_project(project_manager, temp_data_dir):
    """Test that removing the last session from a project keeps the empty project (issue #63 - revised)"""
    project = await project_manager.create_project(
        name="Last Session Test",
        working_directory=str(temp_data_dir / "last_session")
    )

    # Add only one session to the project
    session_id = "last-session-123"
    await project_manager.add_session_to_project(project.project_id, session_id)

    # Verify project exists with the session
    updated_project = await project_manager.get_project(project.project_id)
    assert updated_project is not None
    assert session_id in updated_project.session_ids

    # Remove the last session - project should remain but be empty
    removal_success, project_deleted = await project_manager.remove_session_from_project(project.project_id, session_id)

    assert removal_success is True
    assert project_deleted is False  # Project should NOT be deleted (empty projects persist)

    # Verify project still exists but has no sessions
    empty_project = await project_manager.get_project(project.project_id)
    assert empty_project is not None
    assert len(empty_project.session_ids) == 0
    assert empty_project.name == "Last Session Test"

    # Verify project directory still exists
    project_dir = temp_data_dir / "projects" / project.project_id
    assert project_dir.exists()


@pytest.mark.asyncio
async def test_reorder_project_sessions(project_manager, temp_data_dir):
    """Test reordering sessions within a project"""
    project = await project_manager.create_project(
        name="Reorder Sessions Test",
        working_directory=str(temp_data_dir / "reorder_sessions")
    )

    session1 = "session-1"
    session2 = "session-2"
    session3 = "session-3"

    await project_manager.add_session_to_project(project.project_id, session1)
    await project_manager.add_session_to_project(project.project_id, session2)
    await project_manager.add_session_to_project(project.project_id, session3)

    # Reorder sessions
    new_order = [session3, session1, session2]
    success = await project_manager.reorder_project_sessions(project.project_id, new_order)

    assert success is True

    updated_project = await project_manager.get_project(project.project_id)
    assert updated_project.session_ids == new_order


@pytest.mark.asyncio
async def test_reorder_project_sessions_invalid(project_manager, temp_data_dir):
    """Test reordering with invalid session IDs fails"""
    project = await project_manager.create_project(
        name="Invalid Reorder Test",
        working_directory=str(temp_data_dir / "invalid_reorder")
    )

    await project_manager.add_session_to_project(project.project_id, "session-1")

    # Try to reorder with different sessions
    success = await project_manager.reorder_project_sessions(
        project.project_id,
        ["session-2", "session-3"]
    )

    assert success is False


@pytest.mark.asyncio
async def test_reorder_projects(project_manager, temp_data_dir):
    """Test reordering projects"""
    project1 = await project_manager.create_project(
        name="Project 1",
        working_directory=str(temp_data_dir / "p1")
    )
    project2 = await project_manager.create_project(
        name="Project 2",
        working_directory=str(temp_data_dir / "p2")
    )
    project3 = await project_manager.create_project(
        name="Project 3",
        working_directory=str(temp_data_dir / "p3")
    )

    # Reorder projects
    new_order = [project2.project_id, project3.project_id, project1.project_id]
    success = await project_manager.reorder_projects(new_order)

    assert success is True

    projects = await project_manager.list_projects()
    assert projects[0].project_id == project2.project_id
    assert projects[1].project_id == project3.project_id
    assert projects[2].project_id == project1.project_id


@pytest.mark.asyncio
async def test_delete_project(project_manager, temp_data_dir):
    """Test deleting a project"""
    project = await project_manager.create_project(
        name="Delete Test",
        working_directory=str(temp_data_dir / "delete")
    )

    project_dir = project_manager.projects_dir / project.project_id
    assert project_dir.exists()

    success = await project_manager.delete_project(project.project_id)

    assert success is True
    assert not project_dir.exists()
    assert project.project_id not in project_manager._active_projects


@pytest.mark.asyncio
async def test_project_persistence_across_restarts(temp_data_dir):
    """Test that projects persist across manager restarts"""
    # Create manager and project
    manager1 = ProjectManager(data_dir=temp_data_dir)
    await manager1.initialize()

    project = await manager1.create_project(
        name="Persistence Test",
        working_directory=str(temp_data_dir / "persist")
    )
    await manager1.add_session_to_project(project.project_id, "session-123")

    # Create new manager instance
    manager2 = ProjectManager(data_dir=temp_data_dir)
    await manager2.initialize()

    # Verify project was loaded
    loaded_project = await manager2.get_project(project.project_id)
    assert loaded_project is not None
    assert loaded_project.name == "Persistence Test"
    assert "session-123" in loaded_project.session_ids


@pytest.mark.asyncio
async def test_project_info_serialization():
    """Test ProjectInfo to_dict and from_dict"""
    project = ProjectInfo(
        project_id="test-123",
        name="Test",
        working_directory="/absolute/path",
        session_ids=["s1", "s2"],
        is_expanded=False,
        order=5
    )

    # Serialize
    data = project.to_dict()

    # Deserialize
    restored = ProjectInfo.from_dict(data)

    assert restored.project_id == project.project_id
    assert restored.name == project.name
    assert restored.working_directory == project.working_directory
    assert restored.session_ids == project.session_ids
    assert restored.is_expanded == project.is_expanded
    assert restored.order == project.order


@pytest.mark.asyncio
async def test_project_info_kanban_fields_default():
    """New ProjectInfo defaults kanban fields to empty list/dict"""
    project = ProjectInfo(
        project_id="test-kanban-defaults",
        name="Test",
        working_directory="/absolute/path",
        session_ids=[],
    )
    assert project.kanban_groups == []
    assert project.kanban_group_assignments == {}


@pytest.mark.asyncio
async def test_project_info_kanban_round_trip():
    """kanban_groups/kanban_group_assignments survive to_dict/from_dict"""
    project = ProjectInfo(
        project_id="test-kanban-roundtrip",
        name="Test",
        working_directory="/absolute/path",
        session_ids=["s1"],
        kanban_groups=[{"group_id": "g1", "name": "Urgent"}],
        kanban_group_assignments={"s1": "g1"},
    )

    data = project.to_dict()
    restored = ProjectInfo.from_dict(data)

    assert restored.kanban_groups == [{"group_id": "g1", "name": "Urgent"}]
    assert restored.kanban_group_assignments == {"s1": "g1"}


@pytest.mark.asyncio
async def test_project_info_kanban_legacy_migration():
    """from_dict defaults missing kanban fields for pre-feature state.json data"""
    legacy_data = {
        "project_id": "legacy-project",
        "name": "Legacy",
        "working_directory": "/absolute/path",
        "session_ids": ["s1"],
        "is_expanded": True,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "order": 0,
    }

    restored = ProjectInfo.from_dict(dict(legacy_data))

    assert restored.kanban_groups == []
    assert restored.kanban_group_assignments == {}


@pytest.mark.asyncio
async def test_create_kanban_group(project_manager, temp_data_dir):
    """Creating a kanban group appends it to the ordered list"""
    project = await project_manager.create_project(
        name="Kanban Create Test",
        working_directory=str(temp_data_dir / "kanban_create")
    )

    updated = await project_manager.create_kanban_group(project.project_id, "Urgent")

    assert updated is not None
    assert len(updated.kanban_groups) == 1
    assert updated.kanban_groups[0]["name"] == "Urgent"
    assert "group_id" in updated.kanban_groups[0]


@pytest.mark.asyncio
async def test_rename_kanban_group(project_manager, temp_data_dir):
    """Renaming a kanban group updates its name only"""
    project = await project_manager.create_project(
        name="Kanban Rename Test",
        working_directory=str(temp_data_dir / "kanban_rename")
    )
    project = await project_manager.create_kanban_group(project.project_id, "Urgent")
    group_id = project.kanban_groups[0]["group_id"]

    updated = await project_manager.rename_kanban_group(project.project_id, group_id, "Renamed")

    assert updated is not None
    assert updated.kanban_groups[0]["name"] == "Renamed"
    assert updated.kanban_groups[0]["group_id"] == group_id


@pytest.mark.asyncio
async def test_rename_kanban_group_unknown_id(project_manager, temp_data_dir):
    """Renaming an unknown group id fails"""
    project = await project_manager.create_project(
        name="Kanban Rename Unknown Test",
        working_directory=str(temp_data_dir / "kanban_rename_unknown")
    )

    result = await project_manager.rename_kanban_group(project.project_id, "nonexistent", "X")

    assert result is None


@pytest.mark.asyncio
async def test_delete_kanban_group_reassigns_to_unassigned(project_manager, temp_data_dir):
    """Deleting a group strips matching assignment map entries, falling back to Unassigned"""
    project = await project_manager.create_project(
        name="Kanban Delete Test",
        working_directory=str(temp_data_dir / "kanban_delete")
    )
    await project_manager.add_session_to_project(project.project_id, "session-1")
    project = await project_manager.create_kanban_group(project.project_id, "Urgent")
    group_id = project.kanban_groups[0]["group_id"]
    project = await project_manager.assign_session_to_group(project.project_id, "session-1", group_id)
    assert project.kanban_group_assignments == {"session-1": group_id}

    updated = await project_manager.delete_kanban_group(project.project_id, group_id)

    assert updated is not None
    assert updated.kanban_groups == []
    assert "session-1" not in updated.kanban_group_assignments


@pytest.mark.asyncio
async def test_reorder_kanban_groups(project_manager, temp_data_dir):
    """Reordering kanban groups by id sequence works and rejects mismatched id sets"""
    project = await project_manager.create_project(
        name="Kanban Reorder Test",
        working_directory=str(temp_data_dir / "kanban_reorder")
    )
    project = await project_manager.create_kanban_group(project.project_id, "First")
    project = await project_manager.create_kanban_group(project.project_id, "Second")
    g1, g2 = project.kanban_groups[0]["group_id"], project.kanban_groups[1]["group_id"]

    updated = await project_manager.reorder_kanban_groups(project.project_id, [g2, g1])
    assert updated is not None
    assert [g["group_id"] for g in updated.kanban_groups] == [g2, g1]

    # Mismatched id-set rejected
    result = await project_manager.reorder_kanban_groups(project.project_id, [g1])
    assert result is None


@pytest.mark.asyncio
async def test_assign_session_to_group_and_clear(project_manager, temp_data_dir):
    """Assigning writes a map entry; assigning None/'unassigned' clears it"""
    project = await project_manager.create_project(
        name="Kanban Assign Test",
        working_directory=str(temp_data_dir / "kanban_assign")
    )
    await project_manager.add_session_to_project(project.project_id, "session-1")
    project = await project_manager.create_kanban_group(project.project_id, "Urgent")
    group_id = project.kanban_groups[0]["group_id"]

    updated = await project_manager.assign_session_to_group(project.project_id, "session-1", group_id)
    assert updated.kanban_group_assignments == {"session-1": group_id}

    cleared = await project_manager.assign_session_to_group(project.project_id, "session-1", None)
    assert cleared.kanban_group_assignments == {}

    # Assigning to unknown group fails
    result = await project_manager.assign_session_to_group(project.project_id, "session-1", "nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_cleanup_session_group_assignment(project_manager, temp_data_dir):
    """cleanup_session_group_assignment removes a session's map entry (proactive cleanup)"""
    project = await project_manager.create_project(
        name="Kanban Cleanup Test",
        working_directory=str(temp_data_dir / "kanban_cleanup")
    )
    await project_manager.add_session_to_project(project.project_id, "session-1")
    project = await project_manager.create_kanban_group(project.project_id, "Urgent")
    group_id = project.kanban_groups[0]["group_id"]
    await project_manager.assign_session_to_group(project.project_id, "session-1", group_id)

    success = await project_manager.cleanup_session_group_assignment(project.project_id, "session-1")
    assert success is True

    updated = await project_manager.get_project(project.project_id)
    assert "session-1" not in updated.kanban_group_assignments


@pytest.mark.asyncio
async def test_cleanup_session_group_assignment_noop_when_unassigned(project_manager, temp_data_dir):
    """cleanup_session_group_assignment doesn't raise when the session had no assignment"""
    project = await project_manager.create_project(
        name="Kanban Cleanup Noop Test",
        working_directory=str(temp_data_dir / "kanban_cleanup_noop")
    )
    await project_manager.add_session_to_project(project.project_id, "session-1")

    success = await project_manager.cleanup_session_group_assignment(project.project_id, "session-1")
    assert success is True
