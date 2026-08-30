"""
Per-domain APIRouter modules for ClaudeWebUI.

Each router module exposes build_router(webui) -> APIRouter.
register_all() wires every router into the FastAPI app.

Mount exclusivity (issue #498): a "frontend" Hub (default) registers only /api/* +
the SPA; a "headless" REMOTE registers only /api/backend/* behind bearer auth and
serves no UI — see web_server.py's static mount and ClaudeWebUI.backend_mode. The
two mounts are never both registered on the same app.

Session-domain routers (session_runtime, sessions, poll) use paths relative to
their mount (e.g. "/sessions", not "/api/sessions") specifically so the SAME router
instance shape can be registered under either prefix — this is what makes REMOTE's
/api/backend/sessions/... "the same routes, different prefix" rather than a
hand-written mirror. Routers not yet converted stay /api-absolute and frontend-only
until their own batch (2-5) converts them the same way.
"""

from fastapi import Depends, FastAPI

from . import (
    analytics,
    archives,
    audit,
    backend_auth,
    config,
    core,
    diff,
    docker_status,
    edit_history,
    files,
    filesystem,
    fleet,
    legion,
    mcp,
    permissions,
    poll,
    profiles,
    projects,
    provider_catalog,
    proxy,
    queue,
    schedules,
    secrets,
    session_routing,
    session_runtime,
    sessions,
    skills,
    system,
    templates,
)

# Routers already converted to mount-relative paths — mirrored under /api/backend
# in headless mode. Grows batch by batch as Batches 2-5 convert their own routers.
_RELAY_ELIGIBLE_MODULES = (
    poll, session_runtime, sessions, diff, edit_history, archives,
    projects, legion, fleet, files, filesystem, permissions, proxy, docker_status,
    queue, schedules, mcp, audit,
)


def register_all(app: FastAPI, webui) -> None:
    """Register all domain routers with the FastAPI app."""
    if getattr(webui, "backend_mode", "frontend") == "headless":
        _register_headless(app, webui)
        return
    _register_frontend(app, webui)


def _register_frontend(app: FastAPI, webui) -> None:
    app.include_router(analytics.build_router(webui))
    app.include_router(skills.build_router(webui))
    app.include_router(config.build_router(webui))
    app.include_router(core.build_router(webui))
    app.include_router(provider_catalog.build_router(webui))
    app.include_router(secrets.build_router(webui))
    app.include_router(profiles.build_router(webui))
    app.include_router(templates.build_router(webui))
    app.include_router(system.build_router(webui))
    app.include_router(session_routing.build_router(webui))
    for mod in _RELAY_ELIGIBLE_MODULES:
        app.include_router(mod.build_router(webui), prefix="/api")


def _register_headless(app: FastAPI, webui) -> None:
    """Headless mount: /api/backend/* only, bearer-gated, no UI, no /api/*."""
    auth_dep = Depends(backend_auth.build_backend_bearer_auth(webui))
    app.include_router(
        backend_auth.build_router(webui), prefix="/api/backend", dependencies=[auth_dep]
    )
    for mod in _RELAY_ELIGIBLE_MODULES:
        app.include_router(mod.build_router(webui), prefix="/api/backend", dependencies=[auth_dep])
