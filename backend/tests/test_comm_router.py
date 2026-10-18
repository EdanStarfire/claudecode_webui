"""
Tests for CommRouter communication routing.
"""

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.delivery_gate import DeliveryGate
from backend.legion_system import LegionSystem
from backend.models.legion_models import Comm, CommType, DeliveryPolicy
from backend.pending_store import PendingStore


@pytest.fixture
def legion_system(tmp_path):
    """Create mock LegionSystem for testing."""
    from backend.session_manager import SessionInfo, SessionState

    # Create a default active minion for tests (issue #349: is_minion removed)
    default_minion = Mock(spec=SessionInfo)
    default_minion.session_id = "test-minion-123"
    default_minion.name = "TestMinion"
    default_minion.project_id = "test-legion-456"
    default_minion.state = SessionState.ACTIVE
    # Issue #2193 PR5: explicit, not left to spec's auto-mock default (a truthy
    # MagicMock) — deliver()'s AC6 queue_paused gate must see a real False here,
    # or every test below would spuriously hit the "queues instead" branch the
    # moment _send_to_minion() cuts over to deliver().
    default_minion.queue_paused = False

    mock_session_coordinator = Mock()
    mock_session_coordinator.send_message = AsyncMock()
    mock_session_coordinator.session_manager = Mock()
    mock_session_coordinator.session_manager.get_session_info = AsyncMock(return_value=default_minion)
    # Issue #2193 PR5: deliver() enqueues into a real pending_store ledger backed
    # by a real session directory — both needed now that _send_to_minion() calls
    # through to deliver() for real instead of ensure_ready()/send().
    mock_session_coordinator.session_manager.get_session_directory = AsyncMock(return_value=tmp_path)
    mock_session_coordinator.session_manager.update_session = AsyncMock(return_value=True)
    mock_session_coordinator.pending_store = PendingStore()
    mock_session_coordinator.start_session = AsyncMock()
    mock_session_coordinator.data_dir = tmp_path
    # Issue #2189 (coordinator-split PR 10); issue #2193 PR5: _send_to_minion now
    # calls through delivery_gate.deliver(), which internally calls back out to
    # this same mock coordinator's send_message/start_session/session_manager/
    # pending_store — a real DeliveryGate bound to the mock keeps those the
    # exercised mocked seams.
    mock_session_coordinator.delivery_gate = DeliveryGate(mock_session_coordinator)

    system = LegionSystem(
        session_port=mock_session_coordinator,
        data_storage_manager=Mock(),
        template_manager=Mock()
    )
    # Issue #2193 PR5: back-reference deliver()'s _is_authorized() needs to reach
    # the real CommRouter.is_authorized_sender() for HALT/PIVOT comms — mirrors
    # the real production wiring (coordinator.legion_system is the LegionSystem
    # that owns this same comm_router).
    mock_session_coordinator.legion_system = system

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
    # Issue #2193 PR5: see legion_system fixture's default_minion comment above.
    mock.queue_paused = False
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

        sm = comm_router.system.session_port.session_manager
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

        sm = comm_router.system.session_port.session_manager
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

        sm = comm_router.system.session_port.session_manager
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

        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        visible = await comm_router.get_visible_minions("sibling-a-id")
        visible_set = set(visible)

        assert "sibling-b-id" in visible_set, "Sibling should be visible"
        assert "parent-id" in visible_set, "Parent should be visible"

    @pytest.mark.asyncio
    async def test_visible_minions_nonexistent_caller(self, comm_router):
        """Non-existent caller should return empty list."""
        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(return_value=None)

        visible = await comm_router.get_visible_minions("nonexistent-id")
        assert visible == []


