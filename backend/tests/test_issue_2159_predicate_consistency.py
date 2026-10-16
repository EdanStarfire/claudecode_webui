"""AC3/T2 (epic #1990 stage 6b, issue #2159) + AC4 (issue #2184, stage 6 residuals):
every external caller of session lifecycle state converges on the shared
session_lifecycle predicates instead of a hand-rolled state tuple/raw-string
comparison.

T2 exercises the actual predicate used by each caller's own import, confirming
agreement with session_lifecycle.is_deliverable()/is_interruptible() directly rather
than re-deriving the expected answer independently (which would just be testing the
test).

AC4 replaces the narrower 3-regex/5-file hand-rolled-pattern scan (issue #2159's
original AC3) with a generic regex for any ``SessionState.\\w+`` comparison anywhere
under ``backend/`` (outside ``session_lifecycle.py`` and ``tests/``), paired with an
explicit, documented ALLOWLIST of the legitimate single-state terminal checks that
don't correspond to any sensible boolean predicate (confirmed during the issue #2184
investigation — mostly gating/guard checks like "only allow this live SDK operation
when ACTIVE" or "skip if already TERMINATED", not duplicated deliverability/
interruptibility logic). Anything NOT in the allowlist fails loudly — same
source-scanning regression-guard convention as
shared/tests/test_event_registry_completeness.py.

AC8 (issue #2189, epic #1990 stage 7 sub-stage ac89) broadens the scan again to also
catch string-valued duplicates of the same comparisons — ``.state.value ==``/``!=``/
``in (...)`` — scoped specifically to ``.state.value`` and not bare ``.value``, since
confirmed-unrelated enums elsewhere (``ToolCall.status.value``,
``ParsedMessage.type.value``) also appear in comparison-adjacent contexts under
``backend/`` and must stay out of scope.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.session_lifecycle import (
    SessionState,
    is_deliverable,
    is_interruptible,
    is_live,
    is_not_terminated,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"

# Any SessionState.X appearing in a comparison (==, !=) or tuple/list/set membership
# ("in (...)") context — catches both orderings (`x == SessionState.Y` and
# `SessionState.Y == x`).
_COMPARISON_PATTERNS = [
    re.compile(r"==\s*SessionState\.\w+"),
    re.compile(r"!=\s*SessionState\.\w+"),
    re.compile(r"SessionState\.\w+\s*==(?!=)"),
    re.compile(r"SessionState\.\w+\s*!="),
    re.compile(r"\bin\s*[\(\[\{][^)\]\}]*SessionState\.\w+[^)\]\}]*[\)\]\}]"),
    # AC8: string-valued duplicates of the same comparisons — scoped specifically to
    # ``.state.value`` (not bare ``.value``, which also covers confirmed-unrelated
    # enums like ``ToolCall.status.value``/``ParsedMessage.type.value``).
    re.compile(r"\.state\.value\s*==(?!=)"),
    re.compile(r"\.state\.value\s*!="),
    re.compile(r"\.state\.value\s+in\s*[\(\[\{]"),
]

# file (relative to repo root) -> set of exact stripped line snippets permitted to
# contain a raw SessionState comparison. Each entry below is a legitimate
# single-state terminal/gating check confirmed during the issue #2184 investigation —
# not a hand-rolled stand-in for is_deliverable()/is_interruptible()/is_live()/
# is_not_terminated(). Review this content directly during code review, not just
# whether the test passes — a mis-transcribed snippet either false-fails immediately
# (cheap to catch) or silently exempts something it shouldn't.
ALLOWLIST: dict[str, set[str]] = {
    "backend/legion/comm_router.py": {
        # _send_system_error_comm: deliberately ACTIVE-only (not is_deliverable()) —
        # a PAUSED target just gets its error comm persisted for next start instead
        # of injected into its live stream, same conservative posture as "don't
        # trigger auto-start to avoid loops" a few lines above.
        "if target_minion and target_minion.state == SessionState.ACTIVE:",
    },
    # Issue #2189 (coordinator-split PR 10): comm_router.py's STARTING-only
    # readiness-wait guard moved here verbatim as part of delivery_gate.deliver()'s
    # wait_for_idle=False branch — still an auto-start guard (STARTING isn't
    # deliverable), not a deliverability check duplicate.
    "backend/delivery_gate.py": {
        "if target_minion.state == SessionState.STARTING:",
    },
    # Pinned exemption (see TestT2PredicateConsistency.
    # test_restart_session_deliberately_not_is_deliverable below): restarting a live
    # turn is deliberately ACTIVE-only, not is_deliverable().
    "backend/legion/mcp/legion_mcp_tools.py": {
        "if session_info.state != SessionState.ACTIVE:",
    },
    "backend/legion/scheduler_service.py": {
        # _fire_auto_resume_schedule: decide whether the #1019-pattern unstick
        # (recover_session()) is needed before the ACTIVE/auto-startable check below.
        "if session_info.state == SessionState.ERROR:",
        # _fire_auto_resume_schedule: skip starting if already ACTIVE.
        "if not (session_info.state == SessionState.ACTIVE or is_auto_startable(session_info.state)):",
        # _fire_ephemeral_schedule: same already-ACTIVE skip, different schedule kind.
        "if session_info.state != SessionState.ACTIVE:",
        # _ensure_session_active: early success/failure terminal checks, not a
        # deliverability predicate — this helper's whole job is deciding whether to
        # call start_session() at all.
        "if session_info.state == SessionState.ACTIVE:",
        "if info and info.state == SessionState.ACTIVE:",
        "if info and info.state == SessionState.ERROR:",
        # Orphan-recovery sweep: PAUSED (mid-permission-wait) gets resolved to ERROR
        # (issue #2165) — a single-state branch dispatch, not a predicate duplicate.
        "elif session_info.state == SessionState.PAUSED:",
    },
    "backend/permission_service.py": {
        # Pre-check before resolve_permission_closed(): only meaningful if currently
        # PAUSED — resolve_permission_closed() itself is the real transition gate.
        "if not session_info or session_info.state != SessionState.PAUSED:",
        # Post-check: resolve_permission_closed() returns True for its own no-op case
        # too, so re-confirm the real resulting state before logging a resume.
        "if post_info and post_info.state == SessionState.ACTIVE:",
    },
    "backend/routers/session_runtime.py": {
        # REST restart_session: same deliberate ACTIVE-only restart gate as
        # legion_mcp_tools.py's MCP tool and session_coordinator.restart_session()'s
        # own centralized guard — checked here too for a specific 409 response.
        "if state != SessionState.ACTIVE:",
    },
    "backend/session_coordinator.py": {
        # archive_and_clear_session: force a final TERMINATED transition only if not
        # already there — a terminal guard, not a predicate duplicate.
        "if session_info and session_info.state != SessionState.TERMINATED:",
        # interrupt_session: snapshot whether this was a PAUSED-permission-wait
        # interrupt specifically (for the scoped permission-deny below), independent
        # of the is_interruptible() gate on the very next line.
        "was_paused = session_info.state == SessionState.PAUSED",
        # set_permission_mode/set_model/add_directory live-update guards: only ACTIVE
        # sessions get a live SDK call; every other state persists to disk instead.
        "if session_info.state != SessionState.ACTIVE:",
    },
    # Issue #2189 (coordinator-split PR 1): restart_session()/reset_session() moved
    # to SessionLauncher verbatim — these two entries moved with them.
    "backend/session_launcher.py": {
        # restart_session(): the method's own centralized ACTIVE-only restart gate
        # (restarting a live turn is a different predicate than deliverability).
        "if not session_info or session_info.state != SessionState.ACTIVE:",
        # reset_session(): issue #1019 pattern — ERROR has no SDK to disconnect, so
        # recover_session() is called directly instead of going through the
        # disconnect-then-recover branch above.
        "if _pre_reset_info and _pre_reset_info.state == SessionState.ERROR:",
    },
    "backend/session_manager.py": {
        # _load_existing_sessions(): flags PAUSED/ERROR sessions for the crash-orphaned
        # tool-call repair scan (issue #2111/#2159) — orthogonal to the BOOT_NORMALIZED
        # state transition itself (handled separately via transition()).
        "if session_info.state == SessionState.PAUSED:",
        "if original_state == SessionState.ERROR:",
    },
    "backend/session_watchdog.py": {
        # Idle-alert eligibility: only ACTIVE sessions can be "idle" in the alerting
        # sense — PAUSED (mid-permission-wait) is deliberately exempt.
        "if session.state == SessionState.ACTIVE:",
    },
}


def _iter_backend_py_files():
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        rel_str = str(rel)
        if "/tests/" in rel_str or rel_str.endswith("session_lifecycle.py"):
            continue
        yield rel_str, path


def _comparison_lines(text: str) -> list[tuple[int, str]]:
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if any(p.search(stripped) for p in _COMPARISON_PATTERNS):
            hits.append((i, stripped))
    return hits


class TestAC4NoUnallowlistedHandRolledComparison:
    """Generic regression guard: any raw SessionState comparison anywhere under
    backend/ (outside session_lifecycle.py/tests/) must be in the explicit
    ALLOWLIST above, or it fails immediately. Catches both a newly reintroduced
    hand-rolled predicate duplicate AND a stale allowlist entry whose line no longer
    matches the real source (the mis-transcription risk the plan's Risks section
    calls out)."""

    def test_every_comparison_is_allowlisted(self):
        violations: list[str] = []
        for rel_str, path in _iter_backend_py_files():
            allowed = ALLOWLIST.get(rel_str, set())
            text = path.read_text()
            for lineno, snippet in _comparison_lines(text):
                if snippet not in allowed:
                    violations.append(f"{rel_str}:{lineno}: {snippet}")
        assert not violations, (
            "Unallowlisted raw SessionState comparison(s) found — use "
            "session_lifecycle.is_deliverable()/is_interruptible()/is_live()/"
            "is_not_terminated() instead, or add a justified ALLOWLIST entry:\n"
            + "\n".join(violations)
        )

    def test_allowlist_entries_still_match_real_source(self):
        """The inverse check: every ALLOWLIST entry must actually appear (and still
        be flagged as a comparison) in its file — an entry that no longer matches
        anything is a stale/mis-transcribed snippet silently exempting nothing,
        which hides whatever it was meant to cover."""
        stale: list[str] = []
        for rel_str, allowed in ALLOWLIST.items():
            path = REPO_ROOT / rel_str
            text = path.read_text()
            found_snippets = {snippet for _, snippet in _comparison_lines(text)}
            for snippet in allowed:
                if snippet not in found_snippets:
                    stale.append(f"{rel_str}: {snippet!r}")
        assert not stale, "Stale ALLOWLIST entries (no longer found in source):\n" + "\n".join(stale)


class TestT2PredicateConsistency:
    """Every caller's own is_deliverable/is_interruptible import agrees with the
    canonical predicate for every state — proving there's exactly one definition
    in use, not N independently-evolving copies that happen to agree today."""

    @pytest.mark.parametrize(
        "module_path,import_name",
        [
            ("backend.routers.sessions", "is_deliverable"),
            # Issue #2189 (coordinator-split PR 10): comm_router.py's is_deliverable
            # check moved into delivery_gate.py's wait_for_idle=False branch.
            ("backend.delivery_gate", "is_deliverable"),
            # legion_coordinator.resume_all() no longer scans is_deliverable sessions
            # project-wide (stage 6c) — it resumes exactly the persisted fleet
            # snapshot, so this predicate import moved out of that module entirely.
            ("backend.legion.scheduler_service", "is_deliverable"),
            ("backend.legion.scheduler_service", "is_auto_startable"),
            ("backend.session_coordinator", "is_interruptible"),
            # Issue #2184 (AC4): legion_coordinator's two migrated comparisons.
            ("backend.legion.legion_coordinator", "is_live"),
            ("backend.legion.legion_coordinator", "is_not_terminated"),
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

    def test_is_live_matches_active_or_starting(self):
        for state in SessionState:
            expected = state in (SessionState.ACTIVE, SessionState.STARTING)
            assert is_live(state) == expected

    def test_is_not_terminated_matches_not_terminated(self):
        for state in SessionState:
            expected = state is not SessionState.TERMINATED
            assert is_not_terminated(state) == expected

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
