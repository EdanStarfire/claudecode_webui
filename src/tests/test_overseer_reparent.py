"""
Tests for OverseerController.reparent_minion() — issue #1422.

Covers all seven scenarios from the plan plus edge cases:
  Scenario 1: User moves grandchild to sibling branch
  Scenario 2: User promotes nested minion to root
  Scenario 3: Ancestor A reparents child B under sibling C (MCP)
  Scenario 4: Ancestor A pulls grandchild D directly under itself (MCP)
  Scenario 5: MCP attempt outside subtree → ValueError
  Scenario 6: Self-reparent → ValueError
  Scenario 7: Cycle attempt → ValueError
  Edge: No-op (new_parent == current parent) → success, no timeline entry
  Edge: Leaf reparent works
  Edge: Cross-legion reparent → ValueError
  Edge: overseer_level correctly recomputed for moved subtree
  Edge: is_overseer flips correctly on old and new parent
"""

from unittest.mock import AsyncMock, Mock

import pytest

from src.legion.overseer_controller import OverseerController
from src.models.legion_models import CommType
from src.session_manager import SessionInfo, SessionState
from src.slug_utils import slugify_name

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_session(
    session_id,
    name,
    project_id="legion-1",
    parent_id=None,
    children=None,
    is_overseer=False,
    overseer_level=0,
):
    s = Mock(spec=SessionInfo)
    s.session_id = session_id
    s.name = name
    s.slug = slugify_name(name)
    s.project_id = project_id
    s.parent_overseer_id = parent_id
    s.child_minion_ids = list(children or [])
    s.is_overseer = is_overseer
    s.overseer_level = overseer_level
    s.state = SessionState.ACTIVE
    s.is_processing = False
    s.working_directory = "/tmp/test"
    s.role = "worker"
    return s


def _build_system(session_map):
    """
    Build a mock LegionSystem with session_manager, comm_router, and
    project_manager wired to the given session_map dict.
    """
    sm = Mock()
    sm.get_session_info = AsyncMock(side_effect=lambda sid: session_map.get(sid))

    async def _update_session(sid, **kwargs):
        s = session_map.get(sid)
        if s:
            for k, v in kwargs.items():
                setattr(s, k, v)
        return True

    sm.update_session = AsyncMock(side_effect=_update_session)

    async def _get_descendants_mock(session_id, limit=50, offset=0):
        result = []
        queue = list(session_map[session_id].child_minion_ids) if session_map.get(session_id) else []
        while queue:
            sid = queue.pop(0)
            s = session_map.get(sid)
            if s:
                result.append({"session_id": sid})
                queue.extend(s.child_minion_ids or [])
        return {"descendants": result, "total": len(result), "limit": limit, "offset": offset, "has_more": False}

    async def _get_all_descendants_mock(session_id):
        result = []
        queue = list(session_map[session_id].child_minion_ids) if session_map.get(session_id) else []
        while queue:
            sid = queue.pop(0)
            s = session_map.get(sid)
            if s:
                result.append({"session_id": sid})
                queue.extend(s.child_minion_ids or [])
        return result

    project = Mock()
    project.to_dict = Mock(return_value={"project_id": "legion-1"})

    pm = Mock()
    pm.get_project = AsyncMock(return_value=project)

    coord = Mock()
    coord.get_descendants = AsyncMock(side_effect=_get_descendants_mock)
    coord.get_all_descendants = AsyncMock(side_effect=_get_all_descendants_mock)
    coord.session_manager = sm
    coord.project_manager = pm

    comm_router = Mock()
    comm_router.route_comm = AsyncMock(return_value=True)

    system = Mock()
    system.session_coordinator = coord
    system.comm_router = comm_router
    system.broadcast_ui_event = Mock()

    return system


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 1: User moves grandchild C under sibling A
#   Before: root → A → B → C
#   After:  root → A → B, root → A → C  (C becomes direct child of A)
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario1GrandchildToSibling:
    @pytest.mark.asyncio
    async def test_reparent_grandchild_to_sibling_branch(self):
        a = _make_session("a-id", "A", parent_id=None, children=["b-id"], is_overseer=True, overseer_level=0)
        b = _make_session("b-id", "B", parent_id="a-id", children=["c-id"], is_overseer=True, overseer_level=1)
        c = _make_session("c-id", "C", parent_id="b-id", children=[], is_overseer=False, overseer_level=2)
        session_map = {"a-id": a, "b-id": b, "c-id": c}

        system = _build_system(session_map)
        oc = OverseerController(system)

        result = await oc.reparent_minion(subject_id="c-id", new_parent_id="a-id", caller_id=None)

        assert result["success"] is True
        assert result["old_parent_id"] == "b-id"
        assert result["new_parent_id"] == "a-id"
        assert result["descendants_moved"] == 0

        # C's parent pointer updated
        assert c.parent_overseer_id == "a-id"
        # C added to A's children
        assert "c-id" in a.child_minion_ids
        # C removed from B's children
        assert "c-id" not in b.child_minion_ids
        # B still has no children → should have is_overseer flipped False
        assert b.is_overseer is False
        # A stays overseer
        assert a.is_overseer is True
        # Level recomputed: A=0, C (child of A) = 1
        assert c.overseer_level == 1

        # Timeline REPARENT comm emitted
        system.comm_router.route_comm.assert_awaited_once()
        comm = system.comm_router.route_comm.call_args[0][0]
        assert comm.comm_type == CommType.REPARENT
        assert comm.to_user is True

        # UI event broadcast
        system.broadcast_ui_event.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 2: User promotes nested minion B to root