class TestAuthorityHelpers:
    """Issue #2193 AC7 — is_ancestor()/is_authorized_sender()."""

    @pytest.mark.asyncio
    async def test_is_ancestor_true_for_direct_parent(self, comm_router):
        from backend.session_manager import SessionInfo

        parent = Mock(spec=SessionInfo)
        parent.parent_overseer_id = None
        child = Mock(spec=SessionInfo)
        child.parent_overseer_id = "parent-id"

        session_map = {"parent-id": parent, "child-id": child}
        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        assert await comm_router.is_ancestor("parent-id", "child-id") is True

    @pytest.mark.asyncio
    async def test_is_ancestor_true_for_grandparent_multi_hop(self, comm_router):
        from backend.session_manager import SessionInfo

        grandparent = Mock(spec=SessionInfo)
        grandparent.parent_overseer_id = None
        parent = Mock(spec=SessionInfo)
        parent.parent_overseer_id = "grandparent-id"
        child = Mock(spec=SessionInfo)
        child.parent_overseer_id = "parent-id"

        session_map = {
            "grandparent-id": grandparent,
            "parent-id": parent,
            "child-id": child,
        }
        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        assert await comm_router.is_ancestor("grandparent-id", "child-id") is True

    @pytest.mark.asyncio
    async def test_is_ancestor_false_for_sibling(self, comm_router):
        from backend.session_manager import SessionInfo

        parent = Mock(spec=SessionInfo)
        parent.parent_overseer_id = None
        sibling_a = Mock(spec=SessionInfo)
        sibling_a.parent_overseer_id = "parent-id"
        sibling_b = Mock(spec=SessionInfo)
        sibling_b.parent_overseer_id = "parent-id"

        session_map = {
            "parent-id": parent,
            "sibling-a-id": sibling_a,
            "sibling-b-id": sibling_b,
        }
        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        assert await comm_router.is_ancestor("sibling-a-id", "sibling-b-id") is False

    @pytest.mark.asyncio
    async def test_is_ancestor_false_for_unrelated_minion(self, comm_router):
        from backend.session_manager import SessionInfo

        unrelated = Mock(spec=SessionInfo)
        unrelated.parent_overseer_id = None

        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(return_value=unrelated)

        assert await comm_router.is_ancestor("candidate-id", "unrelated-id") is False

    @pytest.mark.asyncio
    async def test_is_ancestor_false_with_cycle_guard(self, comm_router):
        """Defensive: a corrupted parent_overseer_id cycle must terminate, not loop
        forever (mirrors test_overseer_reparent.py's cycle-guard test style)."""
        from backend.session_manager import SessionInfo

        a = Mock(spec=SessionInfo)
        a.parent_overseer_id = "b-id"
        b = Mock(spec=SessionInfo)
        b.parent_overseer_id = "a-id"  # a <-> b cycle

        session_map = {"a-id": a, "b-id": b}
        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        assert await comm_router.is_ancestor("unrelated-id", "a-id") is False

    @pytest.mark.asyncio
    async def test_is_authorized_sender_true_for_user_regardless_of_hierarchy(self, comm_router):
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="some-minion",
            content="halt",
            delivery=DeliveryPolicy.HALT,
        )
        assert await comm_router.is_authorized_sender(comm) is True

    @pytest.mark.asyncio
    async def test_is_authorized_sender_true_for_ancestor(self, comm_router):
        from backend.session_manager import SessionInfo

        parent = Mock(spec=SessionInfo)
        parent.parent_overseer_id = None
        child = Mock(spec=SessionInfo)
        child.parent_overseer_id = "parent-id"

        session_map = {"parent-id": parent, "child-id": child}
        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="parent-id",
            to_minion_id="child-id",
            content="pivot",
            delivery=DeliveryPolicy.PIVOT,
        )
        assert await comm_router.is_authorized_sender(comm) is True

    @pytest.mark.asyncio
    async def test_is_authorized_sender_false_for_sibling(self, comm_router):
        from backend.session_manager import SessionInfo

        parent = Mock(spec=SessionInfo)
        parent.parent_overseer_id = None
        sibling_a = Mock(spec=SessionInfo)
        sibling_a.parent_overseer_id = "parent-id"
        sibling_b = Mock(spec=SessionInfo)
        sibling_b.parent_overseer_id = "parent-id"

        session_map = {
            "parent-id": parent,
            "sibling-a-id": sibling_a,
            "sibling-b-id": sibling_b,
        }
        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sibling-a-id",
            to_minion_id="sibling-b-id",
            content="halt",
            delivery=DeliveryPolicy.HALT,
        )
        assert await comm_router.is_authorized_sender(comm) is False

    @pytest.mark.asyncio
    async def test_is_authorized_sender_false_for_unrelated_minion(self, comm_router):
        from backend.session_manager import SessionInfo

        unrelated = Mock(spec=SessionInfo)
        unrelated.parent_overseer_id = None

        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(return_value=unrelated)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="unrelated-sender-id",
            to_minion_id="unrelated-target-id",
            content="pivot",
            delivery=DeliveryPolicy.PIVOT,
        )
        assert await comm_router.is_authorized_sender(comm) is False


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
    async def test_persist_comm_from_minion_no_longer_writes_timeline(
        self, comm_router, sample_minion, legion_system
    ):
        """Issue #2193 PR6: timeline.jsonl writes were removed from _persist_comm()
        (decided in PR1, executed here) — _append_to_timeline() must never be
        called, for a minion-originated comm."""
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
            mock_append_timeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_persist_comm_to_minion_no_longer_writes_timeline(
        self, comm_router, sample_minion, legion_system
    ):
        """Issue #2193 PR6: same as above, for a user-originated comm."""
        legion_system.legion_coordinator.get_minion_info = AsyncMock(return_value=sample_minion)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id=sample_minion.session_id,
            content="Test message"
        )

        with patch.object(comm_router, '_append_to_timeline', new=AsyncMock()) as mock_append_timeline:
            await comm_router._persist_comm(comm)
            mock_append_timeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_persist_comm_still_broadcasts_notifies_and_audits(
        self, comm_router, sample_minion, legion_system
    ):
        """Issue #2193 PR6: the timeline-write removal in _persist_comm() must be
        surgical — the legion_id resolution above it, and the WebSocket broadcast,
        UI notification, and audit-writer calls below it, all stay untouched."""
        legion_system.legion_coordinator.get_minion_info = AsyncMock(return_value=sample_minion)
        comm_router._comm_broadcast_callback = AsyncMock()
        comm_router._ui_notification_callback = AsyncMock()
        comm_router.audit_writer = Mock()
        comm_router.audit_writer.on_comm = AsyncMock()

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id=sample_minion.session_id,
            content="Test message",
            comm_type=CommType.TASK,
        )

        with patch.object(comm_router, '_append_to_timeline', new=AsyncMock()) as mock_append_timeline:
            await comm_router._persist_comm(comm)

        mock_append_timeline.assert_not_called()
        comm_router._comm_broadcast_callback.assert_called_once()
        comm_router._ui_notification_callback.assert_called_once()
        comm_router.audit_writer.on_comm.assert_called_once()

    @pytest.mark.asyncio
    async def test_issue_939_attachment_metadata_in_comm(self, comm_router, tmp_path):
        """Regression test: _send_to_minion must include attachment metadata in comm_metadata
        so the frontend can render attachment chips on received comms."""
        # Point data_dir to tmp_path so attachment directory creation succeeds
        comm_router.system.session_port.data_dir = tmp_path

        # Create a real source file for the attachment delivery code to copy
        source_file = tmp_path / "report.txt"
        source_file.write_bytes(b"test file content")

        # Mock async session methods called during attachment delivery
        comm_router.system.session_port.register_uploaded_resource = AsyncMock(
            return_value={"resource_id": "res-abc123"}
        )
        comm_router.system.session_port.register_uploaded_file = AsyncMock()

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
        send_message_mock = comm_router.system.session_port.send_message
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

        send_message_mock = comm_router.system.session_port.send_message
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

        send_message_mock = comm_router.system.session_port.send_message
        call_kwargs = send_message_mock.call_args.kwargs
        comm_meta = call_kwargs["metadata"]["comm"]
        assert comm_meta["summary"] == ""
        assert comm_meta["content"] == "No summary provided for this one."

    @pytest.mark.asyncio
    async def test_issue_2189_comm_content_and_metadata_unchanged_after_send(self, comm_router):
        """Regression test: _send_to_minion builds an SDK-formatted message/metadata
        for delivery_gate.deliver() internally, but must restore the caller's
        original comm.content/comm.metadata afterward — a caller that inspects the
        same Comm post-return (e.g. POST /api/legions/{id}/comms's REST response,
        which returns comm.to_dict() after route_comm()) must see what it passed
        in, not the internal SDK-formatted display text."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="test-minion-123",
            summary="short summary",
            content="original unformatted content",
            comm_type=CommType.TASK,
            metadata={"caller_key": "caller_value"},
        )

        result = await comm_router._send_to_minion(comm)
        assert result is True

        # The SDK must still have received the formatted version...
        send_message_mock = comm_router.system.session_port.send_message
        sent_message = send_message_mock.call_args.kwargs.get("message")
        assert sent_message != "original unformatted content"
        assert "original unformatted content" in sent_message

        # ...but the comm object itself must be untouched for other callers.
        assert comm.content == "original unformatted content"
        assert comm.metadata == {"caller_key": "caller_value"}

    @pytest.mark.asyncio
    async def test_issue_1730_route_comm_persists_non_null_resource_id(self, comm_router, tmp_path):
        """Regression test: route_comm() delivers attachments (resolving
        resource_id) before persisting the Comm, so the comm's own attachments
        carry the correct resource_id from the start instead of a null
        placeholder. (Issue #2193 PR6: timeline.jsonl itself is no longer
        written by _persist_comm(), so this now asserts the resolution
        directly on the comm object rather than on a persisted timeline
        record.)"""
        comm_router.system.session_port.data_dir = tmp_path

        source_file = tmp_path / "notes.txt"
        source_file.write_bytes(b"attachment contents")

        comm_router.system.session_port.register_uploaded_resource = AsyncMock(
            return_value={"resource_id": "res-persisted-1"}
        )
        comm_router.system.session_port.register_uploaded_file = AsyncMock()

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
        mock_append_timeline.assert_not_called()
        assert comm.attachments[0]["resource_id"] == "res-persisted-1", (
            "Comm's attachments must already carry the resolved resource_id, not null"
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
        comm_router.system.session_port.data_dir = tmp_path
        comm_router.system.session_port.session_manager.get_session_info = AsyncMock(
            return_value=None
        )
        register_resource_mock = AsyncMock(return_value={"resource_id": "should-not-be-called"})
        comm_router.system.session_port.register_uploaded_resource = register_resource_mock

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
    """Issue #1839 originally added error_message re-fetching to the old
    ensure_ready() mechanism. Issue #2193 PR3's unified _ensure_session_ready()
    never carried that re-fetch logic forward (it returns a plain generic
    "Could not auto-start target minion" on start_session() failure) — dormant
    until PR5 actually routes production traffic through it. These tests now
    pin that generic-message reality instead of the old per-cause detail."""

    def _make_minion(self, state, error_message=None):
        from backend.session_manager import SessionInfo, SessionState

        mock = Mock(spec=SessionInfo)
        mock.session_id = "test-minion-123"
        mock.name = "TestMinion"
        mock.project_id = "test-legion-456"
        mock.state = state if state is not None else SessionState.CREATED
        mock.error_message = error_message
        # Issue #2193 PR5: see legion_system fixture's default_minion comment.
        mock.queue_paused = False
        return mock

    @pytest.mark.asyncio
    async def test_auto_start_failure_surfaces_generic_message(self, comm_router):
        """start_session() returning False surfaces the unified gate's generic
        message — no per-cause detail (issue #1839's old re-fetch is gone)."""
        from backend.session_manager import SessionState

        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(return_value=self._make_minion(SessionState.CREATED))
        comm_router.system.session_port.start_session = AsyncMock(return_value=False)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="Test message",
            comm_type=CommType.TASK,
            auto_start=True,
        )

        with patch.object(comm_router, '_send_system_error_comm', new=AsyncMock()) as mock_error_comm:
            result = await comm_router._send_to_minion(comm)

        assert result is False
        mock_error_comm.assert_called_once_with(
            to_minion_id="sender-minion",
            error_message="Failed to deliver message: Could not auto-start target minion",
            original_comm_id=comm.comm_id,
        )
        assert comm.metadata["delivery_failure_reason"] == "Could not auto-start target minion"

    @pytest.mark.asyncio
    async def test_target_not_found_message_sets_delivery_failure_reason(self, comm_router):
        """AC4 regression, updated for PR5: target-not-found keeps its existing
        generic message text, but now also stamps delivery_failure_reason —
        _fail()'s deliberate widening to every failure path (issue #2193 PR5),
        not a byte-for-byte carry-forward of the pre-PR5 narrower behavior."""
        sm = comm_router.system.session_port.session_manager
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
        assert comm.metadata["delivery_failure_reason"] == "Target minion not found"

    @pytest.mark.asyncio
    async def test_send_message_rejection_message_unchanged(self, comm_router):
        """AC4 regression: send_message() rejection (post auto-start-success
        path) keeps its existing generic message unchanged."""
        from backend.session_manager import SessionState

        sm = comm_router.system.session_port.session_manager
        active_minion = self._make_minion(SessionState.ACTIVE)
        sm.get_session_info = AsyncMock(return_value=active_minion)
        comm_router.system.session_port.send_message = AsyncMock(return_value=False)

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


