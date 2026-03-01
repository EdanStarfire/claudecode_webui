"""
Tests for spawn_minion with parent_name parameter (issue #594).

Tests the parent_name resolution logic in LegionMCPTools._handle_spawn_minion()
which allows grandparent minions to build multi-level hierarchies.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from src.session_manager import SessionInfo, SessionState


def _make_session(session_id, name, project_id="legion-1", parent_id=None, children=None):
    """Helper to create mock SessionInfo."""
    s = Mock(spec=SessionInfo)
    s.session_id = session_id
    s.name = name
    s.slug = name
    s.project_id = project_id
    s.parent_overseer_id = parent_id
    s.child_minion_ids = children or []
    s.state = SessionState.ACTIVE
    s.working_directory = "/tmp/test"
    s.role = "worker"
    return s


@pytest.fixture
def mock_system():
    """Create a mock LegionSystem with required components."""
    from src.legion_system import LegionSystem

    mock_session_coordinator = Mock()
    mock_session_coordinator.send_message = AsyncMock()
    mock_session_coordinator.session_manager = Mock()
    mock_session_coordinator.data_dir = "/tmp/test"

    system = LegionSystem(
        session_coordinator=mock_session_coordinator,
        data_storage_manager=Mock(),
        template_manager=Mock()
    )

    return system


@pytest.fixture
def mcp_tools(mock_system):
    """Create LegionMCPTools instance."""
    return mock_system.mcp_tools


class TestSpawnWithParentName:
    """Tests for spawn_minion parent_name parameter."""

    @pytest.mark.asyncio
    async def test_spawn_with_parent_name_creates_grandchild(self, mcp_tools, mock_system):
        """
        Grandparent spawns Parent, then spawns Child with parent_name='Parent'.
        Child's parent_overseer_id should be Parent's session_id.
        """
        grandparent = _make_session("gp-id", "Grandparent", children=["parent-id"])
        parent = _make_session("parent-id", "Parent", parent_id="gp-id")

        session_map = {"gp-id": grandparent, "parent-id": parent}
        sm = mock_system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        # get_minion_by_name_in_legion finds Parent
        mock_system.legion_coordinator.get_minion_by_name_in_legion = AsyncMock(
            return_value=parent
        )

        # get_descendants returns Parent as descendant of Grandparent
        mock_system.session_coordinator.get_descendants = AsyncMock(return_value=[
            {"session_id": "parent-id", "name": "Parent", "role": "worker",
             "state": "active", "parent_id": "gp-id"}
        ])

        # Mock spawn_minion to capture what parent_overseer_id is passed
        child_session = _make_session("child-id", "Child", parent_id="parent-id")
        mock_system.overseer_controller.spawn_minion = AsyncMock(
            return_value={"minion_id": "child-id"}
        )
        session_map["child-id"] = child_session
        mock_system.template_manager.get_template_by_name = AsyncMock(return_value=None)

        args = {
            "_parent_overseer_id": "gp-id",
            "name": "Child",
            "role": "Worker",
            "initialization_context": "Do work",
            "parent_name": "Parent",
        }

        result = await mcp_tools._handle_spawn_minion(args)

        assert not result.get("is_error", False), f"Expected success, got: {result}"

        # Verify spawn_minion was called with Parent's session_id as parent
        call_kwargs = mock_system.overseer_controller.spawn_minion.call_args
        assert call_kwargs.kwargs["parent_overseer_id"] == "parent-id", (
            "Child should be spawned under Parent, not Grandparent"
        )

    @pytest.mark.asyncio
    async def test_spawn_with_parent_name_security_rejects_non_descendant(
        self, mcp_tools, mock_system
    ):
        """
        Caller cannot use parent_name to place a child under a minion outside their subtree.
        """
        caller = _make_session("caller-id", "Caller", children=[])
        unrelated = _make_session("unrelated-id", "Unrelated")

        session_map = {"caller-id": caller, "unrelated-id": unrelated}
        sm = mock_system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        mock_system.legion_coordinator.get_minion_by_name_in_legion = AsyncMock(
            return_value=unrelated
        )
        # Caller has no descendants
        mock_system.session_coordinator.get_descendants = AsyncMock(return_value=[])

        args = {
            "_parent_overseer_id": "caller-id",
            "name": "Child",
            "role": "Worker",
            "initialization_context": "Do work",
            "parent_name": "Unrelated",
        }

        result = await mcp_tools._handle_spawn_minion(args)

        assert result.get("is_error") is True, "Should reject non-descendant parent_name"
        assert "must be a descendant" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_spawn_without_parent_name_default_behavior(self, mcp_tools, mock_system):
        """
        When parent_name is omitted, new minion is a direct child of caller (no regression).
        """
        caller = _make_session("caller-id", "Caller")

        session_map = {"caller-id": caller}
        sm = mock_system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        child_session = _make_session("child-id", "Child", parent_id="caller-id")
        session_map["child-id"] = child_session

        mock_system.overseer_controller.spawn_minion = AsyncMock(
            return_value={"minion_id": "child-id"}
        )
        mock_system.template_manager.get_template_by_name = AsyncMock(return_value=None)

        args = {
            "_parent_overseer_id": "caller-id",
            "name": "Child",
            "role": "Worker",
            "initialization_context": "Do work",
            # No parent_name
        }

        result = await mcp_tools._handle_spawn_minion(args)

        assert not result.get("is_error", False), f"Expected success, got: {result}"

        call_kwargs = mock_system.overseer_controller.spawn_minion.call_args
        assert call_kwargs.kwargs["parent_overseer_id"] == "caller-id", (
            "Without parent_name, child should be direct child of caller"
        )

    @pytest.mark.asyncio
    async def test_spawn_with_parent_name_not_found(self, mcp_tools, mock_system):
        """
        parent_name referencing a non-existent minion should return error.
        """
        caller = _make_session("caller-id", "Caller")

        sm = mock_system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(return_value=caller)

        mock_system.legion_coordinator.get_minion_by_name_in_legion = AsyncMock(
            return_value=None
        )

        args = {
            "_parent_overseer_id": "caller-id",
            "name": "Child",
            "role": "Worker",
            "initialization_context": "Do work",
            "parent_name": "NonExistent",
        }

        result = await mcp_tools._handle_spawn_minion(args)

        assert result.get("is_error") is True
        assert "not found in legion" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_spawn_with_parent_name_self_is_allowed(self, mcp_tools, mock_system):
        """
        Using parent_name that resolves to the caller themselves should work (self-referencing).
        """
        caller = _make_session("caller-id", "Caller")

        session_map = {"caller-id": caller}
        sm = mock_system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        mock_system.legion_coordinator.get_minion_by_name_in_legion = AsyncMock(
            return_value=caller
        )

        child_session = _make_session("child-id", "Child", parent_id="caller-id")
        session_map["child-id"] = child_session

        mock_system.overseer_controller.spawn_minion = AsyncMock(
            return_value={"minion_id": "child-id"}
        )
        mock_system.template_manager.get_template_by_name = AsyncMock(return_value=None)

        args = {
            "_parent_overseer_id": "caller-id",
            "name": "Child",
            "role": "Worker",
            "initialization_context": "Do work",
            "parent_name": "Caller",
        }

        result = await mcp_tools._handle_spawn_minion(args)

        assert not result.get("is_error", False), f"Self as parent_name should succeed: {result}"

        call_kwargs = mock_system.overseer_controller.spawn_minion.call_args
        assert call_kwargs.kwargs["parent_overseer_id"] == "caller-id"
