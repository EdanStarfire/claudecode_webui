"""
Tests for CommRouter communication routing.
"""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.legion_system import LegionSystem
from src.models.legion_models import Comm, CommType


@pytest.fixture
def legion_system():
    """Create mock LegionSystem for testing."""
    from src.session_manager import SessionInfo, SessionState

    # Create a default active minion for tests (issue #349: is_minion removed)
    default_minion = Mock(spec=SessionInfo)
    default_minion.session_id = "test-minion-123"
    default_minion.name = "TestMinion"
    default_minion.project_id = "test-legion-456"
    default_minion.state = SessionState.ACTIVE

    mock_session_coordinator = Mock()
    mock_session_coordinator.send_message = AsyncMock()
    mock_session_coordinator.session_manager = Mock()
    mock_session_coordinator.session_manager.get_session_info = AsyncMock(return_value=default_minion)
    mock_session_coordinator.start_session = AsyncMock()
    mock_session_coordinator.data_dir = Path("/tmp/test")

    system = LegionSystem(
        session_coordinator=mock_session_coordinator,
        data_storage_manager=Mock(),
        template_manager=Mock()
    )

    # Mock async methods that tests will use
    system.legion_coordinator.get_minion_info = AsyncMock(return_value=default_minion)

    return system


@pytest.fixture
def comm_router(legion_system):
    """Create CommRouter instance."""
    return legion_system.comm_router


@pytest.fixture
def sample_minion():
    """Create a sample minion SessionInfo (issue #349: is_minion removed)."""
    from src.session_manager import SessionInfo, SessionState
    mock = Mock(spec=SessionInfo)
    mock.session_id = "test-minion-123"
    mock.name = "TestMinion"
    mock.project_id = "test-legion-456"
    mock.state = SessionState.ACTIVE
    return mock


class TestVisibleMinions:
    """Test cases for get_visible_minions recursive hierarchy traversal."""

    @pytest.mark.asyncio
    async def test_visible_minions_includes_all_ancestors(self, comm_router):
        """Grandchild should see grandparent through recursive ancestor traversal."""
        from src.session_manager import SessionInfo

        grandparent = Mock(spec=SessionInfo)
        grandparent.session_id = "grandparent-id"
        grandparent.parent_overseer_id = None
        grandparent.child_minion_ids = ["parent-id"]

        parent = Mock(spec=SessionInfo)
        parent.session_id = "parent-id"
        parent.parent_overseer_id = "grandparent-id"
        parent.child_minion_ids = ["child-id"]

        child = Mock(spec=SessionInfo)
        child.session_id = "child-id"
        child.parent_overseer_id = "parent-id"
        child.child_minion_ids = []

        session_map = {
            "grandparent-id": grandparent,
            "parent-id": parent,
            "child-id": child,
        }

        sm = comm_router.system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        visible = await comm_router.get_visible_minions("child-id")
        visible_set = set(visible)

        assert "grandparent-id" in visible_set, "Grandchild should see grandparent"
        assert "parent-id" in visible_set, "Child should see parent"
        assert "child-id" in visible_set, "Self should be visible"

    @pytest.mark.asyncio
    async def test_visible_minions_includes_all_descendants(self, comm_router):
        """Grandparent should see grandchild through recursive descendant traversal."""
        from src.session_manager import SessionInfo

        grandparent = Mock(spec=SessionInfo)
        grandparent.session_id = "grandparent-id"
        grandparent.parent_overseer_id = None
        grandparent.child_minion_ids = ["parent-id"]

        parent = Mock(spec=SessionInfo)
        parent.session_id = "parent-id"
        parent.parent_overseer_id = "grandparent-id"
        parent.child_minion_ids = ["child-id"]

        child = Mock(spec=SessionInfo)
        child.session_id = "child-id"
        child.parent_overseer_id = "parent-id"
        child.child_minion_ids = []

        session_map = {
            "grandparent-id": grandparent,
            "parent-id": parent,
            "child-id": child,
        }

        sm = comm_router.system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        visible = await comm_router.get_visible_minions("grandparent-id")
        visible_set = set(visible)

        assert "parent-id" in visible_set, "Grandparent should see child"
        assert "child-id" in visible_set, "Grandparent should see grandchild"
        assert "grandparent-id" in visible_set, "Self should be visible"

    @pytest.mark.asyncio
    async def test_visible_minions_cross_branch_not_visible(self, comm_router):
        """Minions in unrelated subtrees should NOT be visible to each other."""
        from src.session_manager import SessionInfo

        root = Mock(spec=SessionInfo)
        root.session_id = "root-id"
        root.parent_overseer_id = None
        root.child_minion_ids = ["branch-a-id", "branch-b-id"]

        branch_a = Mock(spec=SessionInfo)
        branch_a.session_id = "branch-a-id"
        branch_a.parent_overseer_id = "root-id"
        branch_a.child_minion_ids = ["leaf-a-id"]

        branch_b = Mock(spec=SessionInfo)
        branch_b.session_id = "branch-b-id"
        branch_b.parent_overseer_id = "root-id"
        branch_b.child_minion_ids = ["leaf-b-id"]

        leaf_a = Mock(spec=SessionInfo)
        leaf_a.session_id = "leaf-a-id"
        leaf_a.parent_overseer_id = "branch-a-id"
        leaf_a.child_minion_ids = []

        leaf_b = Mock(spec=SessionInfo)
        leaf_b.session_id = "leaf-b-id"
        leaf_b.parent_overseer_id = "branch-b-id"
        leaf_b.child_minion_ids = []

        session_map = {
            "root-id": root,
            "branch-a-id": branch_a,
            "branch-b-id": branch_b,
            "leaf-a-id": leaf_a,
            "leaf-b-id": leaf_b,
        }

        sm = comm_router.system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        visible = await comm_router.get_visible_minions("leaf-a-id")
        visible_set = set(visible)

        # leaf-a should see: self, branch-a (parent), root (grandparent)
        assert "leaf-a-id" in visible_set
        assert "branch-a-id" in visible_set
        assert "root-id" in visible_set

        # leaf-a should NOT see branch-b or leaf-b (different subtree)
        assert "branch-b-id" not in visible_set, "Cross-branch minion should not be visible"
        assert "leaf-b-id" not in visible_set, "Cross-branch leaf should not be visible"

    @pytest.mark.asyncio
    async def test_visible_minions_includes_siblings(self, comm_router):
        """Siblings (children of same parent) should remain visible."""
        from src.session_manager import SessionInfo

        parent = Mock(spec=SessionInfo)
        parent.session_id = "parent-id"
        parent.parent_overseer_id = None
        parent.child_minion_ids = ["sibling-a-id", "sibling-b-id"]

        sibling_a = Mock(spec=SessionInfo)
        sibling_a.session_id = "sibling-a-id"
        sibling_a.parent_overseer_id = "parent-id"
        sibling_a.child_minion_ids = []

        sibling_b = Mock(spec=SessionInfo)
        sibling_b.session_id = "sibling-b-id"
        sibling_b.parent_overseer_id = "parent-id"
        sibling_b.child_minion_ids = []

        session_map = {
            "parent-id": parent,
            "sibling-a-id": sibling_a,
            "sibling-b-id": sibling_b,
        }

        sm = comm_router.system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        visible = await comm_router.get_visible_minions("sibling-a-id")
        visible_set = set(visible)

        assert "sibling-b-id" in visible_set, "Sibling should be visible"
        assert "parent-id" in visible_set, "Parent should be visible"

    @pytest.mark.asyncio
    async def test_visible_minions_nonexistent_caller(self, comm_router):
        """Non-existent caller should return empty list."""
        sm = comm_router.system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(return_value=None)

        visible = await comm_router.get_visible_minions("nonexistent-id")
        assert visible == []


