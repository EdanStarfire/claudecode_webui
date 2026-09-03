"""
Per-domain APIRouter modules for the Frontend API shell.

Each router module exposes build_router(webui) -> APIRouter.
register_all() wires every router into the FastAPI app.

Issue #498: only core.py/poll.py/config.py remain Frontend-side — everything else
moved to backend/routers/. poll.py and config.py still need Phase 2's poll-relay /
split-config rewrite before they'll function against a real Backend; register_all()
already reflects the final Frontend-side router set as of Phase 1.
"""

from fastapi import FastAPI

from . import (
    config,
    core,
    poll,
)


def register_all(app: FastAPI, webui) -> None:
    """Register all Frontend-side routers with the FastAPI app."""
    app.include_router(poll.build_router(webui))
    app.include_router(config.build_router(webui))
    app.include_router(core.build_router(webui))
