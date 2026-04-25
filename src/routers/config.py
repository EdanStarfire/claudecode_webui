"""Config endpoints: /api/config"""

from fastapi import APIRouter, Request

from ..exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/config")
    @handle_exceptions("get config")
    async def get_config():
        """Return full application config."""
        from ..config_manager import load_config
        config = load_config(webui.config_file) if webui.config_file else load_config()
        return {"config": config.to_dict()}

    @router.put("/api/config")
    @handle_exceptions("update config")
    async def update_config(request: Request):
        """Update application config with side effects."""
        from ..config_manager import load_config, save_config
        body = await request.json()
        config = load_config(webui.config_file) if webui.config_file else load_config()
        old_sync = config.features.skill_sync_enabled

        # Merge features section
        if "features" in body:
            features = body["features"]
            if "skill_sync_enabled" in features:
                config.features.skill_sync_enabled = features["skill_sync_enabled"]

        # Merge networking section
        if "networking" in body:
            net = body["networking"]
            if "allow_network_binding" in net:
                config.networking.allow_network_binding = net["allow_network_binding"]
            if "acknowledged_risk" in net:
                config.networking.acknowledged_risk = net["acknowledged_risk"]

        # Merge proxy section (issue #1050)
        if "proxy" in body:
            proxy_data = body["proxy"]
            if "proxy_image" in proxy_data:
                config.proxy.proxy_image = str(proxy_data["proxy_image"])

        # Merge legion section (issue #1064)
        if "legion" in body:
            legion_data = body["legion"]
            if "max_concurrent_minions" in legion_data:
                val = legion_data["max_concurrent_minions"]
                if not isinstance(val, int) or val < 1:
                    raise ValueError("max_concurrent_minions must be a positive integer")
                config.legion.max_concurrent_minions = val

        if webui.config_file:
            save_config(config, webui.config_file)
        else:
            save_config(config)

        # Side effects for skill sync toggle
        new_sync = config.features.skill_sync_enabled
        if old_sync and not new_sync:
            await webui.skill_manager.cleanup_symlinks()
        elif not old_sync and new_sync:
            await webui.skill_manager.sync()

        return {"config": config.to_dict()}

    return router