class TestHaltPivotRunGateBeforeInterrupt:
    """Issue #2189 (coordinator-split PR 10): the auto-start/readiness-wait gate
    (and, as of issue #2193 PR5, every other ordering guarantee — AC5/AC6/AC7/AC8)
    must run before HALT/PIVOT's own handling. That ordering guarantee itself is
    now owned entirely by delivery_gate.deliver() and is already covered by
    test_delivery_gate.py's own ordering/authority tests. These tests are
    retargeted (issue #2193 PR5) to mock deliver() itself and assert only
    _send_to_minion()'s delegation: it calls deliver() exactly once with the
    correctly-built dispatch_content/dispatch_metadata, and correctly relays
    success/failure."""

    @pytest.mark.asyncio
    async def test_halt_to_dormant_target_delegates_to_deliver_with_formatted_dispatch(self, comm_router):
        from backend.delivery_gate import DeliveryResult
        mock_deliver = AsyncMock(return_value=DeliveryResult(success=True))
        comm_router.system.session_port.delivery_gate.deliver = mock_deliver

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="halt now",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.HALT,
        )

        result = await comm_router._send_to_minion(comm)

        assert result is True
        mock_deliver.assert_awaited_once()
        call_args, call_kwargs = mock_deliver.call_args
        assert call_args[0] is comm
        assert "halt now" in call_kwargs["dispatch_content"]
        assert call_kwargs["dispatch_metadata"]["comm"]["content"] == "halt now"

    @pytest.mark.asyncio
    async def test_pivot_to_dormant_target_delegates_to_deliver_with_formatted_dispatch(self, comm_router):
        from backend.delivery_gate import DeliveryResult
        mock_deliver = AsyncMock(return_value=DeliveryResult(success=True))
        comm_router.system.session_port.delivery_gate.deliver = mock_deliver

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="pivot now",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.PIVOT,
        )

        result = await comm_router._send_to_minion(comm)

        assert result is True
        mock_deliver.assert_awaited_once()
        call_args, call_kwargs = mock_deliver.call_args
        assert call_args[0] is comm
        assert "pivot now" in call_kwargs["dispatch_content"]
        assert call_kwargs["dispatch_metadata"]["comm"]["content"] == "pivot now"

    @pytest.mark.asyncio
    async def test_deliver_failure_relayed_and_sends_system_error_comm(self, comm_router):
        from backend.delivery_gate import DeliveryResult
        mock_deliver = AsyncMock(return_value=DeliveryResult(success=False, error="boom"))
        comm_router.system.session_port.delivery_gate.deliver = mock_deliver

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="halt now",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.HALT,
        )

        with patch.object(comm_router, '_send_system_error_comm', new=AsyncMock()) as mock_error_comm:
            result = await comm_router._send_to_minion(comm)

        assert result is False
        mock_error_comm.assert_called_once_with(
            to_minion_id="sender-minion",
            error_message="Failed to deliver message: boom",
            original_comm_id=comm.comm_id,
        )

    @pytest.mark.asyncio
    async def test_sibling_halt_rejected_end_to_end_via_real_deliver(self, comm_router):
        """Regression for the authority-check consolidation (issue #2193 PR5):
        with PR4's tool-layer check removed, a sibling's HALT routed all the way
        through _send_to_minion() with a REAL (not mocked) deliver() call must
        still be rejected — now via deliver()'s own AC7 check alone."""
        from backend.session_manager import SessionInfo, SessionState

        sibling = Mock(spec=SessionInfo)
        sibling.session_id = "test-minion-123"
        sibling.name = "TestMinion"
        sibling.project_id = "test-legion-456"
        sibling.state = SessionState.ACTIVE
        sibling.queue_paused = False
        sibling.parent_overseer_id = None
        comm_router.system.session_port.session_manager.get_session_info = AsyncMock(return_value=sibling)
        comm_router.system.session_port.interrupt_session = AsyncMock(return_value=True)
        # from_minion_id "sender-minion" has no parent_overseer_id wired to
        # "test-minion-123" — is_ancestor() correctly finds no relationship.

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="halt now",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.HALT,
        )

        with patch.object(comm_router, '_send_system_error_comm', new=AsyncMock()) as mock_error_comm:
            result = await comm_router._send_to_minion(comm)

        assert result is False
        comm_router.system.session_port.interrupt_session.assert_not_awaited()
        error_message_arg = mock_error_comm.call_args.kwargs["error_message"]
        assert "authority" in error_message_arg.lower() or "ancestor" in error_message_arg.lower()


