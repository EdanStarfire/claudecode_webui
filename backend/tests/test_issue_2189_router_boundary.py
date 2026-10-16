"""Enforces the router/ApplicationService boundary (issue #2189, stage
router-boundary, AC4): every backend/routers/*.py module must reach
session/domain state through exactly one object — ApplicationService
(`webui.service`) — never directly through `webui.<attribute>`.

Mirrors src/tests/test_import_boundary.py's AST-walk-with-zero-violations
shape: this target state has no legitimate exceptions, unlike an allowlist-
style test.
"""

import ast
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTERS_ROOT = REPO_ROOT / "backend" / "routers"

# The 13-cluster inventory from the approved plan, minus `service` and
# `config_file` (both legitimate `webui.<attr>` accesses router code may keep).
FORBIDDEN_WEBUI_ATTRS = {
    "coordinator",
    "_run_git_command",
    "session_queues",
    "_broadcast_project_updated",
    "_broadcast_project_deleted",
    "_broadcast_queue_update",
    "_broadcast_state_change",
    "_broadcast_server_restarting",
    "permission_service",
    "provider_catalog_manager",
    "skill_manager",
    "litellm_proxy_manager",
    "_last_restart_time",
    "_create_message_callback",
    "_cleanup_pending_permissions_for_session",
    "ui_queue",
    "_queue_append_hook",
    "oauth_callback_path_conflicts_with_app_route",
    "sync_standalone_oauth_callback",
    "_sync_oauth_callback_for_config",
    "remove_standalone_oauth_callback",
    "_remove_oauth_callback_for_config",
}


def _find_forbidden_webui_attrs(root: Path) -> list[str]:
    """Statically scan every .py file under root for `webui.<forbidden-attr>`
    access — the universal `build_router(webui)` parameter name, confirmed
    uniform across all router modules."""
    violations = []
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "webui"
                and node.attr in FORBIDDEN_WEBUI_ATTRS
            ):
                violations.append(f"{path}:{node.lineno}: webui.{node.attr}")
    return violations


def test_routers_never_access_backendapp_directly():
    """Every backend/routers/*.py module must reach session/domain state only
    through webui.service (ApplicationService), never direct webui.<attr>
    access to BackendApp-owned coordinator/managers/callbacks."""
    violations = _find_forbidden_webui_attrs(ROUTERS_ROOT)
    assert not violations, (
        "backend/routers/*.py must reach state only through webui.service "
        "(ApplicationService), never directly via webui.<attr>. Violations found:\n"
        + "\n".join(violations)
    )


def test_scanner_detects_a_real_violation():
    """Regression test for the scanner itself: prove it actually flags a
    forbidden webui.<attr> access, and doesn't flag a legitimate one."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "bad_router.py").write_text(
            "def build_router(webui):\n"
            "    async def handler():\n"
            "        return await webui.coordinator.get_session_info('x')\n"
            "    return handler\n"
        )
        (tmp_path / "another_bad_router.py").write_text(
            "def build_router(webui):\n"
            "    async def handler():\n"
            "        webui._run_git_command(['git'], '.')\n"
            "    return handler\n"
        )
        (tmp_path / "good_router.py").write_text(
            "def build_router(webui):\n"
            "    async def handler():\n"
            "        return await webui.service.get_session_info('x')\n"
            "    return handler\n"
        )
        violations = _find_forbidden_webui_attrs(tmp_path)
        assert len(violations) == 2
        assert any("webui.coordinator" in v for v in violations)
        assert any("webui._run_git_command" in v for v in violations)
