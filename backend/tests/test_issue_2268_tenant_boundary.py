"""Tenant-boundary static scan (issue #2268 AC1, generalized by issue #2279 AC2).

TenantContext (backend/tenant_context.py) replaces process-global config/home/
data-dir access. This enforces the patterns that access used to take, across all
three tiers (backend/, src/, shared/):

1. A module-level constant computed via `Path.home()` at import time. Never
   allowlisted: nothing guarantees import order relative to TenantContext.
2. A bare/no-arg call to `config_manager.load_config()` (however aliased at the
   import site), which silently falls back to the global default config file.
3. A fixed `Path("data")` / `Path("./data/...")` default, or a `"data/..."` string
   parameter default (e.g. `x or Path("data")`).
4. A function-level `Path.home()` call, or `Path("~/...").expanduser()`.

Patterns 2-4 are permitted only via a reviewed FIXED_PATH_ALLOWLIST entry with a
non-empty reason; stale entries fail. Bare `"data"` string literals elsewhere
(dict keys etc.) are deliberately not matched.
"""

from __future__ import annotations

import ast
import re
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_TIERS = ("backend", "src", "shared")

# Exempt entirely: tenant_context.py (the one legitimate home/data-dir default
# definition) and backend/main.py (bootstrap constructing the one TenantContext per
# process). The root main.py is outside the scanned tiers.
EXCLUDED_RELPATHS = {"backend/tenant_context.py", "backend/main.py"}

_FIXED_DATA_RE = re.compile(r"^(\./)?data(/|$)")

# (tier-prefixed relpath, enclosing qualname, kind) -> reviewed reason.
FIXED_PATH_ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("backend/web_server.py", "BackendApp.__init__", "fixed-data-path"): (
        "embedding/test convenience; production passes TenantContext from backend/main.py"
    ),
    ("backend/session_coordinator.py", "SessionCoordinator.__init__", "fixed-data-path"): (
        "embedding/test convenience; production passes TenantContext from backend/main.py"
    ),
    ("backend/config_manager.py", "default_config_dir", "function-home"): (
        "single definition of the default config location; TenantContext/main.py resolve it once"
    ),
    ("src/frontend_config.py", "default_config_file", "function-home"): (
        "Frontend counterpart of config_manager.default_config_file; resolved once per process"
    ),
}


def _is_path_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Path"
    )


class _TenantBoundaryVisitor(ast.NodeVisitor):
    """Single-pass visitor collecting `(key, message)` violations."""

    def __init__(self, relpath: str, load_config_names: set[str]):
        self.relpath = relpath
        self.load_config_names = load_config_names
        self.violations: list[tuple[tuple[str, str, str], str]] = []
        self._scope: list[str] = []
        self._func_depth = 0

    def _qualname(self) -> str:
        return ".".join(self._scope) if self._scope else "<module>"

    def _add(self, kind: str, node: ast.AST, message: str) -> None:
        self.violations.append(
            ((self.relpath, self._qualname(), kind), f"{self.relpath}:{node.lineno}: {message}")
        )

    def visit_ClassDef(self, node):
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _visit_function(self, node):
        # Defaults are evaluated in the enclosing scope; check them before descending.
        args = node.args
        for default in [*args.defaults, *(d for d in args.kw_defaults if d is not None)]:
            if (
                isinstance(default, ast.Constant)
                and isinstance(default.value, str)
                and _FIXED_DATA_RE.match(default.value)
            ):
                self._scope.append(node.name)
                self._add(
                    "fixed-data-path", default, f"fixed data path default {default.value!r}"
                )
                self._scope.pop()
        self._scope.append(node.name)
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1
        self._scope.pop()

    def visit_FunctionDef(self, node):
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node)

    def visit_Call(self, node):
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "home"
            and isinstance(func.value, ast.Name)
            and func.value.id == "Path"
        ):
            if self._func_depth == 0:
                self._add(
                    "module-home",
                    node,
                    "module-level Path.home() call "
                    "(must be a function/property, not evaluated at import time)",
                )
            else:
                self._add(
                    "function-home",
                    node,
                    "function-level Path.home() (thread tenant_context.home_dir instead)",
                )

        if _is_path_call(node) and node.args:
            arg = node.args[0]
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and _FIXED_DATA_RE.match(arg.value)
            ):
                self._add(
                    "fixed-data-path",
                    node,
                    f"fixed Path({arg.value!r}) (thread tenant_context.data_dir instead)",
                )

        if (
            isinstance(func, ast.Attribute)
            and func.attr == "expanduser"
            and _is_path_call(func.value)
            and func.value.args
            and isinstance(func.value.args[0], ast.Constant)
            and isinstance(func.value.args[0].value, str)
            and func.value.args[0].value.startswith("~")
        ):
            self._add(
                "function-expanduser",
                node,
                f"hidden ~ default Path({func.value.args[0].value!r}).expanduser()",
            )

        if (
            isinstance(func, ast.Name)
            and func.id in self.load_config_names
            and not node.args
            and not node.keywords
        ):
            self._add(
                "bare-load-config",
                node,
                f"bare {func.id}() call (use tenant_context.load_config() or pass config_file)",
            )

        self.generic_visit(node)


