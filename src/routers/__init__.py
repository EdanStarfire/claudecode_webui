"""
Per-domain APIRouter modules for ClaudeWebUI.

Each router module exposes build_router(webui) -> APIRouter.
register_all() wires every router into the FastAPI app.
"""

from fastapi import FastAPI

from . import (
    archives,
    config,
    core,
    diff,
    files,
    filesystem,
    fleet,
    legion,
    mcp,
    permissions,
    poll,
    profiles,
    projects,
    proxy,
    queue,
    schedules,
    session_runtime,
    sessions,
    skills,
    system,
    templates,
)


def register_all(app: FastAPI, webui) -> None:
    """Register all domain routers with the FastAPI app."""
    app.include_router(poll.build_router(webui))
    app.include_router(permissions.build_router(webui))
    app.include_router(filesystem.build_router(webui))
    app.include_router(skills.build_router(webui))
    app.include_router(config.build_router(webui))
    app.include_router(fleet.build_router(webui))
    app.include_router(core.build_router(webui))
    app.include_router(proxy.build_router(webui))
    app.include_router(profiles.build_router(webui))
    app.include_router(archives.build_router(webui))
    app.include_router(templates.build_router(webui))
    app.include_router(queue.build_router(webui))
    app.include_router(legion.build_router(webui))
    app.include_router(mcp.build_router(webui))
    app.include_router(schedules.build_router(webui))
    app.include_router(system.build_router(webui))
    app.include_router(diff.build_router(webui))
    app.include_router(files.build_router(webui))
    app.include_router(projects.build_router(webui))
    app.include_router(session_runtime.build_router(webui))
    app.include_router(sessions.build_router(webui))
