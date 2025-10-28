"""
Tests for Project Manager
"""

import pytest
import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone
import tempfile
import shutil

from src.project_manager import ProjectManager, ProjectInfo


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
async def test_remove_last_session_deletes_project(project_manager, temp_data_dir):
    """Test that removing the last session from a project auto-deletes the project (issue #63)"""
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

    # Remove the last session - project should be auto-deleted
    removal_success, project_deleted = await project_manager.remove_session_from_project(project.project_id, session_id)

    assert removal_success is True
    assert project_deleted is True  # Project SHOULD be deleted (no sessions remaining)

    # Verify project no longer exists
    deleted_project = await project_manager.get_project(project.project_id)
    assert deleted_project is None

    # Verify project directory was also deleted
    project_dir = temp_data_dir / "projects" / project.project_id
    assert not project_dir.exists()


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
