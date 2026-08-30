"""MCP config endpoints: /api/mcp-configs*

Issue #498 three-way split by `shared_connection` (mcp_config_manager.py:57):
- Pattern A (simple relay): get/update/delete {config_id}, oauth
  initiate/disconnect/status/import-as-secret, test-connect — all non-shared-only
  operations, unconditional relay when REMOTE is configured (shared configs never
  relay, so a config_id found locally as shared_connection=True always stays local).
- Pattern C (fan-out-and-merge): list/export — LOCAL mode unchanged; REMOTE mode
  fetches REMOTE's full list, filters to shared_connection == False, unions with the
  Hub's own local shared_connection == True configs.
- Pattern D (split-and-route): import — per-entry, shared_connection=True creates on
  the Hub's local store; shared_connection=False creates via whichever store is
  currently active for non-shared configs (implemented in
  ApplicationService.import_mcp_configs).

/oauth/callback moved to core.py (issue #498) — it's a bare, non-/api route with no
REMOTE mirror, incompatible with this file's relative-path + prefix registration.
"""

from fastapi import APIRouter, HTTPException, Request

from .. import relay_client
from ..exception_handlers import handle_exceptions
from ..mcp_config_manager import McpServerType
from ..session_backend import BackendMode
from ._models import (
    McpConfigCreateRequest,
    McpConfigExportRequest,
    McpConfigImportRequest,
    McpConfigUpdateRequest,
    McpOAuthImportAsSecretRequest,
    McpOAuthInitiateRequest,
)


def _portable_entry(c) -> dict:
    entry: dict = {
        "type": c.type.value, "enabled": c.enabled, "shared_connection": c.shared_connection,
    }
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
    return entry


def _portable_entry_from_remote_dict(c: dict) -> dict:
    """Same shape as _portable_entry, but from a REMOTE-relayed dict (list_configs()
    JSON shape) rather than a local McpServerConfig object."""
    entry: dict = {
        "type": c.get("type"), "enabled": c.get("enabled"),
        "shared_connection": c.get("shared_connection", False),
    }
    if c.get("type") == McpServerType.STDIO.value:
        entry["command"] = c.get("command")
        if c.get("args"):
            entry["args"] = c["args"]
        if c.get("env"):
            entry["env"] = c["env"]
    else:
        entry["url"] = c.get("url")
        if c.get("headers"):
            entry["headers"] = c["headers"]
        oauth_client_id = c.get("oauth_client_id")
        oauth_callback_port = c.get("oauth_callback_port")
        if oauth_client_id or oauth_callback_port:
            oauth: dict = {}
            if oauth_client_id:
                oauth["clientId"] = oauth_client_id
            if oauth_callback_port:
                oauth["callbackPort"] = oauth_callback_port
            entry["oauth"] = oauth
    return entry