#   Before: root → A → B
#   After:  root → A, root → B (B and A are siblings at root)
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario2PromoteToRoot:
    @pytest.mark.asyncio
    async def test_promote_nested_minion_to_root(self):
        a = _make_session("a-id", "A", parent_id=None, children=["b-id"], is_overseer=True, overseer_level=0)
        b = _make_session("b-id", "B", parent_id="a-id", children=[], is_overseer=False, overseer_level=1)
        session_map = {"a-id": a, "b-id": b}

        system = _build_system(session_map)
        oc = OverseerController(system)

        result = await oc.reparent_minion(subject_id="b-id", new_parent_id=None, caller_id=None)

        assert result["success"] is True
        assert result["old_parent_id"] == "a-id"
        assert result["new_parent_id"] is None

        # B promoted to root (parent_overseer_id = None)
        assert b.parent_overseer_id is None
        # A no longer has B as child
        assert "b-id" not in a.child_minion_ids
        # A has no children → is_overseer set False
        assert a.is_overseer is False
        # B at root level = 0
        assert b.overseer_level == 0


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 3: Ancestor A reparents direct child B under sibling C (MCP)
#   Hierarchy: A → B, A → C   →   A → C → B
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario3McpReparentChildUnderSibling:
    @pytest.mark.asyncio
    async def test_mcp_reparent_child_under_sibling(self):
        a = _make_session("a-id", "A", parent_id=None, children=["b-id", "c-id"], is_overseer=True, overseer_level=0)
        b = _make_session("b-id", "B", parent_id="a-id", children=[], is_overseer=False, overseer_level=1)
        c = _make_session("c-id", "C", parent_id="a-id", children=[], is_overseer=False, overseer_level=1)
        session_map = {"a-id": a, "b-id": b, "c-id": c}

        system = _build_system(session_map)
        oc = OverseerController(system)

        result = await oc.reparent_minion(subject_id="b-id", new_parent_id="c-id", caller_id="a-id")

        assert result["success"] is True
        assert b.parent_overseer_id == "c-id"
        assert "b-id" not in a.child_minion_ids
        assert "b-id" in c.child_minion_ids
        assert c.is_overseer is True
        # B's level: C is level 1, so B becomes level 2
        assert b.overseer_level == 2


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 4: Ancestor A pulls grandchild D directly under itself (MCP)
#   Before: A → B → D   →   After: A → B, A → D
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario4McpPullGrandchildUp:
    @pytest.mark.asyncio
    async def test_mcp_pull_grandchild_directly_under_ancestor(self):
        a = _make_session("a-id", "A", parent_id=None, children=["b-id"], is_overseer=True, overseer_level=0)
        b = _make_session("b-id", "B", parent_id="a-id", children=["d-id"], is_overseer=True, overseer_level=1)
        d = _make_session("d-id", "D", parent_id="b-id", children=[], is_overseer=False, overseer_level=2)
        session_map = {"a-id": a, "b-id": b, "d-id": d}

        system = _build_system(session_map)
        oc = OverseerController(system)

        result = await oc.reparent_minion(subject_id="d-id", new_parent_id="a-id", caller_id="a-id")

        assert result["success"] is True
        assert d.parent_overseer_id == "a-id"
        assert "d-id" not in b.child_minion_ids
        assert "d-id" in a.child_minion_ids
        # B now has no children → is_overseer flipped False
        assert b.is_overseer is False
        # D's level = A's level + 1 = 0 + 1 = 1
        assert d.overseer_level == 1


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 5: MCP attempt to reparent minion outside subtree → ValueError
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario5McpAuthorityViolation:
    @pytest.mark.asyncio
    async def test_mcp_cannot_reparent_outside_subtree(self):
        """Caller A tries to reparent sibling X (not in A's subtree)."""
        a = _make_session("a-id", "A", parent_id=None, children=[], is_overseer=False, overseer_level=0)
        x = _make_session("x-id", "X", parent_id=None, children=[], is_overseer=False, overseer_level=0)
        session_map = {"a-id": a, "x-id": x}

        system = _build_system(session_map)
        oc = OverseerController(system)

        with pytest.raises(ValueError, match="Authority violation"):
            await oc.reparent_minion(subject_id="x-id", new_parent_id="a-id", caller_id="a-id")


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 6: Self-reparent → ValueError
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario6SelfReparent:
    @pytest.mark.asyncio
    async def test_self_reparent_raises(self):
        a = _make_session("a-id", "A")
        session_map = {"a-id": a}

        system = _build_system(session_map)
        oc = OverseerController(system)

        with pytest.raises(ValueError, match="Cannot reparent a minion to itself"):
            await oc.reparent_minion(subject_id="a-id", new_parent_id="a-id", caller_id=None)


