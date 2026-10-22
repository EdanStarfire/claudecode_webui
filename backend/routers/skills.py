"""Skills endpoints: /api/skills/*"""

from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.post("/api/skills/sync")
    @handle_exceptions("sync skills")
    async def sync_skills():
        """Manually trigger skill sync."""
        config = webui.tenant_context.load_config()
        if not config.features.skill_sync_enabled:
            raise HTTPException(status_code=409, detail="Skill syncing is disabled")
        result = await webui.service.sync_skills()
        return {"status": "synced", **result}

    @router.get("/api/skills/status")
    @handle_exceptions("get skills status")
    async def get_skills_status():
        """Get skill sync status."""
        config = webui.tenant_context.load_config()
        return {
            "sync_enabled": config.features.skill_sync_enabled,
            "last_sync_time": webui.service.get_skill_last_sync_time(),
        }

    return router
