"""
Secrets endpoints: /api/secrets/*, /api/sessions/{id}/secrets/resolve

Issue #827: Host-level secrets storage via keyring (storage + API layer).

The resolve endpoint uses a per-session Bearer token (not the global auth token)
so the proxy sidecar can fetch its assigned secrets without knowing the global
operator token.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from shared.exception_handlers import handle_exceptions

from ._models import (
    SecretCreateRequest,
    SecretOAuthInitiateRequest,
    SecretOAuthReconnectInitiateRequest,
    SecretUpdateRequest,
)


def _build_session_token_auth(webui):
    """Return a FastAPI dependency that validates per-session Bearer tokens."""

    async def session_token_auth(request: Request, session_id: str) -> str:
        """Validate Authorization: Bearer {session_token} for the resolve endpoint.

        Returns session_id on success; raises 401/404 on failure.
        """
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        token = auth_header[7:].strip()
        if not token:
            raise HTTPException(status_code=401, detail="Empty Bearer token")

        session = await webui.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        expected = getattr(session, "secret_fetch_token", None)
        if not expected or token != expected:
            raise HTTPException(status_code=401, detail="Invalid session token")

        return session_id

    return session_token_auth


def build_router(webui) -> APIRouter:
    router = APIRouter()
    session_token_auth = _build_session_token_auth(webui)

    # ==================== SECRETS CRUD ====================

    @router.get("/api/secrets")
    @handle_exceptions("list secrets")
    async def list_secrets():
        """List all secret metadata. Never includes secret values."""
        return await webui.service.list_secrets()

    @router.post("/api/secrets", status_code=201)
    @handle_exceptions("create secret", value_error_status=400)
    async def create_secret(request: SecretCreateRequest):
        """Create a named secret. Returns metadata only (value not in response)."""
        result = await webui.service.create_secret(
            name=request.name,
            secret_type=request.type,
            target_hosts=request.target_hosts,
            value=request.value,
            inject_env=request.inject_env,
            inject_file=request.inject_file,
            scrub=request.scrub,
            username=request.username,
            injection=request.injection,
            refresh=request.refresh,
        )
        # Issue #1387: schedule background refresh if new secret is oauth2 with refresh config
        if result.get("type") == "oauth2" and result.get("refresh"):
            webui.coordinator.vault_refresh_manager.schedule_secret(request.name)
        return result

    @router.patch("/api/secrets/{name}")
    @handle_exceptions("update secret", value_error_status=400)
    async def update_secret(name: str, request: SecretUpdateRequest):
        """Update secret metadata and/or value. Returns updated metadata."""
        result = await webui.service.update_secret(
            name=name,
            secret_type=request.type,
            target_hosts=request.target_hosts,
            value=request.value,
            inject_env=request.inject_env,
            inject_file=request.inject_file,
            scrub=request.scrub,
            username=request.username,
            injection=request.injection,
            refresh=request.refresh,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Secret not found")
        # Issue #1387: reschedule refresh on update (refresh config may have changed)
        if result.get("type") == "oauth2" and result.get("refresh"):
            webui.coordinator.vault_refresh_manager.schedule_secret(name)
        return result

    @router.delete("/api/secrets/{name}")
    @handle_exceptions("delete secret")
    async def delete_secret(name: str):
        """Delete a secret by name (removes metadata + keyring entry)."""
        deleted = await webui.service.delete_secret(name)
        if not deleted:
            raise HTTPException(status_code=404, detail="Secret not found")
        # Issue #1387: cancel any background refresh task for this secret
        webui.coordinator.vault_refresh_manager.unschedule_secret(name)
        return {"deleted": True}

    @router.post("/api/secrets/{name}/refresh")
    @handle_exceptions("refresh secret")
    async def refresh_secret(name: str):
        """Manually trigger an OAuth2 token refresh for an oauth2 secret.

        Calls the token_url with the stored refresh_token; writes the new
        access_token (and rotated refresh_token if any) back to the keyring.
        Returns 404 if the secret does not exist or is not oauth2 type.
        Returns 502 if the token endpoint returns an error.
        """
        result = await webui.service.refresh_secret(name)
        if result is None:
            raise HTTPException(status_code=404, detail="Secret not found or not oauth2 type")
        return result

    # ============ STANDALONE OAUTH2 GUIDED AUTHORIZATION (issue #1871) ============

    def _check_custom_callback_path(path: str | None) -> None:
        """Reject a custom_callback_path that collides with a real application route
        — the same guard MCP config create/update already enforces
        (backend/routers/mcp.py) before accepting oauth_custom_callback_path. Without
        this, _add_dynamic_oauth_route() would insert a route ahead of (and, on
        teardown, delete) the real one for the duration of the pending flow."""
        if path and webui.oauth_callback_path_conflicts_with_app_route(path):
            raise HTTPException(
                status_code=400,
                detail=f"custom_callback_path '{path}' conflicts with an existing application route",
            )

    @router.post("/api/secrets/oauth/initiate")
    @handle_exceptions("initiate standalone OAuth", value_error_status=400)
    async def initiate_standalone_oauth(request: SecretOAuthInitiateRequest):
        """Start the guided browser-authorization flow for a new standalone oauth2
        secret. Returns the auth_url the frontend should open in a popup, plus a
        flow_id (the OAuth `state` value) used to correlate the eventual
        secret_oauth_complete poll event."""
        _check_custom_callback_path(request.custom_callback_path)
        try:
            result = await webui.service.standalone_oauth_initiate(
                base_name=request.base_name,
                authorization_endpoint=request.authorization_endpoint,
                token_endpoint=request.token_endpoint,
                client_id=request.client_id,
                redirect_uri=request.redirect_uri,
                client_secret_secret_name=request.client_secret_secret_name,
                scopes=request.scopes,
                custom_callback_path=request.custom_callback_path,
                custom_callback_port=request.custom_callback_port,
            )
        except KeyError as e:
            # Note: str(KeyError(...)) applies repr() to a single-arg message (a
            # well-known KeyError-specific quirk, not shared by LookupError/ValueError),
            # which re-quotes a message that already contains a quote character —
            # e.args[0] returns the raw message untouched.
            msg = e.args[0] if e.args else str(e)
            detail = msg[5:].strip() if msg.startswith("409:") else msg
            raise HTTPException(status_code=409, detail=detail) from e
        except LookupError as e:
            msg = str(e)
            detail = msg[5:].strip() if msg.startswith("404:") else msg
            raise HTTPException(status_code=404, detail=detail) from e
        await webui.sync_standalone_oauth_callback(
            result["flow_id"], result.get("custom_callback_path"), result.get("custom_callback_port")
        )
        return {"auth_url": result["auth_url"], "flow_id": result["flow_id"]}

    @router.post("/api/secrets/{name}/oauth/reconnect-initiate")
    @handle_exceptions("initiate standalone OAuth reconnect", value_error_status=400)
    async def initiate_standalone_oauth_reconnect(name: str, request: SecretOAuthReconnectInitiateRequest):
        """Start Reconnect for an existing guided-flow oauth2 secret — same shape as
        initiate above, but targets an existing secret and replaces its bundle
        in-place on completion instead of creating a fresh one."""
        _check_custom_callback_path(request.custom_callback_path)
        try:
            result = await webui.service.standalone_oauth_reconnect_initiate(
                secret_name=name,
                authorization_endpoint=request.authorization_endpoint,
                token_endpoint=request.token_endpoint,
                client_id=request.client_id,
                redirect_uri=request.redirect_uri,
                client_secret_secret_name=request.client_secret_secret_name,
                scopes=request.scopes,
                custom_callback_path=request.custom_callback_path,
                custom_callback_port=request.custom_callback_port,
            )
        except LookupError as e:
            msg = str(e)
            detail = msg[5:].strip() if msg.startswith("404:") else msg
            raise HTTPException(status_code=404, detail=detail) from e
        await webui.sync_standalone_oauth_callback(
            result["flow_id"], result.get("custom_callback_path"), result.get("custom_callback_port")
        )
        return {"auth_url": result["auth_url"], "flow_id": result["flow_id"]}

    @router.post("/api/secrets/oauth/{flow_id}/cancel")
    @handle_exceptions("cancel standalone OAuth flow")
    async def cancel_standalone_oauth(flow_id: str):
        """Cancel a pending standalone OAuth flow (user closed the panel/popup before
        completing it) — pops the pending flow and tears down any transient callback
        listener/route eagerly rather than waiting for TTL expiry."""
        cancelled = await webui.service.standalone_oauth_cancel(flow_id)
        await webui.remove_standalone_oauth_callback(flow_id)
        return {"cancelled": cancelled}

    # ==================== RESOLVE ENDPOINT (per-session Bearer token) ====================

    @router.get("/api/sessions/{session_id}/secrets/resolve")
    @handle_exceptions("resolve session secrets")
    async def resolve_session_secrets(
        session_id: str = Depends(session_token_auth),
    ):
        """Return resolved secrets for a session, including plaintext values.

        Authentication: Authorization: Bearer {session_token} (per-session token,
        NOT the global operator token). The global AuthMiddleware is bypassed for
        this path via the EXEMPT_PREFIXES configuration.

        Returns assigned_secrets PLUS transitive sibling records needed for OAuth2 refresh.
        Each record includes a `placeholder` field from session.secret_placeholders.
        """
        return await webui.service.resolve_secrets_for_session(session_id)

    # ==================== SESSION-SCOPED PATCH (proxy write-back) ====================

    @router.patch("/api/sessions/{session_id}/secrets/{name}")
    @handle_exceptions("update session secret", value_error_status=400)
    async def update_session_secret(
        name: str,
        request: SecretUpdateRequest,
        session_id: str = Depends(session_token_auth),
    ):
        """Update a secret value via per-session Bearer token (proxy write-back).

        Scoped to secrets in session.assigned_secrets plus transitive sibling records.
        Used by the proxy sidecar to persist refreshed OAuth2 token values.
        """
        try:
            result = await webui.service.update_secret_for_session(
                session_id=session_id,
                secret_name=name,
                secret_type=request.type,
                target_hosts=request.target_hosts,
                value=request.value,
                inject_env=request.inject_env,
                inject_file=request.inject_file,
                scrub=request.scrub,
                username=request.username,
                injection=request.injection,
                refresh=request.refresh,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Session or secret not found")
        return result

    # ==================== SESSION EVENT EMIT (proxy → UI) ====================

    @router.post("/api/sessions/{session_id}/events", status_code=202)
    @handle_exceptions("emit session event")
    async def emit_session_event(
        request: Request,
        session_id: str = Depends(session_token_auth),
    ):
        """Emit an event to the session's event queue from the proxy sidecar.

        Authentication: Authorization: Bearer {session_token} (per-session token).
        Used by the proxy to surface secret_refresh_failed and similar events
        as session-log entries visible in the WebUI.
        """
        body = await request.json()
        event_type = body.get("type", "proxy_event")
        event_data = body.get("data", {})
        if session_id in webui.session_queues:
            webui.session_queues[session_id].append({
                "type": event_type,
                "data": event_data,
                "timestamp": datetime.now(UTC).isoformat(),
            })
        return {"queued": True}

    return router
