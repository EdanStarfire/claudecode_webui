"""
Tests for CommRouter communication routing.
"""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.legion_system import LegionSystem
from backend.models.legion_models import Comm, CommType


@pytest.fixture
def legion_system():
    """Create mock LegionSystem for testing."""
    from backend.session_manager import SessionInfo, SessionState

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
    from backend.session_manager import SessionInfo, SessionState
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
        from backend.session_manager import SessionInfo

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
        from backend.session_manager import SessionInfo

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
        from backend.session_manager import SessionInfo

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
        from backend.session_manager import SessionInfo

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
    async def test_persist_comm_from_minion(self, comm_router, sample_minion, legion_system):
        """Test persisting Comm from minion writes to timeline."""
        # Mock legion_coordinator.get_minion_info
        legion_system.legion_coordinator.get_minion_info = AsyncMock(return_value=sample_minion)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id=sample_minion.session_id,
            to_user=True,
            content="Test message"
        )

        with patch.object(comm_router, '_append_to_timeline', new=AsyncMock()) as mock_append_timeline:
            await comm_router._persist_comm(comm)
            mock_append_timeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_comm_to_minion(self, comm_router, sample_minion, legion_system):
        """Test persisting Comm to minion writes to timeline."""
        legion_system.legion_coordinator.get_minion_info = AsyncMock(return_value=sample_minion)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id=sample_minion.session_id,
            content="Test message"
        )

        with patch.object(comm_router, '_append_to_timeline', new=AsyncMock()) as mock_append_timeline:
            await comm_router._persist_comm(comm)
            mock_append_timeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_issue_939_attachment_metadata_in_comm(self, comm_router, tmp_path):
        """Regression test: _send_to_minion must include attachment metadata in comm_metadata
        so the frontend can render attachment chips on received comms."""
        # Point data_dir to tmp_path so attachment directory creation succeeds
        comm_router.system.session_coordinator.data_dir = tmp_path

        # Create a real source file for the attachment delivery code to copy
        source_file = tmp_path / "report.txt"
        source_file.write_bytes(b"test file content")

        # Mock async session methods called during attachment delivery
        comm_router.system.session_coordinator.register_uploaded_resource = AsyncMock(
            return_value={"resource_id": "res-abc123"}
        )
        comm_router.system.session_coordinator.register_uploaded_file = AsyncMock()

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="test-minion-123",
            content="Here is a file",
            comm_type=CommType.TASK,
            attachments=[
                {
                    "name": "report.txt",
                    "size": 1024,
                    "source_path": str(source_file),
                }
            ],
        )

        # Attachment delivery now happens in _deliver_attachments(), called by
        # route_comm() before _send_to_minion() (issue #1730)
        await comm_router._deliver_attachments(comm)
        result = await comm_router._send_to_minion(comm)
        assert result is True

        # Verify send_message was called with metadata containing attachments
        send_message_mock = comm_router.system.session_coordinator.send_message
        send_message_mock.assert_called_once()
        call_kwargs = send_message_mock.call_args
        metadata = call_kwargs.kwargs.get("metadata")

        assert metadata is not None, "send_message must be called with metadata"
        assert "attachments" in metadata, "metadata must contain 'attachments' key for frontend chip rendering"
        attachments = metadata["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "report.txt"
        assert attachments[0]["stored_path"] != "", "stored_path must be set after delivery"
        assert attachments[0]["resource_id"] == "res-abc123"

    @pytest.mark.asyncio
    async def test_issue_1843_comm_metadata_includes_summary_and_trailing_instruction(self, comm_router):
        """Regression test: _send_to_minion must expose comm.summary, comm.content, and
        the trailing send_comm instruction as structured metadata fields for the frontend
        CommCard UI, WITHOUT altering the model-delivered `message` text (AC5/T4)."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="test-minion-123",
            summary="Investigate slow query",
            content="Please look into the slow query on orders.",
            comm_type=CommType.TASK,
        )

        result = await comm_router._send_to_minion(comm)
        assert result is True

        send_message_mock = comm_router.system.session_coordinator.send_message
        send_message_mock.assert_called_once()
        call_kwargs = send_message_mock.call_args.kwargs

        metadata = call_kwargs.get("metadata")
        assert metadata is not None
        comm_meta = metadata["comm"]
        assert comm_meta["summary"] == "Investigate slow query"
        assert comm_meta["content"] == "Please look into the slow query on orders."
        assert comm_meta["trailing_instruction"] == "Always send messages to Minion #user using the `send_comm` tool."

        expected_message = (
            "**📋 Task from Minion #user:** Investigate slow query\n\n"
            "Please look into the slow query on orders.\n\n"
            "---\n"
            "Always send messages to Minion #user using the `send_comm` tool."
        )
        assert call_kwargs.get("message") == expected_message, (
            "Delivered message text must be byte-identical to before this change - "
            "metadata additions must never alter what the SDK receives"
        )

    @pytest.mark.asyncio
    async def test_issue_1843_comm_metadata_empty_summary_is_empty_string(self, comm_router):
        """Regression test: when comm.summary is unset, metadata['comm']['summary'] must be
        an empty string (not None, not omitted) so the frontend fallback logic can reliably
        distinguish 'empty' from 'missing'."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="test-minion-123",
            content="No summary provided for this one.",
            comm_type=CommType.INFO,
        )

        result = await comm_router._send_to_minion(comm)
        assert result is True

        send_message_mock = comm_router.system.session_coordinator.send_message
        call_kwargs = send_message_mock.call_args.kwargs
        comm_meta = call_kwargs["metadata"]["comm"]
        assert comm_meta["summary"] == ""
        assert comm_meta["content"] == "No summary provided for this one."

    @pytest.mark.asyncio
    async def test_issue_1730_route_comm_persists_non_null_resource_id(self, comm_router, tmp_path):
        """Regression test: route_comm() delivers attachments (resolving
        resource_id) before persisting the Comm, so timeline.jsonl carries the
        correct resource_id from the start instead of a null placeholder that
        gets mutated in place afterward (append-only log, so the persisted
        record previously stayed null forever)."""
        comm_router.system.session_coordinator.data_dir = tmp_path

        source_file = tmp_path / "notes.txt"
        source_file.write_bytes(b"attachment contents")

        comm_router.system.session_coordinator.register_uploaded_resource = AsyncMock(
            return_value={"resource_id": "res-persisted-1"}
        )
        comm_router.system.session_coordinator.register_uploaded_file = AsyncMock()

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="test-minion-123",
            content="Here is a file",
            comm_type=CommType.TASK,
            attachments=[
                {
                    "name": "notes.txt",
                    "size": 20,
                    "mime_type": "text/plain",
                    "source_path": str(source_file),
                }
            ],
        )

        with patch.object(comm_router, '_append_to_timeline', new=AsyncMock()) as mock_append_timeline:
            result = await comm_router.route_comm(comm)

        assert result is True
        mock_append_timeline.assert_called_once()
        persisted_comm = mock_append_timeline.call_args.args[1]
        assert persisted_comm.attachments[0]["resource_id"] == "res-persisted-1", (
            "Comm persisted to timeline.jsonl must already carry the resolved "
            "resource_id, not null"
        )

    @pytest.mark.asyncio
    async def test_issue_1730_deliver_attachments_skips_when_target_missing(self, comm_router, tmp_path):
        """Regression test: _deliver_attachments() must not write files or
        register resources for a to_minion_id that doesn't exist. Delivery now
        runs before _send_to_minion()'s own "target minion not found" check
        (moved there by issue #1730), so it must re-check existence itself —
        otherwise a comm to a missing/disposed minion would silently create
        orphaned files/resources under a session directory that was never
        wired into session_manager."""
        comm_router.system.session_coordinator.data_dir = tmp_path
        comm_router.system.session_coordinator.session_manager.get_session_info = AsyncMock(
            return_value=None
        )
        register_resource_mock = AsyncMock(return_value={"resource_id": "should-not-be-called"})
        comm_router.system.session_coordinator.register_uploaded_resource = register_resource_mock

        source_file = tmp_path / "notes.txt"
        source_file.write_bytes(b"attachment contents")

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="nonexistent-minion",
            content="Here is a file",
            comm_type=CommType.TASK,
            attachments=[
                {
                    "name": "notes.txt",
                    "size": 20,
                    "mime_type": "text/plain",
                    "source_path": str(source_file),
                }
            ],
        )

        await comm_router._deliver_attachments(comm)

        register_resource_mock.assert_not_called()
        assert comm.attachments[0].get("resource_id") is None
        assert not (tmp_path / "sessions" / "nonexistent-minion").exists(), (
            "No attachment directory should be created for a nonexistent target minion"
        )


