"""
Per-domain APIRouter modules for the Frontend API shell.

Each router module exposes build_router(webui) -> APIRouter.
register_all() wires every router into the FastAPI app.

Issue #498: only core.py/poll.py/config.py remain Frontend-side with real local
logic — everything else moved to backend/routers/. relay.py's generic catch-all
is registered LAST so those three routers' specific routes always take priority;
every other /api/* path falls through to the relay and is forwarded to Backend.
"""

from fastapi import FastAPI

from . import (
    config,
    core,
    poll,
    relay,
)


def register_all(app: FastAPI, webui) -> None:
    """Register all Frontend-side routers with the FastAPI app."""
    app.include_router(poll.build_router(webui))
    app.include_router(config.build_router(webui))
    app.include_router(core.build_router(webui))
    app.include_router(relay.build_router(webui))