class TestHaltPivotAuthorityBeforeSideEffects:
    """Issue #2228: an unauthorized sibling's HALT/PIVOT must be rejected by
    route_comm() itself, before _deliver_attachments()/_persist_comm() ever run —
    restoring the pre-#2193-PR5 "zero side effects on rejection" behavior.
    DeliveryGate._is_authorized() still blocks the actual interrupt/pivot action
    (covered by test_delivery_gate.py); these tests cover the new, earlier
    side-effect-prevention gate in route_comm()."""

    @pytest.mark.asyncio
    async def test_unauthorized_sibling_halt_rejected_with_no_side_effects_via_route_comm(self, comm_router):
        from backend.session_manager import SessionInfo, SessionState

        target = Mock(spec=SessionInfo)
        target.session_id = "test-minion-123"
        target.name = "TestMinion"
        target.project_id = "test-legion-456"
        target.state = SessionState.ACTIVE
        target.queue_paused = False
        target.parent_overseer_id = None  # not an ancestor of sender
        comm_router.system.session_port.session_manager.get_session_info = AsyncMock(return_value=target)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sibling-sender",
            to_minion_id="test-minion-123",
            content="halt now",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.HALT,
        )

        with (
            patch.object(comm_router, "_deliver_attachments", new=AsyncMock()) as mock_deliver_attachments,
            patch.object(comm_router, "_persist_comm", new=AsyncMock()) as mock_persist_comm,
        ):
            result = await comm_router.route_comm(comm)

        assert result is False
        mock_deliver_attachments.assert_not_called()
        mock_persist_comm.assert_not_called()
        assert "ancestor" in comm.metadata["delivery_failure_reason"]

    @pytest.mark.asyncio
    async def test_unauthorized_sibling_pivot_rejected_with_no_side_effects_via_route_comm(self, comm_router):
        from backend.session_manager import SessionInfo, SessionState

        target = Mock(spec=SessionInfo)
        target.session_id = "test-minion-123"
        target.name = "TestMinion"
        target.project_id = "test-legion-456"
        target.state = SessionState.ACTIVE
        target.queue_paused = False
        target.parent_overseer_id = None  # not an ancestor of sender
        comm_router.system.session_port.session_manager.get_session_info = AsyncMock(return_value=target)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sibling-sender",
            to_minion_id="test-minion-123",
            content="pivot now",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.PIVOT,
        )

        with (
            patch.object(comm_router, "_deliver_attachments", new=AsyncMock()) as mock_deliver_attachments,
            patch.object(comm_router, "_persist_comm", new=AsyncMock()) as mock_persist_comm,
        ):
            result = await comm_router.route_comm(comm)

        assert result is False
        mock_deliver_attachments.assert_not_called()
        mock_persist_comm.assert_not_called()
        assert "ancestor" in comm.metadata["delivery_failure_reason"]

    @pytest.mark.asyncio
    async def test_unauthorized_sibling_halt_with_attachment_does_not_write_or_register_resource(
        self, comm_router, tmp_path
    ):
        """Routes through the REAL (non-mocked) _deliver_attachments()/_persist_comm() —
        proves no file is written and no resource is registered, not just that mocked
        methods weren't invoked."""
        from backend.session_manager import SessionInfo, SessionState

        comm_router.system.session_port.data_dir = tmp_path

        target = Mock(spec=SessionInfo)
        target.session_id = "test-minion-123"
        target.name = "TestMinion"
        target.project_id = "test-legion-456"
        target.state = SessionState.ACTIVE
        target.queue_paused = False
        target.parent_overseer_id = None  # not an ancestor of sender
        comm_router.system.session_port.session_manager.get_session_info = AsyncMock(return_value=target)

        register_resource_mock = AsyncMock(return_value={"resource_id": "should-not-be-called"})
        comm_router.system.session_port.register_uploaded_resource = register_resource_mock
        comm_router.system.session_port.register_uploaded_file = AsyncMock()

        source_file = tmp_path / "secret.txt"
        source_file.write_bytes(b"attachment contents")

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sibling-sender",
            to_minion_id="test-minion-123",
            content="halt now",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.HALT,
            attachments=[
                {
                    "name": "secret.txt",
                    "size": 20,
                    "mime_type": "text/plain",
                    "source_path": str(source_file),
                }
            ],
        )

        result = await comm_router.route_comm(comm)

        assert result is False
        register_resource_mock.assert_not_called()
        assert comm.attachments[0].get("resource_id") is None
        assert not (tmp_path / "sessions" / "test-minion-123" / "attachments").exists(), (
            "No attachment file should be written for a rejected unauthorized HALT"
        )

    @pytest.mark.asyncio
    async def test_authorized_ancestor_halt_still_persists_and_delivers_attachment_in_order(
        self, comm_router, tmp_path
    ):
        """Positive-control regression: an authorized ancestor's HALT must still reach
        _deliver_attachments() before _persist_comm() (issue #1730's ordering guarantee,
        untouched by this fix) and must still actually interrupt the target session."""
        from backend.session_manager import SessionInfo, SessionState

        comm_router.system.session_port.data_dir = tmp_path

        parent = Mock(spec=SessionInfo)
        parent.parent_overseer_id = None

        child = Mock(spec=SessionInfo)
        child.session_id = "test-minion-123"
        child.name = "TestMinion"
        child.project_id = "test-legion-456"
        child.state = SessionState.ACTIVE
        child.queue_paused = False
        child.parent_overseer_id = "parent-id"

        session_map = {"parent-id": parent, "test-minion-123": child}
        sm = comm_router.system.session_port.session_manager
        sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

        comm_router.system.session_port.interrupt_session = AsyncMock(return_value=True)
        comm_router.system.session_port.register_uploaded_resource = AsyncMock(
            return_value={"resource_id": "res-authorized-1"}
        )
        comm_router.system.session_port.register_uploaded_file = AsyncMock()

        source_file = tmp_path / "notes.txt"
        source_file.write_bytes(b"attachment contents")

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="parent-id",
            to_minion_id="test-minion-123",
            content="halt now",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.HALT,
            attachments=[
                {
                    "name": "notes.txt",
                    "size": 20,
                    "mime_type": "text/plain",
                    "source_path": str(source_file),
                }
            ],
        )

        call_order = []
        original_deliver_attachments = comm_router._deliver_attachments
        original_persist_comm = comm_router._persist_comm

        async def tracked_deliver_attachments(*args, **kwargs):
            call_order.append("deliver_attachments")
            return await original_deliver_attachments(*args, **kwargs)

        async def tracked_persist_comm(*args, **kwargs):
            call_order.append("persist_comm")
            return await original_persist_comm(*args, **kwargs)

        with (
            patch.object(comm_router, "_deliver_attachments", side_effect=tracked_deliver_attachments),
            patch.object(comm_router, "_persist_comm", side_effect=tracked_persist_comm),
        ):
            result = await comm_router.route_comm(comm)

        assert result is True
        assert call_order == ["deliver_attachments", "persist_comm"]
        assert comm.attachments[0]["resource_id"] == "res-authorized-1"
        comm_router.system.session_port.interrupt_session.assert_awaited_once_with("test-minion-123")

    @pytest.mark.asyncio
    async def test_user_sent_halt_bypasses_new_early_exit(self, comm_router):
        """from_user=True is always authorized (is_authorized_sender()'s own rule) —
        confirms the new early-exit never rejects a user-originated HALT/PIVOT."""
        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_user=True,
            to_minion_id="test-minion-123",
            content="halt now",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.HALT,
        )

        with (
            patch.object(comm_router, "_deliver_attachments", new=AsyncMock()) as mock_deliver_attachments,
            patch.object(comm_router, "_persist_comm", new=AsyncMock()) as mock_persist_comm,
            patch.object(comm_router, "_send_to_minion", new=AsyncMock(return_value=True)) as mock_send_to_minion,
        ):
            result = await comm_router.route_comm(comm)

        assert result is True
        mock_deliver_attachments.assert_called_once()
        mock_persist_comm.assert_called_once()
        mock_send_to_minion.assert_called_once()
        assert "delivery_failure_reason" not in comm.metadata

    @pytest.mark.asyncio
    async def test_halt_to_nonexistent_target_not_misreported_as_unauthorized(self, comm_router):
        """Review finding (issue #2228): is_ancestor()'s hierarchy walk starts from
        comm.to_minion_id's own session info and returns False if that session is
        missing — indistinguishable, from is_authorized_sender()'s return value
        alone, from "no ancestor relationship." Production's deliver() never
        confuses the two because it checks session_dir before its own authority
        check; the new gate must preserve that ordering by falling through to the
        existing missing-target handling instead of misreporting a disposal race
        as an authority failure."""
        comm_router.system.session_port.session_manager.get_session_info = AsyncMock(return_value=None)

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="ancestor-sender",
            to_minion_id="disposed-minion",
            content="halt now",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.HALT,
        )

        with (
            patch.object(comm_router, "_deliver_attachments", new=AsyncMock()) as mock_deliver_attachments,
            patch.object(comm_router, "_persist_comm", new=AsyncMock()) as mock_persist_comm,
            patch.object(comm_router, "_send_to_minion", new=AsyncMock(return_value=False)) as mock_send_to_minion,
        ):
            result = await comm_router.route_comm(comm)

        assert result is False
        mock_deliver_attachments.assert_called_once()
        mock_persist_comm.assert_called_once()
        mock_send_to_minion.assert_called_once()
        assert comm.metadata.get("delivery_failure_reason") != (
            "HALT requires the sender to be the user or an ancestor of the recipient"
        )


