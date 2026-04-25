"""MCP config and OAuth endpoints: /api/mcp-configs, /oauth/callback"""

import html
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..exception_handlers import handle_exceptions
from ..mcp_config_manager import McpServerType
from ._models import (
    McpConfigCreateRequest,
    McpConfigExportRequest,
    McpConfigImportRequest,
    McpConfigUpdateRequest,
    McpOAuthInitiateRequest,
)

logger = logging.getLogger(__name__)


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
        return await webui.service.create_mcp_config(
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
        )

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
        return await webui.service.update_mcp_config(
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
        )

    @router.delete("/api/mcp-configs/{config_id}")
    @handle_exceptions("delete MCP config")
    async def delete_mcp_config(config_id: str):
        """Delete an MCP server configuration"""
        success = await webui.service.delete_mcp_config(config_id)
        if not success:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return {"deleted": True}

    # ========== MCP OAuth Endpoints (issue #813) ==========

    @router.get("/oauth/callback", response_class=HTMLResponse)
    @handle_exceptions("handle oauth callback")
    async def oauth_callback(request: Request):
        """Handle OAuth 2.1 authorization code callback.

        Exempt from auth middleware — this route is reached before any token exists.
        On success broadcasts mcp_oauth_complete to all UI WebSocket clients.
        """
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")

        if error:
            error_desc = request.query_params.get("error_description", error)
            return HTMLResponse(
                content=f"""<!DOCTYPE html>
<html><head><title>OAuth Error</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<h2>&#x274C; Authorization Failed</h2>
<p>{html.escape(error_desc)}</p>
<p>You may close this window.</p>
</body></html>""",
                status_code=400,
            )

        if not code or not state:
            return HTMLResponse(
                content="""<!DOCTYPE html>
<html><head><title>OAuth Error</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<h2>&#x274C; Missing Parameters</h2>
<p>Authorization code or state parameter missing.</p>
<p>You may close this window.</p>
</body></html>""",
                status_code=400,
            )

        try:
            server_id = await webui.service.oauth_complete_flow(state, code)
            # Append OAuth completion to UI poll queue
            webui._broadcast_mcp_oauth_complete(server_id)
            return HTMLResponse(
                content="""<!DOCTYPE html>
<html><head><title>Connected</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<h2>&#x2705; Connected Successfully</h2>
<p>MCP server authorized. You may close this window.</p>
<script>window.close();</script>
</body></html>"""
            )
        except Exception as e:
            logger.exception("OAuth callback error")
            return HTMLResponse(
                content=f"""<!DOCTYPE html>
<html><head><title>OAuth Error</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px">
<h2>&#x274C; Authorization Failed</h2>
<p>{html.escape(str(e))}</p>
<p>You may close this window.</p>
</body></html>""",
                status_code=400,
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
