"""AC1 (issue #2268, epic #1990 stage 8): a static boundary test mirroring
test_import_boundary.py's zero-violation AST-walk style.

TenantContext (backend/tenant_context.py) replaces process-global config/home/
data-dir access. This enforces the two patterns that access used to take:

1. A module-level constant computed via `Path.home()` at import time — can't be
   threaded through TenantContext at all, since nothing guarantees import order
   relative to TenantContext construction.
2. A bare/no-arg call to `config_manager.load_config()` (however aliased at the
   import site) — silently falls back to the global default config file instead
   of this process's own TenantContext.config_file.

Like test_import_boundary.py, this only catches *future* regressions outside a
small, reviewed allowlist — it does not retroactively prove every site this
stage's own migration touched was covered (see the approved plan's Risks
section for that inventory).
"""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"

# Files exempt from both checks entirely:
# - tenant_context.py itself: the one legitimate `Path.home()` default (mirrors
#   the data_dir/config_file override pattern) and the one legitimate
#   `load_config()`-wrapping method live here, by design.
# - main.py: the bootstrap that constructs the one TenantContext per process,
#   before anything else exists to thread one through.
EXCLUDED_FILENAMES = {"tenant_context.py", "main.py"}

# (relative-path-from-backend-root, enclosing-function-qualname) pairs allowed to
# keep a bare load_config()-family call with no explicit config_file. Reviewed
# exceptions only — ClaudeSDK is a leaf SDK wrapper constructed per-session via a
# factory, with no coordinator/TenantContext back-reference; threading one through
# would mean a disproportionate constructor change to a class untouched by every
# other part of this stage, for a class with exactly one Backend-process-wide
# config anyway (today's "one backend : one user" model — see the approved plan's
# TenantContext-granularity decision).
BARE_LOAD_CONFIG_ALLOWLIST = {
    ("claude_sdk.py", "_get_sdk_options"),
    ("claude_sdk.py", "_resolve_env_vars"),
}


class _TenantBoundaryVisitor(ast.NodeVisitor):
    """Single-pass visitor collecting both violation kinds.

    Tracks function-nesting depth (for the module-level Path.home() check) and
    the current enclosing function's qualified name (for the bare-load_config
    allowlist), rather than two separate re-walks of the same tree.
    """

    def __init__(self, relpath: str, load_config_names: set[str]):
        self.relpath = relpath
        self.load_config_names = load_config_names
        self.violations: list[str] = []
        self._func_depth = 0
        self._func_stack: list[str] = []

    def _enclosing_qualname(self) -> str:
        return self._func_stack[-1] if self._func_stack else "<module>"

    def _visit_function(self, node):
        self._func_depth += 1
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()
        self._func_depth -= 1

    def visit_FunctionDef(self, node):
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node)

    def visit_Call(self, node):
        # Path.home() calls made directly at module level (never inside any
        # function/method) — these are the import-time-evaluated constants.
        if (
            self._func_depth == 0
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "home"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "Path"
        ):
            self.violations.append(
                f"{self.relpath}:{node.lineno}: module-level Path.home() call "
                "(must be a function/property, not evaluated at import time)"
            )

        # Bare/no-arg load_config()-family calls, anywhere.
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in self.load_config_names
            and not node.args
            and not node.keywords
        ):
            key = (self.relpath, self._enclosing_qualname())
            if key not in BARE_LOAD_CONFIG_ALLOWLIST:
                self.violations.append(
                    f"{self.relpath}:{node.lineno}: bare {node.func.id}() call in "
                    f"{self._enclosing_qualname()} (use tenant_context.load_config() "
                    "instead, or add a reviewed allowlist entry)"
                )

        self.generic_visit(node)


def _local_load_config_names(tree: ast.Module) -> set[str]:
    """Every local name `config_manager.load_config` is bound to in this module,
    however aliased at the import site (e.g. `import load_config as _load_cfg`)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("config_manager"):
            for alias in node.names:
                if alias.name == "load_config":
                    names.add(alias.asname or alias.name)
    return names


def _find_violations(root: Path) -> list[str]:
    violations = []
    for path in root.rglob("*.py"):
        if path.name in EXCLUDED_FILENAMES:
            continue
        if "tests" in path.relative_to(root).parts:
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        relpath = str(path.relative_to(root))
        visitor = _TenantBoundaryVisitor(relpath, _local_load_config_names(tree))
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


def test_backend_never_evaluates_path_home_or_bare_load_config_outside_allowlist():
    """TenantContext.data_dir/config_file/home_dir must be the one path to this
    process's tenant identity — no new module-level Path.home() constant or bare
    load_config() call anywhere in backend/ outside the reviewed allowlist."""
    violations = _find_violations(BACKEND_ROOT)
    assert not violations, (
        "Found tenant-boundary violations (issue #2268 AC1). Thread TenantContext "
        "through instead, or add a reviewed allowlist entry with justification:\n"
        + "\n".join(violations)
    )


def test_scanner_detects_a_real_violation():
    """Regression test for the scanner itself: prove it actually flags both
    violation kinds, and leaves a correctly-used one alone."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "bad_module_constant.py").write_text(
            "from pathlib import Path\nCONFIG_DIR = Path.home() / 'x'\n"
        )
        (tmp_path / "bad_bare_call.py").write_text(
            "from backend.config_manager import load_config as _load_cfg\n"
            "def f():\n"
            "    return _load_cfg()\n"
        )
        (tmp_path / "good_module.py").write_text(
            "from pathlib import Path\n"
            "from backend.config_manager import load_config\n"
            "def f(home_dir):\n"
            "    return home_dir or Path.home()\n"
            "def g(tenant_context):\n"
            "    return load_config(tenant_context.config_file)\n"
        )
        violations = _find_violations(tmp_path)
        assert len(violations) == 2
