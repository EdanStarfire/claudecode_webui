"""AC6 (issue #2189, epic #1990 stage 7 coordinator-split, PR 11 FINAL): late
imports (imports inside a function/method body, not at module top-level) across
session_coordinator.py and the 7 modules this stage extracted from it are either
eliminated or explicitly justified in a comment.

Census during this PR's AC6 investigation found 43 late imports across these 8
files, of which only 2 carried an explicit justification comment. Direct
empirical testing (hoisting each one to module scope and attempting a fresh
`import backend.X`, validated first against the two known-true circular cases)
showed 41 of the 43 were NOT actually required to avoid a circular import —
they were either pure habit (stdlib `uuid`/`shutil`, a redundant `json as _json`
alias) or, in a few cases, deferred for an entirely different reason: tests
patching the *source* module's attribute directly (e.g.
``backend.docker_utils.resolve_docker_cli_path``), which only takes effect if
the importing module looks the name up fresh on every call rather than binding
it once at module-import time; or an `except ImportError` guard tolerating an
older claude_agent_sdk lacking a newer helper. All 41 confirmed-safe late
imports were moved to module scope; only the small number with a real,
confirmed reason remain, each with an explicit comment explaining why
(matching the wording convention already used before this PR at
session_launcher.py:388/message_pipeline.py:209).

This test is modeled on test_issue_2189_coordinator_responsibility.py's
allowlist-with-per-entry-ceiling AST pattern: one ceiling entry per scanned
file, counting only late imports WITHOUT a nearby justification comment (so a
future genuinely-justified addition doesn't immediately fail CI, but an
unexplained one — the "pure habit" kind this PR just eliminated — does).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"

# The coordinator plus the 7 modules extracted from it over this stage's 11 PRs.
SCANNED_FILES = [
    "backend/session_coordinator.py",
    "backend/session_launcher.py",
    "backend/message_pipeline.py",
    "backend/tool_call_tracker.py",
    "backend/archive_coordinator.py",
    "backend/session_resources.py",
    "backend/session_query.py",
    "backend/delivery_gate.py",
]

# file (relative to repo root) -> ceiling on UNJUSTIFIED late imports (those
# with no nearby comment explaining the deferral). Pinned to the CURRENT count
# (0 everywhere, confirmed by this PR's own cleanup) — do NOT raise these to
# accommodate a new late import; either justify it with a comment (preferred,
# and it won't count against this ceiling) or eliminate it.
UNJUSTIFIED_CEILING: dict[str, int] = {
    "backend/session_coordinator.py": 0,
    "backend/session_launcher.py": 0,
    "backend/message_pipeline.py": 0,
    "backend/tool_call_tracker.py": 0,
    "backend/archive_coordinator.py": 0,
    "backend/session_resources.py": 0,
    "backend/session_query.py": 0,
    "backend/delivery_gate.py": 0,
}

# Block-opener lines that may sit between a justification comment and the
# import it explains (e.g. a `try:` wrapping an `except ImportError` guard)
# without breaking the "nearby comment" link.
_SKIPPABLE_SUFFIX = ":"
_MAX_LOOKBACK = 10


def _has_function_ancestor(node: ast.AST, ancestors: list[ast.AST]) -> bool:
    return any(isinstance(a, ast.FunctionDef | ast.AsyncFunctionDef) for a in ancestors)


def _late_imports(tree: ast.Module) -> list[ast.stmt]:
    """Every Import/ImportFrom node with at least one function/method ancestor
    (i.e. inside a function body, not at module top-level). Module-level
    `if TYPE_CHECKING:` imports have no such ancestor and are correctly
    excluded — they carry no runtime deferral concern."""
    found: list[ast.stmt] = []

    def walk(node: ast.AST, ancestors: list[ast.AST]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import | ast.ImportFrom) and _has_function_ancestor(
                child, ancestors
            ):
                found.append(child)
            walk(child, ancestors + [child])

    walk(tree, [])
    return found


def _is_justified(lines: list[str], import_lineno: int) -> bool:
    """Walk upward from the line before the import, skipping blank lines and
    block-opener lines (`try:`, `if ...:`, etc. — the kind of line that can sit
    between a comment and the import it justifies, e.g. a `try:`/`except
    ImportError` guard). Stop at the first real content line: justified if
    it's a comment, not justified if it's anything else."""
    idx = import_lineno - 2  # 0-indexed line before the import
    steps = 0
    while idx >= 0 and steps < _MAX_LOOKBACK:
        stripped = lines[idx].strip()
        if not stripped:
            idx -= 1
            steps += 1
            continue
        if stripped.startswith("#"):
            return True
        if stripped.endswith(_SKIPPABLE_SUFFIX):
            idx -= 1
            steps += 1
            continue
        return False
    return False


def _unjustified_late_imports(rel_path: str) -> list[str]:
    path = REPO_ROOT / rel_path
    source = path.read_text()
    lines = source.splitlines()
    tree = ast.parse(source)
    unjustified = []
    for node in _late_imports(tree):
        if not _is_justified(lines, node.lineno):
            snippet = lines[node.lineno - 1].strip()
            unjustified.append(f"{rel_path}:{node.lineno}: {snippet}")
    return unjustified


class TestAC6LateImportsJustified:
    """Every late import in the coordinator-split file set is either gone or
    explicitly explained — anything else must be a reviewed ceiling bump
    (which should not happen; see UNJUSTIFIED_CEILING's docstring)."""

    def test_unjustified_late_imports_within_ceiling(self):
        violations: list[str] = []
        for rel_path in SCANNED_FILES:
            unjustified = _unjustified_late_imports(rel_path)
            ceiling = UNJUSTIFIED_CEILING[rel_path]
            if len(unjustified) > ceiling:
                violations.extend(unjustified)
        assert not violations, (
            "Unjustified late import(s) found (no nearby comment explaining the "
            "deferral) — either eliminate (move to module scope) or add a "
            "justification comment:\n" + "\n".join(violations)
        )

    def test_scanned_files_still_exist(self):
        """Inverse-style check: every file this test scans must still exist —
        catches a stale entry if one of the 7 extracted modules is ever
        renamed/removed without updating this test."""
        missing = [f for f in SCANNED_FILES if not (REPO_ROOT / f).is_file()]
        assert not missing, f"Scanned file(s) no longer exist: {missing}"
