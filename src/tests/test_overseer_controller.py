"""
Tests for spawn_minion with parent_name parameter (issue #594).

Tests the parent_name resolution logic in LegionMCPTools._handle_spawn_minion()
which allows grandparent minions to build multi-level hierarchies.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from src.session_manager import SessionInfo, SessionState, slugify_name


def _make_session(session_id, name, project_id="legion-1", parent_id=None, children=None):
    """Helper to create mock SessionInfo."""
    s = Mock(spec=SessionInfo)
    s.session_id = session_id
    s.name = name
    s.slug = slugify_name(name)
    s.project_id = project_id
    s.parent_overseer_id = parent_id
    s.child_minion_ids = children or []
    s.state = SessionState.ACTIVE
    s.working_directory = "/tmp/test"
    s.role = "worker"
    # Attributes inherited by child minion SessionConfig construction
    s.thinking_mode = None
    s.thinking_budget_tokens = None
    s.effort = None
    s.setting_sources = None
    s.additional_directories = []
    s.sandbox_config = None
    s.docker_enabled = False
    s.docker_image = None
    s.docker_extra_mounts = []
    s.docker_home_directory = None
    s.history_distillation_enabled = True
    s.auto_memory_mode = "claude"
    s.auto_memory_directory = None
    s.skill_creating_enabled = False
    s.mcp_server_ids = []
    s.enable_claudeai_mcp_servers = True
    s.strict_mcp_config = False
    s.bare_mode = False
    s.env_scrub_enabled = False
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
            "system_prompt": "Do work",
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
            "system_prompt": "Do work",
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
            "system_prompt": "Do work",
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
            "system_prompt": "Do work",
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
            "system_prompt": "Do work",
            "parent_name": "Caller",
        }

        result = await mcp_tools._handle_spawn_minion(args)

        assert not result.get("is_error", False), f"Self as parent_name should succeed: {result}"

        call_kwargs = mock_system.overseer_controller.spawn_minion.call_args
        assert call_kwargs.kwargs["parent_overseer_id"] == "caller-id"


class TestDisposeDescendant:
    """Tests for ancestor disposing descendant minions (not just direct children)."""

    @pytest.mark.asyncio
    async def test_dispose_grandchild_by_grandparent(self, mcp_tools, mock_system):
        """
        Grandparent should be able to dispose a grandchild (child of its child).
        The MCP layer resolves the grandchild's actual parent for disposal.
        """
        grandparent = _make_session(
            "gp-id", "Grandparent", children=["parent-id"]
        )
        parent = _make_session(
            "parent-id", "Parent", parent_id="gp-id", children=["child-id"]
        )
        child = _make_session(
            "child-id", "Child", parent_id="parent-id"
        )

        session_map = {
            "gp-id": grandparent,
            "parent-id": parent,
            "child-id": child,
        }
        sm = mock_system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        # get_descendants returns full subtree under Grandparent
        mock_system.session_coordinator.get_descendants = AsyncMock(return_value=[
            {"session_id": "parent-id", "name": "Parent", "role": "worker",
             "state": "active", "parent_id": "gp-id"},
            {"session_id": "child-id", "name": "Child", "role": "worker",
             "state": "active", "parent_id": "parent-id"},
        ])

        mock_system.overseer_controller.dispose_minion = AsyncMock(
            return_value={
                "success": True,
                "disposed_minion_id": "child-id",
                "disposed_minion_name": "Child",
                "descendants_count": 0,
                "deleted": False,
            }
        )

        args = {
            "_parent_overseer_id": "gp-id",
            "minion_name": "Child",
            "delete": False,
        }

        result = await mcp_tools._handle_dispose_minion(args)

        assert not result.get("is_error", False), f"Expected success, got: {result}"

        # Verify dispose was called with Child's actual parent (Parent), not Grandparent
        call_kwargs = mock_system.overseer_controller.dispose_minion.call_args
        assert call_kwargs.kwargs["parent_overseer_id"] == "parent-id", (
            "Grandchild should be disposed via its actual parent, not the grandparent"
        )
        assert call_kwargs.kwargs["child_minion_name"] == "Child"

    @pytest.mark.asyncio
    async def test_dispose_direct_child_still_works(self, mcp_tools, mock_system):
        """
        Disposing a direct child should work as before (no regression).
        """
        parent = _make_session("parent-id", "Parent", children=["child-id"])
        child = _make_session("child-id", "Child", parent_id="parent-id")

        session_map = {"parent-id": parent, "child-id": child}
        sm = mock_system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        mock_system.overseer_controller.dispose_minion = AsyncMock(
            return_value={
                "success": True,
                "disposed_minion_id": "child-id",
                "disposed_minion_name": "Child",
                "descendants_count": 0,
                "deleted": False,
            }
        )

        args = {
            "_parent_overseer_id": "parent-id",
            "minion_name": "Child",
            "delete": False,
        }

        result = await mcp_tools._handle_dispose_minion(args)

        assert not result.get("is_error", False), f"Expected success, got: {result}"

        # Direct child: dispose called with caller as parent
        call_kwargs = mock_system.overseer_controller.dispose_minion.call_args
        assert call_kwargs.kwargs["parent_overseer_id"] == "parent-id"

    @pytest.mark.asyncio
    async def test_dispose_unrelated_minion_rejected(self, mcp_tools, mock_system):
        """
        Caller cannot dispose a minion outside their subtree.
        """
        caller = _make_session("caller-id", "Caller", children=[])

        session_map = {"caller-id": caller}
        sm = mock_system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        # No descendants
        mock_system.session_coordinator.get_descendants = AsyncMock(return_value=[])

        args = {
            "_parent_overseer_id": "caller-id",
            "minion_name": "Unrelated",
            "delete": False,
        }

        result = await mcp_tools._handle_dispose_minion(args)

        assert result.get("is_error") is True
        assert "found in your subtree" in result["content"][0]["text"]
