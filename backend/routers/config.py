"""Config endpoints: /api/config — Backend-owned sections only (issue #498).

Backend owns everything in AppConfig except networking/backend_connection
(Frontend-owned — see src/routers/config.py's merged-read). This endpoint
never reads or writes those two sections; Frontend's own /api/config splits
incoming writes and merges this response with its local networking read.
"""

from fastapi import APIRouter, Request

from shared.exception_handlers import handle_exceptions


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/config")
    @handle_exceptions("get config")
    async def get_config():
        """Return Backend-owned application config sections (excludes networking)."""
        from ..config_manager import default_pricing_rates, load_config
        config = load_config(webui.config_file) if webui.config_file else load_config()
        result = config.to_dict()
        result.pop("networking", None)
        result["pricing_defaults"] = {
            model_id: rates.to_dict()
            for model_id, rates in default_pricing_rates().items()
        }
        return {"config": result}

    @router.put("/api/config")
    @handle_exceptions("update config", value_error_status=400)
    async def update_config(request: Request):
        """Update Backend-owned application config sections with side effects."""
        from ..config_manager import load_config, save_config
        body = await request.json()
        config = load_config(webui.config_file) if webui.config_file else load_config()
        old_sync = config.features.skill_sync_enabled

        # Merge features section
        if "features" in body:
            features = body["features"]
            if "skill_sync_enabled" in features:
                config.features.skill_sync_enabled = features["skill_sync_enabled"]
            if "max_peek_cards" in features:
                val = features["max_peek_cards"]
                if not isinstance(val, int) or val < 1:
                    raise ValueError("max_peek_cards must be a positive integer")
                config.features.max_peek_cards = val
            if "max_subagents_per_session" in features:
                val = features["max_subagents_per_session"]
                if not isinstance(val, int) or not (1 <= val <= 200):
                    raise ValueError("max_subagents_per_session must be an integer between 1 and 200")
                config.features.max_subagents_per_session = val
            if "forward_subagent_text" in features:
                val = features["forward_subagent_text"]
                if not isinstance(val, bool):
                    raise ValueError("forward_subagent_text must be a boolean")
                config.features.forward_subagent_text = val
            if "allow_background_agent" in features:
                val = features["allow_background_agent"]
                if not isinstance(val, bool):
                    raise ValueError("allow_background_agent must be a boolean")
                config.features.allow_background_agent = val
            if "resume_batch_size" in features:
                val = features["resume_batch_size"]
                if not isinstance(val, int) or val < 1:
                    raise ValueError("resume_batch_size must be a positive integer")
                config.features.resume_batch_size = val
            if "resume_batch_delay_seconds" in features:
                val = features["resume_batch_delay_seconds"]
                if not isinstance(val, int) or val < 0:
                    raise ValueError("resume_batch_delay_seconds must be a non-negative integer")
                config.features.resume_batch_delay_seconds = val
            if "enable_experimental_nav_header" in features:
                val = features["enable_experimental_nav_header"]
                if not isinstance(val, bool):
                    raise ValueError("enable_experimental_nav_header must be a boolean")
                config.features.enable_experimental_nav_header = val

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

        # Merge pricing section (issue #1125)
        if "pricing" in body:
            from ..config_manager import ModelRates
            pricing_body = body["pricing"]
            if "default_model" in pricing_body:
                config.pricing.default_model = str(pricing_body["default_model"])
            if "rates" in pricing_body:
                raw_rates = pricing_body["rates"]
                if not isinstance(raw_rates, dict):
                    raise ValueError("pricing.rates must be an object")
                for model_id, rate_data in raw_rates.items():
                    if not isinstance(rate_data, dict):
                        raise ValueError(f"pricing.rates.{model_id} must be an object")
                    for key in ("input", "output", "cache_write", "cache_read"):
                        val = rate_data.get(key)
                        if val is not None and (not isinstance(val, (int, float)) or float(val) < 0):
                            raise ValueError(
                                f"pricing.rates.{model_id}.{key} must be a non-negative number"
                            )
                    config.pricing.rates[model_id] = ModelRates.from_dict(rate_data)
            if "removed_models" in pricing_body:
                removed = pricing_body["removed_models"]
                if not isinstance(removed, list) or not all(isinstance(m, str) for m in removed):
                    raise ValueError("pricing.removed_models must be a list of strings")
                for model_id in removed:
                    if model_id == config.pricing.default_model:
                        raise ValueError(
                            f"Cannot remove '{model_id}' because it is the default_model"
                        )
                    config.pricing.rates.pop(model_id, None)

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

        from ..config_manager import default_pricing_rates
        result = config.to_dict()
        result.pop("networking", None)
        result["pricing_defaults"] = {
            model_id: rates.to_dict()
            for model_id, rates in default_pricing_rates().items()
        }
        return {"config": result}

    return router
