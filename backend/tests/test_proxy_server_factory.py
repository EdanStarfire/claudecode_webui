"""Unit tests for proxy_server_factory (issue #1484 §4.2)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server import Server
from mcp.types import (
    CallToolResult,
    CompleteResult,
    Completion,
    CompletionArgument,
    CompletionContext,
    EmptyResult,
    GetPromptResult,
    Prompt,
    PromptReference,
    ReadResourceResult,
    Resource,
    ResourceTemplate,
    TextContent,
    TextResourceContents,
    Tool,
)

from backend.mcp.proxy_server_factory import build_proxy_server
from backend.mcp_config_manager import McpServerConfig, McpServerType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _cfg(slug="my-mcp"):
    return McpServerConfig(
        id="cfg-1",
        name="My MCP",
        slug=slug,
        type=McpServerType.HTTP,
        url="https://example.com/mcp",
    )


def _shared_mgr(tools=None, call_result=None):
    mgr = MagicMock()
    mgr.list_tools_or_cached = AsyncMock(
        return_value=tools
        or [Tool(name="echo", description="echo tool", inputSchema={"type": "object"})]
    )
    mgr.call_tool = AsyncMock(
        return_value=call_result
        or CallToolResult(
            content=[TextContent(type="text", text="pong")], isError=False
        )
    )
    mgr.list_resources = AsyncMock(return_value=[])
    mgr.list_resource_templates = AsyncMock(return_value=[])
    mgr.read_resource = AsyncMock(return_value=ReadResourceResult(contents=[]))
    mgr.list_prompts = AsyncMock(return_value=[])
    mgr.get_prompt = AsyncMock(return_value=GetPromptResult(messages=[]))
    mgr.complete = AsyncMock(return_value=CompleteResult(completion=Completion(values=[])))
    mgr.set_logging_level = AsyncMock(return_value=EmptyResult())
    return mgr


# ---------------------------------------------------------------------------
# Shape assertions
# ---------------------------------------------------------------------------


def test_build_proxy_server_returns_sdk_config_dict():
    cfg = _cfg()
    mgr = _shared_mgr()
    result = build_proxy_server(cfg, mgr)

    assert result["type"] == "sdk"
    assert result["name"] == cfg.slug
    assert isinstance(result["instance"], Server)


# ---------------------------------------------------------------------------
# Handler delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_handler_delegates_to_shared_manager():
    cfg = _cfg()
    tools = [Tool(name="search", description="search", inputSchema={"type": "object"})]
    mgr = _shared_mgr(tools=tools)
    result = build_proxy_server(cfg, mgr)
    server: Server = result["instance"]

    # mcp 2.x: handlers are registered by method string, not request type, and
    # the handler itself returns the Result directly (no ServerResult wrapping).
    entry = server.get_request_handler("tools/list")
    response = await entry.handler(MagicMock(), None)

    mgr.list_tools_or_cached.assert_awaited_once_with(cfg)
    assert response.tools == tools


@pytest.mark.asyncio
async def test_call_tool_handler_delegates_and_returns_call_tool_result():
    cfg = _cfg()
    expected = CallToolResult(
        content=[TextContent(type="text", text="hello")], isError=False
    )
    mgr = _shared_mgr(call_result=expected)
    result = build_proxy_server(cfg, mgr)
    server: Server = result["instance"]

    entry = server.get_request_handler("tools/call")
    params = MagicMock()
    params.name = "echo"
    params.arguments = {"msg": "hi"}
    response = await entry.handler(MagicMock(), params)

    mgr.call_tool.assert_awaited_once_with(cfg, "echo", {"msg": "hi"})
    assert response == expected


# ---------------------------------------------------------------------------
# Each call to build_proxy_server creates a NEW Server instance
# ---------------------------------------------------------------------------


def test_build_proxy_server_creates_fresh_server_each_call():
    cfg = _cfg()
    mgr = _shared_mgr()
    s1 = build_proxy_server(cfg, mgr)["instance"]
    s2 = build_proxy_server(cfg, mgr)["instance"]
    assert s1 is not s2


# ---------------------------------------------------------------------------
# Issue #2234: resources/prompts/completion/logging-level handler delegation.
# Method strings (resources/list, resources/templates/list, resources/read,
# prompts/list, prompts/get, completion/complete, logging/setLevel) verified
# directly against the installed SDK's Server.__init__ _spec_requests table,
# the same way tools/list and tools/call are confirmed above.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_resources_handler_delegates_to_shared_manager():
    cfg = _cfg()
    resources = [Resource(name="r1", uri="file:///a.txt")]
    mgr = _shared_mgr()
    mgr.list_resources = AsyncMock(return_value=resources)
    server: Server = build_proxy_server(cfg, mgr)["instance"]

    entry = server.get_request_handler("resources/list")
    response = await entry.handler(MagicMock(), None)

    mgr.list_resources.assert_awaited_once_with(cfg)
    assert response.resources == resources


@pytest.mark.asyncio
async def test_list_resource_templates_handler_delegates_to_shared_manager():
    cfg = _cfg()
    templates = [ResourceTemplate(name="rt1", uriTemplate="file:///{name}.txt")]
    mgr = _shared_mgr()
    mgr.list_resource_templates = AsyncMock(return_value=templates)
    server: Server = build_proxy_server(cfg, mgr)["instance"]

    entry = server.get_request_handler("resources/templates/list")
    response = await entry.handler(MagicMock(), None)

    mgr.list_resource_templates.assert_awaited_once_with(cfg)
    assert response.resource_templates == templates


@pytest.mark.asyncio
async def test_read_resource_handler_delegates_and_returns_result():
    cfg = _cfg()
    expected = ReadResourceResult(
        contents=[TextResourceContents(uri="file:///a.txt", text="hi")]
    )
    mgr = _shared_mgr()
    mgr.read_resource = AsyncMock(return_value=expected)
    server: Server = build_proxy_server(cfg, mgr)["instance"]

    entry = server.get_request_handler("resources/read")
    params = MagicMock()
    params.uri = "file:///a.txt"
    response = await entry.handler(MagicMock(), params)

    mgr.read_resource.assert_awaited_once_with(cfg, "file:///a.txt")
    assert response == expected


@pytest.mark.asyncio
async def test_list_prompts_handler_delegates_to_shared_manager():
    cfg = _cfg()
    prompts = [Prompt(name="p1")]
    mgr = _shared_mgr()
    mgr.list_prompts = AsyncMock(return_value=prompts)
    server: Server = build_proxy_server(cfg, mgr)["instance"]

    entry = server.get_request_handler("prompts/list")
    response = await entry.handler(MagicMock(), None)

    mgr.list_prompts.assert_awaited_once_with(cfg)
    assert response.prompts == prompts


@pytest.mark.asyncio
async def test_get_prompt_handler_delegates_and_returns_result():
    cfg = _cfg()
    expected = GetPromptResult(messages=[])
    mgr = _shared_mgr()
    mgr.get_prompt = AsyncMock(return_value=expected)
    server: Server = build_proxy_server(cfg, mgr)["instance"]

    entry = server.get_request_handler("prompts/get")
    params = MagicMock()
    params.name = "p1"
    params.arguments = {"arg": "val"}
    response = await entry.handler(MagicMock(), params)

    mgr.get_prompt.assert_awaited_once_with(cfg, "p1", {"arg": "val"})
    assert response == expected


@pytest.mark.asyncio
async def test_complete_handler_maps_completion_argument_object_to_plain_dict():
    """CompleteRequestParams.argument is a CompletionArgument object
    (.name/.value), not the plain dict ClientSession.complete() expects, and
    .context is a CompletionContext|None wrapping a plain dict — the handler
    closure must map both before delegating to the shared manager.

    Uses an argument name other than "name" deliberately: ClientSession.complete()
    reconstructs the dict via CompletionArgument(**argument), which requires the
    literal keys "name"/"value" regardless of what the completed argument is
    actually called — a dict built as {params.argument.name: params.argument.value}
    would pass this test by coincidence if the argument name happened to be "name".
    """
    cfg = _cfg()
    expected = CompleteResult(completion=Completion(values=["foo", "foobar"]))
    mgr = _shared_mgr()
    mgr.complete = AsyncMock(return_value=expected)
    server: Server = build_proxy_server(cfg, mgr)["instance"]

    entry = server.get_request_handler("completion/complete")
    params = MagicMock()
    params.ref = PromptReference(name="p1")
    params.argument = CompletionArgument(name="city", value="fo")
    params.context = CompletionContext(arguments={"other": "bar"})
    response = await entry.handler(MagicMock(), params)

    mgr.complete.assert_awaited_once_with(
        cfg, params.ref, {"name": "city", "value": "fo"}, {"other": "bar"}
    )
    assert response == expected


@pytest.mark.asyncio
async def test_complete_handler_maps_none_context_to_none():
    cfg = _cfg()
    mgr = _shared_mgr()
    server: Server = build_proxy_server(cfg, mgr)["instance"]

    entry = server.get_request_handler("completion/complete")
    params = MagicMock()
    params.ref = PromptReference(name="p1")
    params.argument = CompletionArgument(name="city", value="fo")
    params.context = None
    await entry.handler(MagicMock(), params)

    mgr.complete.assert_awaited_once_with(
        cfg, params.ref, {"name": "city", "value": "fo"}, None
    )


@pytest.mark.asyncio
async def test_set_logging_level_handler_delegates_to_shared_manager():
    cfg = _cfg()
    mgr = _shared_mgr()
    server: Server = build_proxy_server(cfg, mgr)["instance"]

    entry = server.get_request_handler("logging/setLevel")
    params = MagicMock()
    params.level = "debug"
    response = await entry.handler(MagicMock(), params)

    mgr.set_logging_level.assert_awaited_once_with(cfg, "debug")
    assert isinstance(response, EmptyResult)


# ---------------------------------------------------------------------------
# Regression: existing tools path unaffected by the new handlers.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_tools_handlers_still_registered_alongside_new_ones():
    cfg = _cfg()
    mgr = _shared_mgr()
    server: Server = build_proxy_server(cfg, mgr)["instance"]

    for method in (
        "tools/list",
        "tools/call",
        "resources/list",
        "resources/templates/list",
        "resources/read",
        "prompts/list",
        "prompts/get",
        "completion/complete",
        "logging/setLevel",
    ):
        assert server.get_request_handler(method) is not None, (
            f"{method} handler must be registered"
        )
