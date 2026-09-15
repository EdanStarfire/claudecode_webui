"""Hook config endpoints: /api/hook-configs (issue #1629)"""

from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions

from ._models import HookConfigCreateRequest, HookConfigUpdateRequest


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/hook-configs")
    @handle_exceptions("list hook configs")
    async def list_hook_configs(limit: int = 100, offset: int = 0):
        """List global hook configurations, paginated"""
        return await webui.service.list_hook_configs(limit=limit, offset=offset)

    @router.post("/api/hook-configs")
    @handle_exceptions("create hook config", value_error_status=400)
    async def create_hook_config(request: HookConfigCreateRequest):
        """Create a new global hook configuration"""
        return await webui.service.create_hook_config(
            name=request.name,
            enabled=request.enabled,
            hooks=[h.model_dump() for h in request.hooks],
        )

    @router.get("/api/hook-configs/{config_id}")
    @handle_exceptions("get hook config")
    async def get_hook_config(config_id: str):
        """Get a specific hook configuration"""
        config = await webui.service.get_hook_config(config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Hook config not found")
        return config

    @router.put("/api/hook-configs/{config_id}")
    @handle_exceptions("update hook config", value_error_status=400)
    async def update_hook_config(config_id: str, request: HookConfigUpdateRequest):
        """Update an existing hook configuration"""
        hooks = [h.model_dump() for h in request.hooks] if request.hooks is not None else None
        return await webui.service.update_hook_config(
            config_id,
            name=request.name,
            enabled=request.enabled,
            hooks=hooks,
        )

    @router.delete("/api/hook-configs/{config_id}")
    @handle_exceptions("delete hook config")
    async def delete_hook_config(config_id: str):
        """Delete a hook configuration"""
        success = await webui.service.delete_hook_config(config_id)
        if not success:
            raise HTTPException(status_code=404, detail="Hook config not found")
        return {"deleted": True}

    return router
