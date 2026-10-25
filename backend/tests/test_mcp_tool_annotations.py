"""Issue #2269: ToolAnnotations hints pinned for every built-in in-process MCP tool."""

from unittest.mock import MagicMock

import pytest

from backend.legion_system import LegionSystem
from backend.mcp.links_mcp_tools import LinksMCPTools
from backend.mcp.resource_mcp_tools import ResourceMCPTools

# (readOnly, destructive, idempotent, openWorld)
_RO = (True, False, True, False)
_WRITE = (False, False, False, False)
_UPSERT = (False, False, True, False)

EXPECTED = {
    "legion": {
        "list_minions": _RO,
        "get_minion_info": _RO,
        "search_capability": _RO,
        "whoami": _RO,
        "list_templates": _RO,
        "list_schedules": _RO,
        "send_comm": _WRITE,
        "spawn_minion": _WRITE,
        "queue_task": _WRITE,
        "update_expertise": _UPSERT,
        "pause_schedule": _UPSERT,
        "resume_schedule": _UPSERT,
        "reparent_minion": _UPSERT,
        "create_schedule": (False, False, False, True),
        "update_schedule": (False, False, True, True),
        "delete_schedule": (False, True, True, False),
        "dispose_minion": (False, True, True, False),
        "restart_session": (False, True, False, False),
    },
    "resources": {
        "register_resource": _WRITE,
        "register_image": _WRITE,
        "list_resources": _RO,
        "get_resource": _RO,
    },
    "links": {
        "register_link": _UPSERT,
        "list_links": _RO,
    },
}


async def _tools(server_config):
    entry = server_config["instance"].get_request_handler("tools/list")
    result = await entry.handler(None, None)
    return {t.name: t for t in result.tools}


def _server(kind):
    if kind == "legion":
        system = LegionSystem(session_port=MagicMock(), data_storage_manager=MagicMock(),
                              template_manager=MagicMock())
        return system.mcp_tools.create_mcp_server_for_session("s1")
    if kind == "resources":
        return ResourceMCPTools(MagicMock()).create_mcp_server_for_session("s1")
    return LinksMCPTools(MagicMock()).create_mcp_server_for_session("s1")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(EXPECTED))
async def test_issue_2269_annotations_match_table(kind):
    tools = await _tools(_server(kind))
    # Completeness guard: a new tool must be added to the table with explicit hints.
    assert set(tools) == set(EXPECTED[kind])
    for name, expected in EXPECTED[kind].items():
        ann = tools[name].annotations
        assert ann is not None, name
        actual = (ann.read_only_hint, ann.destructive_hint, ann.idempotent_hint, ann.open_world_hint)
        assert actual == expected, name


def test_issue_2269_total_tool_count():
    assert sum(len(v) for v in EXPECTED.values()) == 24