# ──────────────────────────────────────────────────────────────────────────────
# Scenario 7: Cycle attempt (move ancestor into its own descendant) → ValueError
# ──────────────────────────────────────────────────────────────────────────────

class TestScenario7CycleDetection:
    @pytest.mark.asyncio
    async def test_cycle_reparent_raises(self):
        """Try to make A a child of B where B is already a child of A."""
        a = _make_session("a-id", "A", parent_id=None, children=["b-id"], is_overseer=True, overseer_level=0)
        b = _make_session("b-id", "B", parent_id="a-id", children=[], is_overseer=False, overseer_level=1)
        session_map = {"a-id": a, "b-id": b}

        system = _build_system(session_map)
        oc = OverseerController(system)

        with pytest.raises(ValueError, match="cycle"):
            await oc.reparent_minion(subject_id="a-id", new_parent_id="b-id", caller_id=None)


# ──────────────────────────────────────────────────────────────────────────────
# Edge: No-op — new_parent_id equals current parent
# ──────────────────────────────────────────────────────────────────────────────

class TestEdgeNoOp:
    @pytest.mark.asyncio
    async def test_noop_returns_success_without_timeline_entry(self):
        a = _make_session("a-id", "A", parent_id=None, children=["b-id"], is_overseer=True, overseer_level=0)
        b = _make_session("b-id", "B", parent_id="a-id", children=[], is_overseer=False, overseer_level=1)
        session_map = {"a-id": a, "b-id": b}

        system = _build_system(session_map)
        oc = OverseerController(system)

        # B is already a child of A — same parent
        result = await oc.reparent_minion(subject_id="b-id", new_parent_id="a-id", caller_id=None)

        assert result["success"] is True
        assert result["descendants_moved"] == 0
        # No timeline event emitted for a no-op
        system.comm_router.route_comm.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────────────────