def build_router(webui) -> APIRouter:
    router = APIRouter()

    def _is_remote() -> bool:
        return webui.coordinator.backend_mode == BackendMode.REMOTE

    async def _local_shared_config(config_id: str):
        """Return the Hub-local config only if it exists AND is shared_connection —
        the seam every Pattern-A per-ID route below uses to decide local-vs-relay."""
        config = await webui.coordinator.mcp_config_manager.get_config(config_id)
        if config is not None and config.shared_connection:
            return config
        return None

    # ==================== Pattern C: list / export ====================

    @router.get("/mcp-configs")
    @handle_exceptions("list MCP configs")
    async def list_mcp_configs(limit: int = 100, offset: int = 0):
        """List global MCP server configurations, paginated"""
        if _is_remote():
            local = await webui.coordinator.mcp_config_manager.list_configs()
            local_shared = [c.to_dict() for c in local if c.shared_connection]
            remote_all = await webui.coordinator.backend.list_mcp_configs()
            remote_non_shared = [c for c in remote_all if not c.get("shared_connection")]
            merged = local_shared + remote_non_shared
            total = len(merged)
            sliced = merged[offset : offset + limit]
            return {
                "configs": sliced, "total": total, "limit": limit, "offset": offset,
                "has_more": offset + len(sliced) < total,
            }
        return await webui.service.list_mcp_configs(limit=limit, offset=offset)

    @router.post("/mcp-configs/export")
    @handle_exceptions("export MCP configs")
    async def export_mcp_configs(request: McpConfigExportRequest):
        """Export MCP server configurations as portable named dict (issue #788)"""
        local = await webui.coordinator.mcp_config_manager.list_configs()
        local_shared = [c for c in local if c.shared_connection]
        if request.ids is not None:
            id_set = set(request.ids)
            local_shared = [c for c in local_shared if c.id in id_set]

        portable: dict = {c.name: _portable_entry(c) for c in local_shared}

        if _is_remote():
            remote_all = await webui.coordinator.backend.list_mcp_configs()
            remote_non_shared = [c for c in remote_all if not c.get("shared_connection")]
            if request.ids is not None:
                remote_non_shared = [c for c in remote_non_shared if c.get("id") in id_set]
            for c in remote_non_shared:
                portable[c["name"]] = _portable_entry_from_remote_dict(c)
        else:
            local_non_shared = [c for c in local if not c.shared_connection]
            if request.ids is not None:
                local_non_shared = [c for c in local_non_shared if c.id in id_set]
            for c in local_non_shared:
                portable[c.name] = _portable_entry(c)

        return portable

    # ==================== Pattern D: import (see ApplicationService) ====================

    @router.post("/mcp-configs/import")
    @handle_exceptions("import MCP configs")
    async def import_mcp_configs(request: McpConfigImportRequest):
        """Import MCP server configurations with dry_run preview support (issue #788).

        Per-entry shared_connection routing happens inside
        ApplicationService.import_mcp_configs — not a plain relay, since a single
        import batch can contain both shared (Hub-local) and non-shared
        (REMOTE-when-configured) entries.
        """
        return await webui.service.import_mcp_configs(
            servers=request.servers, dry_run=request.dry_run
        )

    # ==================== create: shared always local, non-shared relays ====================

    @router.post("/mcp-configs")
    @handle_exceptions("create MCP config", value_error_status=400)
    async def create_mcp_config(request: McpConfigCreateRequest, http_request: Request):
        """Create a new global MCP server configuration"""
        if not request.shared_connection and _is_remote():
            # Non-shared + REMOTE configured: that's where non-shared configs live
            # going forward, full stop — no per-request backend hint needed.
            return await relay_client.forward(webui.coordinator, http_request)
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

    # ==================== Pattern A: per-config-id routes ====================

    @router.get("/mcp-configs/{config_id}")
    @handle_exceptions("get MCP config")
    async def get_mcp_config(config_id: str, request: Request):
        """Get a specific MCP server configuration"""
        if _is_remote() and await _local_shared_config(config_id) is None:
            return await relay_client.forward(webui.coordinator, request)
        config = await webui.service.get_mcp_config(config_id)
        if not config:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return config

    @router.put("/mcp-configs/{config_id}")
    @handle_exceptions("update MCP config", value_error_status=400)
    async def update_mcp_config(config_id: str, request: McpConfigUpdateRequest, http_request: Request):
        """Update an existing MCP server configuration"""
        if _is_remote() and await _local_shared_config(config_id) is None:
            return await relay_client.forward(webui.coordinator, http_request)
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

    @router.delete("/mcp-configs/{config_id}")
    @handle_exceptions("delete MCP config")
    async def delete_mcp_config(config_id: str, request: Request):
        """Delete an MCP server configuration"""
        if _is_remote() and await _local_shared_config(config_id) is None:
            return await relay_client.forward(webui.coordinator, request)
        success = await webui.service.delete_mcp_config(config_id)
        if not success:
            raise HTTPException(status_code=404, detail="MCP config not found")
        await webui._remove_oauth_callback_for_config(config_id)
        return {"deleted": True}

    # ========== MCP OAuth Endpoints (issue #813) — Pattern A, non-shared only ==========

    @router.post("/mcp-configs/{config_id}/oauth/initiate")
    @handle_exceptions("initiate MCP OAuth")
    async def initiate_mcp_oauth(config_id: str, request: McpOAuthInitiateRequest, http_request: Request):
        """Initiate OAuth 2.1 flow for an MCP server.

        Returns the authorization URL the frontend should open in a popup.
        """
        if _is_remote() and await _local_shared_config(config_id) is None:
            return await relay_client.forward(webui.coordinator, http_request)
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

    @router.post("/mcp-configs/{config_id}/oauth/disconnect")
    @handle_exceptions("disconnect MCP OAuth")
    async def disconnect_mcp_oauth(config_id: str, request: Request):
        """Clear stored OAuth tokens for an MCP server."""
        if _is_remote() and await _local_shared_config(config_id) is None:
            return await relay_client.forward(webui.coordinator, request)
        success = await webui.service.oauth_disconnect(config_id)
        if not success:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return {"disconnected": True}

    @router.post("/mcp-configs/{config_id}/oauth/import-as-secret")
    @handle_exceptions("import OAuth as secret", value_error_status=400)
    async def import_oauth_as_secret(
        config_id: str, request: McpOAuthImportAsSecretRequest, http_request: Request
    ):
        """Import stored OAuth 2.1 tokens as proxy-injectable vault secrets (issue #1381).

        Creates up to 3 vault secrets (oauth2 primary, refresh token, client secret) and
        updates the MCP server's Authorization header to ${secret:<base_name>}.
        """
        if _is_remote() and await _local_shared_config(config_id) is None:
            return await relay_client.forward(webui.coordinator, http_request)
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

    @router.get("/mcp-configs/{config_id}/tools")
    @handle_exceptions("get MCP config tools", value_error_status=400)
    async def get_mcp_config_tools(config_id: str):
        """Return live tool list for a shared-connection MCP server (issue #1799).

        Shared-only by construction (raises ValueError for non-shared configs) — never
        relays, since shared configs always live on the Hub regardless of backend_mode.

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

    @router.post("/mcp-configs/{config_id}/test-connect")
    @handle_exceptions("test-connect MCP config", value_error_status=400)
    async def test_connect_mcp_config(config_id: str, request: Request):
        """Open a throwaway connection to a non-shared MCP config, list its tools, and
        close it immediately (issue #1800). Blocks until the outcome is known (up to
        ~30s on a hung server).

        Returns {"status": "disabled"|"needs-auth"|"connected"|"failed",
                 "stage": "transport"|"handshake"|"list_tools"|None,
                 "tools": [...], "error": str|None}.
        """
        if _is_remote() and await _local_shared_config(config_id) is None:
            return await relay_client.forward(webui.coordinator, request)
        result = await webui.service.test_connect_mcp_config(config_id)
        if result is None:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return result

    @router.get("/mcp-configs/{config_id}/oauth/status")
    @handle_exceptions("get MCP OAuth status")
    async def get_mcp_oauth_status(config_id: str, request: Request):
        """Return OAuth status for this MCP server.

        Returns {"status": "authenticated" | "expired" | "unauthenticated"}.
        Expiry is determined from the timestamp recorded at token storage time.
        """
        if _is_remote() and await _local_shared_config(config_id) is None:
            return await relay_client.forward(webui.coordinator, request)
        result = await webui.service.oauth_get_status(config_id)
        if result is None:
            raise HTTPException(status_code=404, detail="MCP config not found")
        return result

    return router