class TestCommRouter:
    """Test cases for CommRouter class."""

    def test_comm_router_initialization(self, comm_router):
        """Test CommRouter initializes with system reference."""
        assert comm_router is not None
        assert hasattr(comm_router, 'system')

    @pytest.mark.asyncio
    async def test_route_comm_validation(self, comm_router):
        """Test that route_comm validates Comm objects."""
        # Create invalid Comm (no destination)
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            content="Test message"
        )

        with pytest.raises(ValueError, match="exactly one destination"):
            await comm_router.route_comm(comm)

    @pytest.mark.asyncio
    async def test_send_to_minion(self, comm_router):
        """Test sending Comm to minion."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="test-minion-123",
            content="Test message",
            comm_type=CommType.TASK
        )

        result = await comm_router._send_to_minion(comm)
        assert result is True

    @pytest.mark.asyncio
    async def test_send_to_user(self, comm_router):
        """Test sending Comm to user."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="test-minion-123",
            to_user=True,
            content="Test message to user",
            comm_type=CommType.REPORT
        )

        result = await comm_router._send_to_user(comm)
        assert result is True

    @pytest.mark.asyncio
    async def test_append_to_comm_log(self, comm_router, tmp_path):
        """Test appending Comm to JSONL log file."""
        # Point data_dir to tmp_path so _append_to_comm_log writes there
        comm_router.system.session_coordinator.data_dir = tmp_path

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="test-minion-123",
            content="Test message"
        )

        await comm_router._append_to_comm_log(
            "test-legion-456",
            "minions/test-minion-123",
            comm
        )

        # Verify log file was created with comm data
        log_file = tmp_path / "legions" / "test-legion-456" / "minions" / "test-minion-123" / "comms.jsonl"
        assert log_file.exists()
        assert log_file.read_text().strip() != ""

    @pytest.mark.asyncio
    async def test_persist_comm_from_minion(self, comm_router, sample_minion, legion_system):
        """Test persisting Comm from minion."""
        # Mock legion_coordinator.get_minion_info
        legion_system.legion_coordinator.get_minion_info = AsyncMock(return_value=sample_minion)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=sample_minion.session_id,
            to_user=True,
            content="Test message"
        )

        with patch.object(comm_router, '_append_to_comm_log', new=AsyncMock()) as mock_append:
            await comm_router._persist_comm(comm)
            # Should append to source minion's log
            mock_append.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_comm_to_minion(self, comm_router, sample_minion, legion_system):
        """Test persisting Comm to minion."""
        legion_system.legion_coordinator.get_minion_info = AsyncMock(return_value=sample_minion)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id=sample_minion.session_id,
            content="Test message"
        )

        with patch.object(comm_router, '_append_to_comm_log', new=AsyncMock()) as mock_append:
            await comm_router._persist_comm(comm)
            # Should append to destination minion's log
            mock_append.assert_called_once()
