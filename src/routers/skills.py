"""Skills endpoints: /api/skills/*"""

from fastapi import APIRouter, HTTPException

from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.post("/api/skills/sync")
    @handle_exceptions("sync skills")
    async def sync_skills():
        """Manually trigger skill sync."""
        from ..config_manager import load_config
        config = load_config(webui.config_file) if webui.config_file else load_config()
        if not config.features.skill_sync_enabled:
            raise HTTPException(status_code=409, detail="Skill syncing is disabled")
        result = await webui.skill_manager.sync()
        return {"status": "synced", **result}

    @router.get("/api/skills/status")
    @handle_exceptions("get skills status")
    async def get_skills_status():
        """Get skill sync status."""
        from ..config_manager import load_config
        config = load_config(webui.config_file) if webui.config_file else load_config()
        return {
            "sync_enabled": config.features.skill_sync_enabled,
            "last_sync_time": webui.skill_manager.last_sync_time,
        }

    return router