# Edge: Cross-legion reparent → ValueError
# ──────────────────────────────────────────────────────────────────────────────

class TestEdgeCrossLegion:
    @pytest.mark.asyncio
    async def test_cross_legion_reparent_raises(self):
        subject = _make_session("s-id", "Subject", project_id="legion-A")
        target_parent = _make_session("t-id", "Target", project_id="legion-B")
        session_map = {"s-id": subject, "t-id": target_parent}

        system = _build_system(session_map)
        oc = OverseerController(system)

        with pytest.raises(ValueError, match="different legion"):
            await oc.reparent_minion(subject_id="s-id", new_parent_id="t-id", caller_id=None)


# ──────────────────────────────────────────────────────────────────────────────
# Edge: overseer_level recomputed for entire moved subtree
# ──────────────────────────────────────────────────────────────────────────────

class TestEdgeSubtreeLevelRecomputation:
    @pytest.mark.asyncio
    async def test_overseer_level_recomputed_for_full_subtree(self):
        """
        Before: root(0) → A(0) → B(1) → C(2) → D(3)
        Move B under root (level 0), expecting B=0, C=1, D=2.
        """
        a = _make_session("a-id", "A", parent_id=None, children=["b-id"], is_overseer=True, overseer_level=0)
        b = _make_session("b-id", "B", parent_id="a-id", children=["c-id"], is_overseer=True, overseer_level=1)
        c = _make_session("c-id", "C", parent_id="b-id", children=["d-id"], is_overseer=True, overseer_level=2)
        d = _make_session("d-id", "D", parent_id="c-id", children=[], is_overseer=False, overseer_level=3)
        session_map = {"a-id": a, "b-id": b, "c-id": c, "d-id": d}

        system = _build_system(session_map)
        oc = OverseerController(system)

        # Promote B to root
        result = await oc.reparent_minion(subject_id="b-id", new_parent_id=None, caller_id=None)

        assert result["success"] is True
        assert result["descendants_moved"] == 2  # C and D

        # Level recomputation
        assert b.overseer_level == 0
        assert c.overseer_level == 1
        assert d.overseer_level == 2

        # A lost B, so is_overseer flipped
        assert a.is_overseer is False


# ──────────────────────────────────────────────────────────────────────────────
# Edge: MCP cannot promote to root (user-only)
# ──────────────────────────────────────────────────────────────────────────────

class TestEdgeMcpCannotPromoteToRoot:
    @pytest.mark.asyncio
    async def test_mcp_promote_to_root_raises(self):
        caller = _make_session("caller-id", "Caller", parent_id=None, children=["child-id"], is_overseer=True)
        child = _make_session("child-id", "Child", parent_id="caller-id", children=[])
        session_map = {"caller-id": caller, "child-id": child}

        system = _build_system(session_map)
        oc = OverseerController(system)

        with pytest.raises(ValueError, match="root level"):
            await oc.reparent_minion(subject_id="child-id", new_parent_id=None, caller_id="caller-id")


# ──────────────────────────────────────────────────────────────────────────────
# Edge: Leaf reparent (subject has no children)
# ──────────────────────────────────────────────────────────────────────────────

class TestEdgeLeafReparent:
    @pytest.mark.asyncio
    async def test_leaf_reparent_works(self):
        a = _make_session("a-id", "A", parent_id=None, children=["b-id", "c-id"], is_overseer=True, overseer_level=0)
        b = _make_session("b-id", "B", parent_id="a-id", children=[], is_overseer=False, overseer_level=1)
        c = _make_session("c-id", "C", parent_id="a-id", children=[], is_overseer=False, overseer_level=1)
        session_map = {"a-id": a, "b-id": b, "c-id": c}

        system = _build_system(session_map)
        oc = OverseerController(system)

        result = await oc.reparent_minion(subject_id="b-id", new_parent_id="c-id", caller_id=None)

        assert result["success"] is True
        assert result["descendants_moved"] == 0
        assert b.parent_overseer_id == "c-id"
        assert "b-id" not in a.child_minion_ids
        assert "b-id" in c.child_minion_ids
        assert c.is_overseer is True


