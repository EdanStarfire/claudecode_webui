"""
proxy_server_factory — Issue #1484

build_proxy_server(cfg, shared_mgr) -> McpSdkServerConfig dict

Creates a fresh in-process mcp.server.Server per WebUI session whose
list_tools and call_tool handlers delegate to a shared upstream session
held by SharedMcpConnectionManager. Wraps the result in the SDK's
McpSdkServerConfig dict so it can be dropped into ClaudeAgentOptions
.mcp_servers without further translation.
"""

from mcp.server import Server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    CompleteRequestParams,
    CompleteResult,
    EmptyResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    ReadResourceRequestParams,
    ReadResourceResult,
    SetLevelRequestParams,
)


def build_proxy_server(cfg, shared_mgr) -> dict:
    """Return a McpSdkServerConfig dict for the SDK's mcp_servers map."""

    # Issue #1982: degrade instead of raise — the SDK calls list_tools() on every
    # attached server at its own connect time, so a failed shared server must not
    # take down the SDK's own connection to this proxy.
    async def _list_tools(ctx, params) -> ListToolsResult:
        return ListToolsResult(tools=await shared_mgr.list_tools_or_cached(cfg))

    # No get_tool_input_schema is passed to Server(...) below: upstream server
    # validates; cached Tool schemas on this proxy may temporarily lag after
    # notifications/tools/list_changed.
    async def _call_tool(ctx, params: CallToolRequestParams) -> CallToolResult:
        return await shared_mgr.call_tool(cfg, params.name, params.arguments or {})

    # Issue #2234: mirrors _list_tools' degrade-not-raise shape — same
    # connect-time-eager-listing risk may apply to resources/prompts too.
    async def _list_resources(ctx, params) -> ListResourcesResult:
        return ListResourcesResult(resources=await shared_mgr.list_resources(cfg))

    async def _list_resource_templates(ctx, params) -> ListResourceTemplatesResult:
        return ListResourceTemplatesResult(
            resource_templates=await shared_mgr.list_resource_templates(cfg)
        )

    async def _read_resource(ctx, params: ReadResourceRequestParams) -> ReadResourceResult:
        return await shared_mgr.read_resource(cfg, params.uri)

    async def _list_prompts(ctx, params) -> ListPromptsResult:
        return ListPromptsResult(prompts=await shared_mgr.list_prompts(cfg))

    async def _get_prompt(ctx, params: GetPromptRequestParams) -> GetPromptResult:
        return await shared_mgr.get_prompt(cfg, params.name, params.arguments)

    async def _complete(ctx, params: CompleteRequestParams) -> CompleteResult:
        # CompleteRequestParams.argument is a CompletionArgument object
        # (.name/.value), not the plain dict ClientSession.complete() expects —
        # and ClientSession.complete() reconstructs it via
        # CompletionArgument(**argument), so the dict needs the literal keys
        # "name"/"value", not {argument.name: argument.value}.
        # .context is a CompletionContext|None wrapping a plain dict.
        argument = {"name": params.argument.name, "value": params.argument.value}
        context_arguments = params.context.arguments if params.context else None
        return await shared_mgr.complete(cfg, params.ref, argument, context_arguments)

    async def _set_logging_level(ctx, params: SetLevelRequestParams) -> EmptyResult:
        return await shared_mgr.set_logging_level(cfg, params.level)

    server = Server(
        cfg.slug,
        version="1.0.0",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
        on_list_resources=_list_resources,
        on_list_resource_templates=_list_resource_templates,
        on_read_resource=_read_resource,
        on_list_prompts=_list_prompts,
        on_get_prompt=_get_prompt,
        on_completion=_complete,
        on_set_logging_level=_set_logging_level,
    )

    return {"type": "sdk", "name": cfg.slug, "instance": server}
