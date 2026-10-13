"""AC3/T2 (epic #1990 stage 6b, issue #2159): every external caller of session
lifecycle state converges on the shared session_lifecycle predicates instead of a
hand-rolled state tuple/raw-string comparison.

T2 exercises the actual predicate used by each caller's own import, confirming
agreement with session_lifecycle.is_deliverable()/is_interruptible() directly rather
than re-deriving the expected answer independently (which would just be testing the
test). AC3 is a regression guard, not a one-time audit: source-scans every file 6b
migrated for the specific hand-rolled patterns the plan's investigation found there
(`[SessionState.ACTIVE]`, `(SessionState.ACTIVE, SessionState.PAUSED)`,
`.state.value == "active"`), so a future change re-introducing one fails this test
immediately — same convention as shared/tests/test_event_registry_completeness.py's
source-scanning regression guards.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.session_lifecycle import SessionState, is_deliverable, is_interruptible

REPO_ROOT = Path(__file__).resolve().parents[2]

# Files 6b migrated onto the shared predicates (plan's verified call-site inventory).
MIGRATED_FILES = [
    "backend/routers/session_runtime.py",
    "backend/routers/sessions.py",
    "backend/session_coordinator.py",
    "backend/queue_processor.py",
    "backend/legion/comm_router.py",
    "backend/legion/legion_coordinator.py",
    "backend/legion/scheduler_service.py",
]

# Patterns found during the plan's investigation — a literal state tuple/list/set
# standing in for is_deliverable()/is_interruptible(), or a raw-string `.value`
# comparison standing in for proper enum membership.
HAND_ROLLED_PATTERNS = [
    re.compile(r"\[\s*SessionState\.ACTIVE\s*\]"),
    re.compile(r"\(\s*SessionState\.ACTIVE\s*,\s*SessionState\.PAUSED\s*\)"),
    re.compile(r"\[\s*SessionState\.ACTIVE\s*,\s*SessionState\.PAUSED\s*\]"),
    re.compile(r"\{\s*SessionState\.ACTIVE\s*,\s*SessionState\.PAUSED\s*\}"),
    re.compile(r"""\.state\.value\s*==\s*["']active["']"""),
]


class TestAC3NoHandRolledStateTuples:
    @pytest.mark.parametrize("rel_path", MIGRATED_FILES)
    def test_file_has_no_hand_rolled_predicate(self, rel_path: str):
        source = (REPO_ROOT / rel_path).read_text()
        for pattern in HAND_ROLLED_PATTERNS:
            match = pattern.search(source)
            assert match is None, (
                f"{rel_path} contains a hand-rolled state-tuple pattern "
                f"({pattern.pattern!r}: {match.group(0) if match else ''!r}) — "
                f"use session_lifecycle.is_deliverable()/is_interruptible() instead"
            )


class TestT2PredicateConsistency:
    """Every caller's own is_deliverable/is_interruptible import agrees with the
    canonical predicate for every state — proving there's exactly one definition
    in use, not N independently-evolving copies that happen to agree today."""

    @pytest.mark.parametrize(
        "module_path,import_name",
        [
            ("backend.routers.sessions", "is_deliverable"),
            ("backend.legion.comm_router", "is_deliverable"),
            ("backend.legion.legion_coordinator", "is_deliverable"),
            ("backend.legion.scheduler_service", "is_deliverable"),
            ("backend.legion.scheduler_service", "is_auto_startable"),
            ("backend.session_coordinator", "is_interruptible"),
        ],
    )
    def test_caller_predicate_is_the_canonical_one(self, module_path: str, import_name: str):
        import importlib

        module = importlib.import_module(module_path)
        caller_predicate = getattr(module, import_name)

        from backend import session_lifecycle

        canonical_predicate = getattr(session_lifecycle, import_name)

        # Identity, not just behavioral agreement — proves it's the same function
        # object (a real shared import), not a same-named local re-implementation.
        assert caller_predicate is canonical_predicate

        for state in SessionState:
            assert caller_predicate(state) == canonical_predicate(state)

    def test_is_deliverable_matches_active_or_paused(self):
        for state in SessionState:
            expected = state in (SessionState.ACTIVE, SessionState.PAUSED)
            assert is_deliverable(state) == expected

    def test_is_interruptible_matches_active_or_paused(self):
        for state in SessionState:
            expected = state in (SessionState.ACTIVE, SessionState.PAUSED)
            assert is_interruptible(state) == expected

    def test_restart_session_deliberately_not_is_deliverable(self):
        """legion_mcp_tools.py's restart_session keeps strict ACTIVE-only — a
        deliberate divergence (restarting a live turn is a different predicate
        than deliverability), not a missed migration. This pins that choice: if
        someone "fixes" it to use is_deliverable(), this test documents why not."""
        source = (
            REPO_ROOT / "backend/legion/mcp/legion_mcp_tools.py"
        ).read_text()
        assert "session_info.state != SessionState.ACTIVE" in source
        assert "is_deliverable(" not in source