def _local_load_config_names(tree: ast.Module) -> set[str]:
    """Every local name `config_manager.load_config` is bound to in this module,
    however aliased at the import site (e.g. `import load_config as _load_cfg`)."""
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.endswith("config_manager")
        ):
            for alias in node.names:
                if alias.name == "load_config":
                    names.add(alias.asname or alias.name)
    return names


def _scan(root: Path, prefix: str) -> list[tuple[tuple[str, str, str], str]]:
    """Return every `(key, message)` violation under `root`, keys prefixed with `prefix`."""
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if "tests" in rel.parts:
            continue
        relpath = f"{prefix}/{rel.as_posix()}"
        if relpath in EXCLUDED_RELPATHS:
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        visitor = _TenantBoundaryVisitor(relpath, _local_load_config_names(tree))
        visitor.visit(tree)
        found.extend(visitor.violations)
    return found


def _unallowed(
    found: list[tuple[tuple[str, str, str], str]],
    allowlist: dict[tuple[str, str, str], str],
) -> list[str]:
    # module-home is never allowlistable.
    return [
        msg for key, msg in found if key[2] == "module-home" or key not in allowlist
    ]


def _scan_repo() -> list[tuple[tuple[str, str, str], str]]:
    found = []
    for tier in SCAN_TIERS:
        found.extend(_scan(REPO_ROOT / tier, tier))
    return found


def test_no_tenant_boundary_violations_outside_allowlist():
    """No hidden process-global (module-level Path.home(), bare load_config(), fixed
    data/home defaults) anywhere in backend/, src/, shared/ outside the allowlist."""
    violations = _unallowed(_scan_repo(), FIXED_PATH_ALLOWLIST)
    assert not violations, (
        "Found tenant-boundary violations (issue #2268/#2279). Thread TenantContext "
        "through instead, or add a reviewed FIXED_PATH_ALLOWLIST entry with a reason:\n"
        + "\n".join(violations)
    )


def test_allowlist_reasons_are_non_empty():
    assert all(isinstance(r, str) and r.strip() for r in FIXED_PATH_ALLOWLIST.values())


def test_allowlist_has_no_stale_entries():
    live = {key for key, _ in _scan_repo()}
    stale = sorted(set(FIXED_PATH_ALLOWLIST) - live)
    assert not stale, f"Stale FIXED_PATH_ALLOWLIST entries (no matching violation): {stale}"


def _write(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / name).write_text(body)


def test_scanner_detects_violations_in_any_tier():
    """Self-test: each pattern is flagged under a src/-like tree; correct usage is not."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        _write(t, "bad_module_constant.py", "from pathlib import Path\nC = Path.home() / 'x'\n")
        _write(
            t,
            "bad_bare_call.py",
            "from backend.config_manager import load_config as _l\ndef f():\n    return _l()\n",
        )
        _write(
            t,
            "bad_data_default.py",
            "from pathlib import Path\n"
            "class M:\n    def __init__(self, data_dir=None):\n"
            "        self.d = data_dir or Path('data')\n",
        )
        _write(t, "bad_data_param.py", "def f(log_dir: str = 'data/logs'):\n    return log_dir\n")
        _write(
            t,
            "bad_func_home.py",
            "from pathlib import Path\ndef f(h=None):\n    return h or Path.home()\n",
        )
        _write(
            t,
            "bad_expanduser.py",
            "from pathlib import Path\ndef f():\n    return Path('~/x').expanduser()\n",
        )
        _write(
            t,
            "good_module.py",
            "from pathlib import Path\n"
            "from backend.config_manager import load_config\n"
            "def g(tenant_context, p):\n"
            "    d = {'data': 1}\n"
            "    q = Path(p).expanduser()\n"
            "    return load_config(tenant_context.config_file), d, q\n",
        )
        found = _scan(t, "src")
        kinds = {(k[0], k[2]) for k, _ in found}
        assert kinds == {
            ("src/bad_module_constant.py", "module-home"),
            ("src/bad_bare_call.py", "bare-load-config"),
            ("src/bad_data_default.py", "fixed-data-path"),
            ("src/bad_data_param.py", "fixed-data-path"),
            ("src/bad_func_home.py", "function-home"),
            ("src/bad_expanduser.py", "function-expanduser"),
        }
        assert len(_unallowed(found, {})) == 6


def test_allowlisted_entry_is_clean_but_module_home_never_is():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        _write(t, "ok.py", "from pathlib import Path\ndef f():\n    return Path.home()\n")
        _write(t, "mod.py", "from pathlib import Path\nC = Path.home()\n")
        found = _scan(t, "src")
        allow = {
            ("src/ok.py", "f", "function-home"): "reviewed",
            ("src/mod.py", "<module>", "module-home"): "tries to allow",
        }
        remaining = _unallowed(found, allow)
        assert len(remaining) == 1 and "mod.py" in remaining[0]


@pytest.mark.parametrize("reason", ["", "   "])
def test_empty_reason_is_rejected(reason):
    with pytest.raises(AssertionError):
        assert all(isinstance(r, str) and r.strip() for r in {("a", "b", "c"): reason}.values())
