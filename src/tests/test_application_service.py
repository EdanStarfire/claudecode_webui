"""Unit tests for ApplicationService.

Tests run without an HTTP server — service methods are called directly
with a mock coordinator.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application_service import ApplicationService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_coordinator():
    coordinator = MagicMock()
    coordinator.project_manager = MagicMock()
    coordinator.session_manager = MagicMock()
    coordinator.mcp_config_manager = MagicMock()
    coordinator.oauth_manager = MagicMock()
    coordinator.template_manager = MagicMock()

    coordinator.project_manager.create_project = AsyncMock()
    coordinator.project_manager.list_projects = AsyncMock()
    coordinator.project_manager.get_project = AsyncMock()
    coordinator.project_manager.update_project = AsyncMock()
    coordinator.project_manager.delete_project = AsyncMock()
    coordinator.project_manager.toggle_expansion = AsyncMock()
    coordinator.project_manager.reorder_projects = AsyncMock()
    coordinator.project_manager.reorder_project_sessions = AsyncMock()

    coordinator.session_manager.get_session_info = AsyncMock()
    coordinator.session_manager.get_sessions_by_ids = AsyncMock()
    coordinator.session_manager.update_session = AsyncMock()
    coordinator.session_manager.reorder_sessions = AsyncMock()

    coordinator.mcp_config_manager.list_configs = AsyncMock()
    coordinator.mcp_config_manager.create_config = AsyncMock()
    coordinator.mcp_config_manager.get_config = AsyncMock()
    coordinator.mcp_config_manager.update_config = AsyncMock()
    coordinator.mcp_config_manager.delete_config = AsyncMock()

    coordinator.oauth_manager.complete_flow = AsyncMock()
    coordinator.oauth_manager.start_flow = AsyncMock()
    coordinator.oauth_manager.disconnect = AsyncMock()
    coordinator.oauth_manager.get_stored_token = AsyncMock()
    coordinator.oauth_manager.get_token_store = MagicMock()

    coordinator.template_manager.list_templates = AsyncMock()
    coordinator.template_manager.get_template = AsyncMock()
    coordinator.template_manager.create_template = AsyncMock()
    coordinator.template_manager.update_template = AsyncMock()
    coordinator.template_manager.delete_template = AsyncMock()
    coordinator.template_manager.import_template = AsyncMock()

    coordinator.start_session = AsyncMock()
    coordinator.terminate_session = AsyncMock()
    coordinator.restart_session = AsyncMock()
    coordinator.reset_session = AsyncMock()
    coordinator.disconnect_sdk = AsyncMock()
    coordinator.clear_message_callbacks = MagicMock()
    coordinator.find_project_for_session = AsyncMock()
    coordinator.delete_session = AsyncMock()
    coordinator.set_permission_mode = AsyncMock()
    return coordinator


@pytest.fixture
def service(mock_coordinator):
    return ApplicationService(mock_coordinator)


def _make_project(project_id="p1", session_ids=None):
    p = MagicMock()
    p.project_id = project_id
    p.session_ids = session_ids or []
    p.is_expanded = True
    p.to_dict.return_value = {"project_id": project_id, "session_ids": session_ids or [], "is_expanded": True}
    return p


def _make_session(session_id="s1", name="test", state="active", working_directory="/tmp"):
    s = MagicMock()
    s.session_id = session_id
    s.name = name
    s.state = state
    s.working_directory = working_directory
    s.to_dict.return_value = {"session_id": session_id, "name": name, "state": state}
    return s


def _make_config(config_id="c1", name="myserver"):
    c = MagicMock()
    c.id = config_id
    c.name = name
    c.to_dict.return_value = {"id": config_id, "name": name}
    return c


def _make_template(template_id="t1", name="mytemplate"):
    t = MagicMock()
    t.template_id = template_id
    t.name = name
    t.to_dict.return_value = {"template_id": template_id, "name": name}
    return t


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_project_calls_project_manager(service, mock_coordinator):
    project = _make_project()
    mock_coordinator.project_manager.create_project.return_value = project

    result = await service.create_project(name="test", working_directory="/tmp", max_concurrent_minions=3)

    mock_coordinator.project_manager.create_project.assert_called_once_with(
        name="test", working_directory="/tmp", max_concurrent_minions=3
    )
    assert result == project.to_dict()


@pytest.mark.asyncio
async def test_list_projects_returns_dicts(service, mock_coordinator):
    p1, p2 = _make_project("p1"), _make_project("p2")
    mock_coordinator.project_manager.list_projects.return_value = [p1, p2]

    result = await service.list_projects()

    assert result["total"] == 2
    assert result["projects"][0] == p1.to_dict()
    assert result["projects"][1] == p2.to_dict()


@pytest.mark.asyncio
async def test_get_project_returns_none_when_not_found(service, mock_coordinator):
    mock_coordinator.project_manager.get_project.return_value = None

    result = await service.get_project("nonexistent")

    assert result is None


@pytest.mark.asyncio
async def test_get_project_includes_sessions(service, mock_coordinator):
    project = _make_project("p1", session_ids=["s1", "s2"])
    mock_coordinator.project_manager.get_project.return_value = project
    s1, s2 = _make_session("s1"), _make_session("s2")
    mock_coordinator.session_manager.get_sessions_by_ids.return_value = [s1, s2]

    result = await service.get_project("p1")

    mock_coordinator.session_manager.get_sessions_by_ids.assert_called_once_with(["s1", "s2"])
    assert "sessions" in result
    assert len(result["sessions"]) == 2


@pytest.mark.asyncio
async def test_update_project_returns_none_when_not_found(service, mock_coordinator):
    mock_coordinator.project_manager.get_project.return_value = None

    result = await service.update_project("nonexistent", name="new")

    assert result is None
    mock_coordinator.project_manager.update_project.assert_not_called()


@pytest.mark.asyncio
async def test_delete_project_returns_false_when_not_found(service, mock_coordinator):
    mock_coordinator.project_manager.get_project.return_value = None

    result = await service.delete_project("nonexistent")

    assert result["success"] is False
    mock_coordinator.project_manager.delete_project.assert_not_called()


@pytest.mark.asyncio
async def test_toggle_project_expansion_calls_manager(service, mock_coordinator):
    project = _make_project()
    mock_coordinator.project_manager.toggle_expansion.return_value = True
    mock_coordinator.project_manager.get_project.return_value = project

    result = await service.toggle_project_expansion("p1")

    mock_coordinator.project_manager.toggle_expansion.assert_called_once_with("p1")
    assert result == project.to_dict()


@pytest.mark.asyncio
async def test_reorder_project_sessions_calls_reorder_sessions(service, mock_coordinator):
    project = _make_project()
    mock_coordinator.project_manager.reorder_project_sessions.return_value = True
    mock_coordinator.project_manager.get_project.return_value = project

    result = await service.reorder_project_sessions("p1", ["s2", "s1"])

    mock_coordinator.session_manager.reorder_sessions.assert_called_once_with(["s2", "s1"])
    assert result == project.to_dict()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_session_clears_callbacks_first(service, mock_coordinator):
    mock_coordinator.start_session.return_value = True
    callback = MagicMock()

    await service.start_session("s1", callback)

    mock_coordinator.clear_message_callbacks.assert_called_once_with("s1")
    mock_coordinator.start_session.assert_called_once_with("s1", permission_callback=callback)


@pytest.mark.asyncio
async def test_restart_session_clears_callbacks_first(service, mock_coordinator):
    mock_coordinator.restart_session.return_value = True
    callback = MagicMock()

    await service.restart_session("s1", callback)

    mock_coordinator.clear_message_callbacks.assert_called_once_with("s1")
    mock_coordinator.restart_session.assert_called_once_with("s1", permission_callback=callback)


@pytest.mark.asyncio
async def test_disconnect_session_calls_disconnect_sdk(service, mock_coordinator):
    mock_coordinator.disconnect_sdk.return_value = True

    result = await service.disconnect_session("s1")

    mock_coordinator.disconnect_sdk.assert_called_once_with("s1")
    assert result == {"success": True}


@pytest.mark.asyncio
async def test_delete_session_returns_project_deleted_true_when_project_gone(service, mock_coordinator):
    project = _make_project("p1")
    mock_coordinator.find_project_for_session.return_value = project
    mock_coordinator.delete_session.return_value = {"success": True, "deleted_session_ids": ["s1"]}
    mock_coordinator.project_manager.get_project.return_value = None  # project was deleted

    result = await service.delete_session("s1")

    assert result["project_id"] == "p1"
    assert result["project_deleted"] is True
    assert result["updated_project"] is None


@pytest.mark.asyncio
async def test_delete_session_returns_project_deleted_false_when_project_remains(service, mock_coordinator):
    project = _make_project("p1")
    updated = _make_project("p1")
    mock_coordinator.find_project_for_session.return_value = project
    mock_coordinator.delete_session.return_value = {"success": True, "deleted_session_ids": ["s1"]}
    mock_coordinator.project_manager.get_project.return_value = updated

    result = await service.delete_session("s1")

    assert result["project_deleted"] is False
    assert result["updated_project"] == updated.to_dict()


@pytest.mark.asyncio
async def test_delete_session_no_project_field_when_session_has_no_project(service, mock_coordinator):
    mock_coordinator.find_project_for_session.return_value = None
    mock_coordinator.delete_session.return_value = {"success": True, "deleted_session_ids": ["s1"]}

    result = await service.delete_session("s1")

    assert "project_id" not in result
    assert "project_deleted" not in result


@pytest.mark.asyncio
async def test_update_session_delegates_to_session_manager(service, mock_coordinator):
    mock_coordinator.session_manager.update_session.return_value = True

    result = await service.update_session("s1", name="new name", model="sonnet")

    mock_coordinator.session_manager.update_session.assert_called_once_with(
        "s1",
        template_manager=mock_coordinator.template_manager,
        name="new name",
        model="sonnet",
    )
    assert result is True


@pytest.mark.asyncio
async def test_terminate_session_returns_false_when_not_found(service, mock_coordinator):
    mock_coordinator.session_manager.get_session_info.return_value = None

    result = await service.terminate_session("nonexistent")

    assert result is False
    mock_coordinator.terminate_session.assert_not_called()


@pytest.mark.asyncio
async def test_set_permission_mode_returns_false_when_session_not_found(service, mock_coordinator):
    mock_coordinator.session_manager.get_session_info.return_value = None

    result = await service.set_session_permission_mode("nonexistent", "default")

    assert result is False
    mock_coordinator.set_permission_mode.assert_not_called()


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_mcp_configs_returns_dicts(service, mock_coordinator):
    c1, c2 = _make_config("c1"), _make_config("c2")
    mock_coordinator.mcp_config_manager.list_configs.return_value = [c1, c2]

    result = await service.list_mcp_configs()

    assert result["total"] == 2
    assert result["configs"][0] == c1.to_dict()


@pytest.mark.asyncio
async def test_create_mcp_config_returns_dict(service, mock_coordinator):
    config = _make_config()
    mock_coordinator.mcp_config_manager.create_config.return_value = config

    result = await service.create_mcp_config(name="srv", server_type="stdio")

    assert result == config.to_dict()


@pytest.mark.asyncio
async def test_get_mcp_config_returns_none_when_not_found(service, mock_coordinator):
    mock_coordinator.mcp_config_manager.get_config.return_value = None

    result = await service.get_mcp_config("nonexistent")

    assert result is None


@pytest.mark.asyncio
async def test_import_mcp_configs_dry_run_returns_preview(service, mock_coordinator):
    """Verifies list_configs is used (not configs.values()) for import."""
    mock_coordinator.mcp_config_manager.list_configs.return_value = []

    result = await service.import_mcp_configs(
        servers={"myserver": {"type": "stdio", "command": "npx"}},
        dry_run=True,
    )

    mock_coordinator.mcp_config_manager.list_configs.assert_called_once()
    assert result["dry_run"] is True
    assert result["summary"]["create"] == 1
    assert len(result["imported"]) == 0  # dry_run → nothing imported


@pytest.mark.asyncio
async def test_import_mcp_configs_executes_when_not_dry_run(service, mock_coordinator):
    mock_coordinator.mcp_config_manager.list_configs.return_value = []
    created_config = _make_config("new1", "myserver")
    mock_coordinator.mcp_config_manager.create_config.return_value = created_config

    result = await service.import_mcp_configs(
        servers={"myserver": {"type": "stdio", "command": "npx"}},
        dry_run=False,
    )

    mock_coordinator.mcp_config_manager.create_config.assert_called_once()
    assert len(result["imported"]) == 1


@pytest.mark.asyncio
async def test_delete_mcp_config_returns_bool(service, mock_coordinator):
    mock_coordinator.mcp_config_manager.delete_config.return_value = True

    result = await service.delete_mcp_config("c1")

    assert result is True


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_complete_flow_returns_server_id(service, mock_coordinator):
    mock_coordinator.oauth_manager.complete_flow.return_value = "server-123"

    result = await service.oauth_complete_flow("state-abc", "code-xyz")

    assert result == "server-123"
    mock_coordinator.oauth_manager.complete_flow.assert_called_once_with("state-abc", "code-xyz")


@pytest.mark.asyncio
async def test_oauth_initiate_flow_returns_none_when_config_not_found(service, mock_coordinator):
    mock_coordinator.mcp_config_manager.get_config.return_value = None

    result = await service.oauth_initiate_flow("nonexistent", "http://srv", "http://cb", "App")

    assert result is None
    mock_coordinator.oauth_manager.start_flow.assert_not_called()


@pytest.mark.asyncio
async def test_oauth_disconnect_returns_false_when_config_not_found(service, mock_coordinator):
    mock_coordinator.mcp_config_manager.get_config.return_value = None

    result = await service.oauth_disconnect("nonexistent")

    assert result is False
    mock_coordinator.oauth_manager.disconnect.assert_not_called()


@pytest.mark.asyncio
async def test_oauth_get_status_returns_none_when_config_not_found(service, mock_coordinator):
    mock_coordinator.mcp_config_manager.get_config.return_value = None

    result = await service.oauth_get_status("nonexistent")

    assert result is None


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_templates_returns_dicts(service, mock_coordinator):
    t1, t2 = _make_template("t1"), _make_template("t2")
    mock_coordinator.template_manager.list_templates.return_value = [t1, t2]

    result = await service.list_templates()

    assert result["total"] == 2
    assert result["templates"][0] == t1.to_dict()


@pytest.mark.asyncio
async def test_get_template_returns_none_when_not_found(service, mock_coordinator):
    mock_coordinator.template_manager.get_template.return_value = None

    result = await service.get_template("nonexistent")

    assert result is None


@pytest.mark.asyncio
async def test_create_template_returns_dict(service, mock_coordinator):
    template = _make_template()
    mock_coordinator.template_manager.create_template.return_value = template

    result = await service.create_template(name="mytemplate")

    assert result == template.to_dict()
