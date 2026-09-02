"""MCP config and OAuth endpoints: /api/mcp-configs, /oauth/callback"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..exception_handlers import handle_exceptions
from ..mcp_config_manager import McpServerType
from ..oauth_callback_listener_manager import render_oauth_callback
from ._models import (
    McpConfigCreateRequest,
    McpConfigExportRequest,
    McpConfigImportRequest,
    McpConfigUpdateRequest,
    McpOAuthImportAsSecretRequest,
    McpOAuthInitiateRequest,
)


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/mcp-configs")
    @handle_exceptions("list MCP configs")
    async def list_mcp_configs(limit: int = 100, offset: int = 0):
        """List global MCP server configurations, paginated"""
        return await webui.service.list_mcp_configs(limit=limit, offset=offset)

    @router.post("/api/mcp-configs")
    @handle_exceptions("create MCP config", value_error_status=400)
    async def create_mcp_config(request: McpConfigCreateRequest):
        """Create a new global MCP server configuration"""
        if request.oauth_custom_callback_path and webui.oauth_callback_path_conflicts_with_app_route(
            request.oauth_custom_callback_path
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"oauth_custom_callback_path '{request.oauth_custom_callback_path}' "
                    "conflicts with an existing application route"
                ),
            )
        result = await webui.service.create_mcp_config(
            name=request.name,
            server_type=request.type,
            command=request.command,
            args=request.args,
            env=request.env,
            url=request.url,
            headers=request.headers,
            enabled=request.enabled,
            oauth_enabled=request.oauth_enabled,
            oauth_client_id=request.oauth_client_id,
            oauth_callback_port=request.oauth_callback_port,
            shared_connection=request.shared_connection,
            oauth_custom_callback_path=request.oauth_custom_callback_path,
            oauth_custom_callback_port=request.oauth_custom_callback_port,
        )
        # Issue #1789: wire the custom callback route/listener synchronously with the write.
        config_obj = await webui.coordinator.mcp_config_manager.get_config(result["id"])
        await webui._sync_oauth_callback_for_config(config_obj)
        return result

    @router.post("/api/mcp-configs/export")
    @handle_exceptions("export MCP configs")
    async def export_mcp_configs(request: McpConfigExportRequest):
        """Export MCP server configurations as portable named dict (issue #788)"""
        all_configs = await webui.service.export_mcp_configs(ids=request.ids)
        portable: dict = {}
        for c in all_configs:
            entry: dict = {"type": c.type.value, "enabled": c.enabled}
            if c.type == McpServerType.STDIO:
                entry["command"] = c.command
                if c.args:
                    entry["args"] = c.args
                if c.env:
                    entry["env"] = c.env
            else:
                entry["url"] = c.url
                if c.headers:
                    entry["headers"] = c.headers
                if c.oauth_client_id or c.oauth_callback_port:
                    oauth: dict = {}
                    if c.oauth_client_id:
                        oauth["clientId"] = c.oauth_client_id
                    if c.oauth_callback_port:
                        oauth["callbackPort"] = c.oauth_callback_port
                    entry["oauth"] = oauth
            portable[c.name] = entry
        return portable

    @router.post("/api/mcp-configs/import")
    @handle_exceptions("import MCP configs")
    async def import_mcp_configs(request: McpConfigImportRequest):
        """Import MCP server configurations with dry_run preview support (issue #788)"""
        return await webui.service.import_mcp_configs(
            servers=request.servers, dry_run=request.dry_run
        )

    @router.get("/api/mcp-configs/{config_id}")
    @handle_exceptions("get MCP config")
    async def get_mcp_config(config_id: str):
        """Get a specific MCP server configuration"""
        config = await webui.service.get_mcp_config(config_id)
        if not config:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return config

    @router.put("/api/mcp-configs/{config_id}")
    @handle_exceptions("update MCP config", value_error_status=400)
    async def update_mcp_config(config_id: str, request: McpConfigUpdateRequest):
        """Update an existing MCP server configuration"""
        # Issue #1789: only forward oauth_custom_callback_path/port when the caller actually
        # set them. Unlike the request's other fields, these two are the sole way to *clear*
        # a live custom callback back to the default — always forwarding them (even when
        # omitted, where Pydantic defaults to None) would silently wipe an existing custom
        # callback on any partial update that doesn't happen to resend it (e.g. `{"enabled":
        # false}`), tearing down its listener/route as an unintended side effect.
        custom_callback_kwargs = {}
        if "oauth_custom_callback_path" in request.model_fields_set:
            custom_callback_kwargs["oauth_custom_callback_path"] = request.oauth_custom_callback_path
            if request.oauth_custom_callback_path and webui.oauth_callback_path_conflicts_with_app_route(
                request.oauth_custom_callback_path
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"oauth_custom_callback_path '{request.oauth_custom_callback_path}' "
                        "conflicts with an existing application route"
                    ),
                )
        if "oauth_custom_callback_port" in request.model_fields_set:
            custom_callback_kwargs["oauth_custom_callback_port"] = request.oauth_custom_callback_port

        result = await webui.service.update_mcp_config(
            config_id,
            name=request.name,
            server_type=request.type,
            command=request.command,
            args=request.args,
            env=request.env,
            url=request.url,
            headers=request.headers,
            enabled=request.enabled,
            oauth_enabled=request.oauth_enabled,
            oauth_client_id=request.oauth_client_id,
            oauth_callback_port=request.oauth_callback_port,
            shared_connection=request.shared_connection,
            **custom_callback_kwargs,
        )
        # Issue #1789: wire the custom callback route/listener synchronously with the write
        # (covers enable/disable too, since that's just enabled=False via this same endpoint).
        config_obj = await webui.coordinator.mcp_config_manager.get_config(config_id)
        if config_obj is not None:
            await webui._sync_oauth_callback_for_config(config_obj)
        return result

    @router.delete("/api/mcp-configs/{config_id}")
    @handle_exceptions("delete MCP config")
    async def delete_mcp_config(config_id: str):
        """Delete an MCP server configuration"""
        success = await webui.service.delete_mcp_config(config_id)
        if not success:
            raise HTTPException(status_code=404, detail="MCP config not found")
        await webui._remove_oauth_callback_for_config(config_id)
        return {"deleted": True}

    # ========== MCP OAuth Endpoints (issue #813) ==========

    @router.get("/oauth/callback", response_class=HTMLResponse)
    @handle_exceptions("handle oauth callback")
    async def oauth_callback(request: Request):
        """Handle OAuth 2.1 authorization code callback.

        Exempt from auth middleware — this route is reached before any token exists.
        On success broadcasts mcp_oauth_complete to all UI WebSocket clients.

        Shares its parsing/rendering logic with per-config custom callback routes/listeners
        (issue #1789) via render_oauth_callback().
        """
        return await render_oauth_callback(
            request, webui.coordinator.oauth_callback_listener_manager.complete_and_broadcast
        )

    @router.post("/api/mcp-configs/{config_id}/oauth/initiate")
    @handle_exceptions("initiate MCP OAuth")
    async def initiate_mcp_oauth(config_id: str, request: McpOAuthInitiateRequest):
        """Initiate OAuth 2.1 flow for an MCP server.

        Returns the authorization URL the frontend should open in a popup.
        """
        config = await webui.service.get_mcp_config(config_id)
        if not config:
            raise HTTPException(status_code=404, detail="MCP config not found")
        if not config.get("url"):
            raise HTTPException(status_code=400, detail="OAuth requires a URL-based MCP server")
        auth_url = await webui.service.oauth_initiate_flow(
            config_id=config_id,
            server_url=config["url"],
            redirect_uri=request.redirect_uri,
            client_name=f"Claude Code WebUI — {config['name']}",
        )
        return {"auth_url": auth_url}

    @router.post("/api/mcp-configs/{config_id}/oauth/disconnect")
    @handle_exceptions("disconnect MCP OAuth")
    async def disconnect_mcp_oauth(config_id: str):
        """Clear stored OAuth tokens for an MCP server."""
        success = await webui.service.oauth_disconnect(config_id)
        if not success:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return {"disconnected": True}

    @router.post("/api/mcp-configs/{config_id}/oauth/import-as-secret")
    @handle_exceptions("import OAuth as secret", value_error_status=400)
    async def import_oauth_as_secret(config_id: str, request: McpOAuthImportAsSecretRequest):
        """Import stored OAuth 2.1 tokens as proxy-injectable vault secrets (issue #1381).

        Creates up to 3 vault secrets (oauth2 primary, refresh token, client secret) and
        updates the MCP server's Authorization header to ${secret:<base_name>}.
        """
        from fastapi.responses import JSONResponse
        try:
            result = await webui.service.import_oauth_as_secret(
                config_id, request.base_name, replace=request.replace
            )
        except LookupError as e:
            msg = str(e)
            detail = msg[5:].strip() if msg.startswith("404:") else msg
            raise HTTPException(status_code=404, detail=detail) from e
        except KeyError as e:
            msg = str(e).strip("'")
            detail = msg[5:].strip() if msg.startswith("409:") else msg
            raise HTTPException(status_code=409, detail=detail) from e
        return JSONResponse(status_code=201, content=result)

    @router.get("/api/mcp-configs/{config_id}/tools")
    @handle_exceptions("get MCP config tools", value_error_status=400)
    async def get_mcp_config_tools(config_id: str):
        """Return live tool list for a shared-connection MCP server (issue #1799).

        Triggers a connection attempt (opening one if not already open) and blocks
        until the outcome is known — can take up to ~35s on a broken connection, or
        up to ~70s if a concurrent OAuth token-refresh reconnect is also contending
        for the same config's open-lock (issue #1806).
        Returns {"status": "disabled"|"needs-auth"|"connected"|"failed", "tools": [...], "error": str|None}.
        """
        result = await webui.service.get_mcp_config_tools(config_id)
        if result is None:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return result

    @router.post("/api/mcp-configs/{config_id}/test-connect")
    @handle_exceptions("test-connect MCP config", value_error_status=400)
    async def test_connect_mcp_config(config_id: str):
        """Open a throwaway connection to a non-shared MCP config, list its tools, and
        close it immediately (issue #1800). Blocks until the outcome is known (up to
        ~30s on a hung server).

        Returns {"status": "disabled"|"needs-auth"|"connected"|"failed",
                 "stage": "transport"|"handshake"|"list_tools"|None,
                 "tools": [...], "error": str|None}.
        """
        result = await webui.service.test_connect_mcp_config(config_id)
        if result is None:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return result

    @router.get("/api/mcp-configs/{config_id}/oauth/status")
    @handle_exceptions("get MCP OAuth status")
    async def get_mcp_oauth_status(config_id: str):
        """Return OAuth status for this MCP server.

        Returns {"status": "authenticated" | "expired" | "unauthenticated"}.
        Expiry is determined from the timestamp recorded at token storage time.
        """
        result = await webui.service.oauth_get_status(config_id)
        if result is None:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return result

    return router
