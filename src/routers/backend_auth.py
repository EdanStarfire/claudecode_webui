"""
Bearer-token auth for the headless `/api/backend/*` mount (issue #498).

A headless-mode instance (REMOTE, issue #499) never mounts `/api/*` or serves the
frontend UI — the only surface it exposes is `/api/backend/*`, gated by a single
static bearer token (`backend_auth_token`) instead of the browser-facing
`AuthMiddleware`. Modeled directly on `routers/secrets.py`'s
`_build_session_token_auth` (per-session Bearer token pattern already in this
codebase), just scoped to one process-wide token instead of one per session.
"""

import secrets

from fastapi import APIRouter, HTTPException, Request


def build_backend_bearer_auth(webui):
    """Return a FastAPI dependency that validates `Authorization: Bearer {token}`
    against `webui.backend_auth_token`."""

    async def backend_bearer_auth(request: Request) -> None:
        expected = getattr(webui, "backend_auth_token", None)
        if not expected:
            # Misconfiguration: headless mode with no token configured — refuse
            # everything rather than silently running unauthenticated.
            raise HTTPException(status_code=401, detail="Backend auth token not configured")

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        token = auth_header[7:].strip()
        # Constant-time compare — this token guards a network-reachable REMOTE mount
        # (issue #499), so a naive `!=` would leak a timing side-channel.
        if not secrets.compare_digest(token, expected):
            raise HTTPException(status_code=401, detail="Invalid backend auth token")

    return backend_bearer_auth


def build_router(webui) -> APIRouter:
    """Minimal always-present route under the headless mount, gated by the same
    bearer dependency every other `/api/backend/*` router uses — lets ops/tests
    verify the auth mechanism itself independent of which business routers have
    been mirrored under this mount so far (mirroring lands incrementally, batch
    by batch — see routers/__init__.py)."""
    router = APIRouter()

    @router.get("/health")
    async def backend_health():
        return {"status": "ok"}

    return router
