"""
Secrets endpoints: /api/secrets/*, /api/sessions/{id}/secrets/resolve

Issue #827: Host-level secrets storage via keyring (storage + API layer).

The resolve endpoint uses a per-session Bearer token (not the global auth token)
so the proxy sidecar can fetch its assigned secrets without knowing the global
operator token.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from ..exception_handlers import handle_exceptions
from ._models import SecretCreateRequest, SecretUpdateRequest


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
        return await webui.service.create_secret(
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
        return result

    @router.delete("/api/secrets/{name}")
    @handle_exceptions("delete secret")
    async def delete_secret(name: str):
        """Delete a secret by name (removes metadata + keyring entry)."""
        deleted = await webui.service.delete_secret(name)
        if not deleted:
            raise HTTPException(status_code=404, detail="Secret not found")
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
