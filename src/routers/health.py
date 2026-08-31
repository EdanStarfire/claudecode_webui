"""Liveness + readiness probes: /health, /health/ready.

Mounted unprefixed and unauthenticated on both the frontend and headless mounts
(issue #499) — a deliberate, narrow exception to headless mode's "only
/api/backend/* is exposed" rule, since orchestrator probes can't carry a bearer
token. /health (liveness) was previously owned by core.py; moved here so it can
also be registered in headless mode without pulling in core.py's other,
frontend-only routes.
"""

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    @handle_exceptions("health check")
    async def health_check():
        """Liveness check: 200 if the process is alive, regardless of readiness."""
        return {"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}

    @router.get("/health/ready")
    @handle_exceptions("readiness check")
    async def readiness_check():
        """Readiness check: 200 once startup has finished, 503 otherwise/during shutdown-drain."""
        if not getattr(webui, "_ready", False):
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "timestamp": datetime.now(UTC).isoformat()},
            )
        return {"status": "ready", "timestamp": datetime.now(UTC).isoformat()}

    return router