class TestAutoStartFailureReason:
    """Issue #1839: auto-start failure should surface the real error_message
    (re-fetched from SessionInfo) instead of only the generic state text."""

    def _make_minion(self, state, error_message=None):
        from backend.session_manager import SessionInfo, SessionState

        mock = Mock(spec=SessionInfo)
        mock.session_id = "test-minion-123"
        mock.name = "TestMinion"
        mock.project_id = "test-legion-456"
        mock.state = state if state is not None else SessionState.CREATED
        mock.error_message = error_message
        return mock

    @pytest.mark.asyncio
    async def test_auto_start_failure_surfaces_error_message(self, comm_router):
        """T1-equivalent: a freshly computed error_message on the re-fetched
        SessionInfo is included in both the error Comm and the log line."""
        from backend.session_manager import SessionState

        sm = comm_router.system.session_coordinator.session_manager
        initial_minion = self._make_minion(SessionState.CREATED)
        failed_minion = self._make_minion(
            SessionState.ERROR, error_message="Docker sandbox unavailable: image not found"
        )
        sm.get_session_info = AsyncMock(side_effect=[initial_minion, failed_minion])
        comm_router.system.session_coordinator.start_session = AsyncMock(return_value=False)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="Test message",
            comm_type=CommType.TASK,
        )

        with patch.object(comm_router, '_send_system_error_comm', new=AsyncMock()) as mock_error_comm, \
                patch('backend.legion.comm_router.legion_logger') as mock_logger:
            result = await comm_router._send_to_minion(comm)

        assert result is False
        mock_error_comm.assert_called_once()
        error_message_arg = mock_error_comm.call_args.kwargs["error_message"]
        assert "Docker sandbox unavailable: image not found" in error_message_arg
        assert comm.metadata["delivery_failure_reason"] == "Docker sandbox unavailable: image not found"

        logged = "".join(str(call) for call in mock_logger.error.call_args_list)
        assert "Docker sandbox unavailable: image not found" in logged

    @pytest.mark.asyncio
    async def test_auto_start_failure_surfaces_stale_error_message(self, comm_router):
        """T2-equivalent: session already in ERROR state from a prior failure
        (no fresh error computed this call) still surfaces its stale-but-relevant
        error_message via the re-fetch."""
        from backend.session_manager import SessionState

        sm = comm_router.system.session_coordinator.session_manager
        initial_minion = self._make_minion(SessionState.CREATED)
        failed_minion = self._make_minion(
            SessionState.ERROR, error_message="Previous failure: connection refused"
        )
        sm.get_session_info = AsyncMock(side_effect=[initial_minion, failed_minion])
        comm_router.system.session_coordinator.start_session = AsyncMock(return_value=False)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="Test message",
            comm_type=CommType.TASK,
        )

        with patch.object(comm_router, '_send_system_error_comm', new=AsyncMock()) as mock_error_comm:
            result = await comm_router._send_to_minion(comm)

        assert result is False
        error_message_arg = mock_error_comm.call_args.kwargs["error_message"]
        assert "Previous failure: connection refused" in error_message_arg

    @pytest.mark.asyncio
    async def test_auto_start_failure_degrades_gracefully_when_session_info_none(self, comm_router):
        """Degrade-gracefully case: get_session_info() returns None on the
        re-fetch — falls back to the existing state-based text, no exception."""
        from backend.session_manager import SessionState

        sm = comm_router.system.session_coordinator.session_manager
        initial_minion = self._make_minion(SessionState.CREATED)
        sm.get_session_info = AsyncMock(side_effect=[initial_minion, None])
        comm_router.system.session_coordinator.start_session = AsyncMock(return_value=False)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="Test message",
            comm_type=CommType.TASK,
        )

        with patch.object(comm_router, '_send_system_error_comm', new=AsyncMock()) as mock_error_comm:
            result = await comm_router._send_to_minion(comm)

        assert result is False
        error_message_arg = mock_error_comm.call_args.kwargs["error_message"]
        assert f"state: {SessionState.CREATED}" in error_message_arg
        assert comm.metadata["delivery_failure_reason"] is None

    @pytest.mark.asyncio
    async def test_auto_start_failure_degrades_gracefully_when_lookup_raises(self, comm_router):
        """Degrade-gracefully case: get_session_info() raises on the re-fetch —
        falls back to the existing state-based text, no exception escapes."""
        from backend.session_manager import SessionState

        sm = comm_router.system.session_coordinator.session_manager
        initial_minion = self._make_minion(SessionState.CREATED)

        async def raise_on_second_call(*_args, **_kwargs):
            raise RuntimeError("db unavailable")

        call_count = {"n": 0}

        async def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return initial_minion
            return await raise_on_second_call()

        sm.get_session_info = AsyncMock(side_effect=side_effect)
        comm_router.system.session_coordinator.start_session = AsyncMock(return_value=False)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="Test message",
            comm_type=CommType.TASK,
        )

        with patch.object(comm_router, '_send_system_error_comm', new=AsyncMock()) as mock_error_comm:
            result = await comm_router._send_to_minion(comm)

        assert result is False
        error_message_arg = mock_error_comm.call_args.kwargs["error_message"]
        assert f"state: {SessionState.CREATED}" in error_message_arg
        assert comm.metadata["delivery_failure_reason"] is None

    @pytest.mark.asyncio
    async def test_target_not_found_message_unchanged(self, comm_router):
        """AC4 regression: target-not-found failure keeps its existing
        generic message, byte-for-byte, untouched by this change."""
        sm = comm_router.system.session_coordinator.session_manager
        sm.get_session_info = AsyncMock(return_value=None)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="nonexistent-minion",
            content="Test message",
            comm_type=CommType.TASK,
        )

        with patch.object(comm_router, '_send_system_error_comm', new=AsyncMock()) as mock_error_comm:
            result = await comm_router._send_to_minion(comm)

        assert result is False
        mock_error_comm.assert_called_once_with(
            to_minion_id="sender-minion",
            error_message="Failed to deliver message: Target minion not found",
            original_comm_id=comm.comm_id,
        )
        assert "delivery_failure_reason" not in comm.metadata

    @pytest.mark.asyncio
    async def test_send_message_rejection_message_unchanged(self, comm_router):
        """AC4 regression: send_message() rejection (post auto-start-success
        path) keeps its existing generic message unchanged."""
        from backend.session_manager import SessionState

        sm = comm_router.system.session_coordinator.session_manager
        active_minion = self._make_minion(SessionState.ACTIVE)
        sm.get_session_info = AsyncMock(return_value=active_minion)
        comm_router.system.session_coordinator.send_message = AsyncMock(return_value=False)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="Test message",
            comm_type=CommType.TASK,
        )

        with patch.object(comm_router, '_send_system_error_comm', new=AsyncMock()) as mock_error_comm:
            result = await comm_router._send_to_minion(comm)

        assert result is False
        mock_error_comm.assert_called_once_with(
            to_minion_id="sender-minion",
            error_message="Failed to deliver message: SDK rejected the message",
            original_comm_id=comm.comm_id,
        )
        assert "delivery_failure_reason" not in comm.metadata
