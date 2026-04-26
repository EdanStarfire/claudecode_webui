"""
Secrets endpoints: /api/secrets/*, /api/sessions/{id}/secrets/resolve

Issue #827: Host-level secrets storage via keyring (storage + API layer).

The resolve endpoint uses a per-session Bearer token (not the global auth token)
so the proxy sidecar can fetch its assigned secrets without knowing the global
operator token.
"""

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

        Returns the union of assigned_secrets from session + template + profile chain.
        """
        return await webui.service.resolve_secrets_for_session(session_id)

    return router