class TestPausedTargetDeliversWithoutForceStart:
    """Issue #1918 stopped comm delivery from force-starting a target minion PAUSED
    mid-permission-wait (e.g. AskUserQuestion) — that clobbers it into STARTING with
    no way back. Issue #1940: that fix over-scoped the protection into skipping
    delivery entirely. The PAUSED-is-already-ready ordering guarantee itself is now
    owned by delivery_gate.deliver() (covered by test_delivery_gate.py's
    TestReadinessUnification::test_paused_session_ready_without_waiting) —
    retargeted (issue #2193 PR5) to mock deliver() and assert only
    _send_to_minion()'s delegation."""

    @pytest.mark.asyncio
    async def test_paused_target_delegates_to_deliver_and_relays_success(self, comm_router):
        from backend.delivery_gate import DeliveryResult
        mock_deliver = AsyncMock(return_value=DeliveryResult(success=True))
        comm_router.system.session_port.delivery_gate.deliver = mock_deliver

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="Test message",
            comm_type=CommType.TASK,
        )

        with patch.object(comm_router, '_send_system_error_comm', new=AsyncMock()) as mock_error_comm:
            result = await comm_router._send_to_minion(comm)

        assert result is True
        mock_deliver.assert_awaited_once()
        mock_error_comm.assert_not_called()

    @pytest.mark.asyncio
    async def test_active_target_still_delegates_to_deliver_unchanged(self, comm_router):
        """Regression: ACTIVE targets go through the same single deliver() call."""
        from backend.delivery_gate import DeliveryResult
        mock_deliver = AsyncMock(return_value=DeliveryResult(success=True))
        comm_router.system.session_port.delivery_gate.deliver = mock_deliver

        comm = Comm(
            comm_id=str(uuid.uuid4()),
            from_minion_id="sender-minion",
            to_minion_id="test-minion-123",
            content="Test message",
            comm_type=CommType.TASK,
        )

        result = await comm_router._send_to_minion(comm)

        assert result is True
        mock_deliver.assert_awaited_once()
        assert "delivery_failure_reason" not in comm.metadata
