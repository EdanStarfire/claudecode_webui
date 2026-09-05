"""Enforces the Frontend API / Backend import boundary (issue #498).

src/ (Frontend API) must be structurally incapable of importing backend/
(the session-execution tier) — proven by this test, not by code-review
vigilance. See plan-498-final.md, "Import-boundary enforcement".
"""

import ast
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"


def _find_backend_imports(root: Path) -> list[str]:
    """Statically scan every .py file under root for `import backend`/`from backend...`."""
    violations = []
    for path in root.rglob("*.py"):
        if path.name == "test_import_boundary.py":
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "backend" or alias.name.startswith("backend."):
                        violations.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module == "backend" or node.module.startswith("backend.")):
                    violations.append(f"{path}: from {node.module} import ...")
    return violations


def test_src_never_imports_backend():
    """The Frontend API (src/) must never import from backend/ (session-execution tier)."""
    violations = _find_backend_imports(SRC_ROOT)
    assert not violations, (
        "src/ (Frontend API) must never import from backend/. Violations found:\n"
        + "\n".join(violations)
    )


def test_scanner_detects_a_real_violation():
    """Regression test for the scanner itself: prove it actually flags forbidden imports."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "bad_import.py").write_text("import backend\n")
        (tmp_path / "bad_import_from.py").write_text(
            "from backend.session_coordinator import SessionCoordinator\n"
        )
        (tmp_path / "good_module.py").write_text(
            "import os\nfrom shared.event_queue import EventQueue\n"
        )
        violations = _find_backend_imports(tmp_path)
        assert len(violations) == 2
