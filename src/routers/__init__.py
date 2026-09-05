"""
Per-domain APIRouter modules for the Frontend API shell.

Each router module exposes build_router(webui) -> APIRouter.
register_all() wires every router into the FastAPI app.

Issue #498: core.py/poll.py/config.py/system.py remain Frontend-side with real
local logic — everything else moved to backend/routers/. relay.py's generic
catch-all is registered LAST so those routers' specific routes always take
priority; every other /api/* path falls through to the relay and is forwarded
to Backend. system.py only intercepts POST /api/system/restart — Backend's
GET /api/system/git-status etc. are still accurate to relay through unchanged.
"""

from fastapi import FastAPI

from . import (
    config,
    core,
    poll,
    relay,
    system,
)


def register_all(app: FastAPI, webui) -> None:
    """Register all Frontend-side routers with the FastAPI app."""
    app.include_router(poll.build_router(webui))
    app.include_router(config.build_router(webui))
    app.include_router(core.build_router(webui))
    app.include_router(system.build_router(webui))
    app.include_router(relay.build_router(webui))