# ──────────────────────────────────────────────────────────────────────────────
# Edge: MCP new_parent outside caller's subtree → ValueError
# ──────────────────────────────────────────────────────────────────────────────

class TestEdgeMcpNewParentOutsideSubtree:
    @pytest.mark.asyncio
    async def test_mcp_new_parent_outside_subtree_raises(self):
        """Caller A owns child B; tries to move B under unrelated X."""
        a = _make_session("a-id", "A", parent_id=None, children=["b-id"], is_overseer=True, overseer_level=0)
        b = _make_session("b-id", "B", parent_id="a-id", children=[], is_overseer=False, overseer_level=1)
        x = _make_session("x-id", "X", parent_id=None, children=[], is_overseer=False, overseer_level=0)
        session_map = {"a-id": a, "b-id": b, "x-id": x}

        system = _build_system(session_map)
        oc = OverseerController(system)

        with pytest.raises(ValueError, match="Authority violation"):
            await oc.reparent_minion(subject_id="b-id", new_parent_id="x-id", caller_id="a-id")


# ──────────────────────────────────────────────────────────────────────────────
# Issue #1581 regression: large subtrees (>50 descendants)
# ──────────────────────────────────────────────────────────────────────────────

class TestIssue1581LargeSubtree:
    @pytest.mark.asyncio
    async def test_issue_1581_cycle_detection_beyond_page(self):
        """
        Cycle detection must catch a proposed new_parent that lives beyond
        position 50 in the subject's descendant list.
        """
        # Build subject with 60 linear descendants: s → d1 → d2 → … → d60
        subject = _make_session("s-id", "Subject", parent_id=None, children=["d1"], is_overseer=True, overseer_level=0)
        sessions = {"s-id": subject}
        prev = subject
        for i in range(1, 61):
            sid = f"d{i}"
            next_id = f"d{i + 1}" if i < 60 else None
            children = [next_id] if next_id else []
            s = _make_session(sid, f"Desc{i}", parent_id=prev.session_id, children=children, is_overseer=bool(children), overseer_level=i)
            sessions[sid] = s
            prev = s

        system = _build_system(sessions)
        oc = OverseerController(system)

        # d55 is beyond page limit 50; using it as new_parent would create a cycle
        with pytest.raises(ValueError, match="cycle"):
            await oc.reparent_minion(subject_id="s-id", new_parent_id="d55", caller_id=None)

    @pytest.mark.asyncio
    async def test_issue_1581_authority_check_still_rejects_outsider(self):
        """
        Authority check must still reject a subject that is genuinely outside
        the caller's subtree, even when the caller has >50 descendants.
        """
        # Caller has 60 linear descendants
        caller = _make_session("caller-id", "Caller", parent_id=None, children=["d1"], is_overseer=True, overseer_level=0)
        sessions = {"caller-id": caller}
        prev = caller
        for i in range(1, 61):
            sid = f"d{i}"
            next_id = f"d{i + 1}" if i < 60 else None
            children = [next_id] if next_id else []
            s = _make_session(sid, f"Desc{i}", parent_id=prev.session_id, children=children, is_overseer=bool(children), overseer_level=i)
            sessions[sid] = s
            prev = s

        # outsider is NOT in caller's subtree
        outsider = _make_session("out-id", "Outsider", parent_id=None, children=[], is_overseer=False, overseer_level=0)
        sessions["out-id"] = outsider

        system = _build_system(sessions)
        oc = OverseerController(system)

        with pytest.raises(ValueError, match="Authority violation"):
            await oc.reparent_minion(subject_id="out-id", new_parent_id="d1", caller_id="caller-id")
