"""Config endpoints: /api/config — split ownership, merged read (issue #498).

Frontend owns/writes networking + backend_connection; Backend owns/writes
everything else (features, legion, watchdog, pricing, background-calls, proxy,
secrets). This route merges a local read of Frontend's sections with a relayed
read of Backend's sections into one response, so the browser's Settings modal
doesn't need to know two processes exist. A PUT body may contain a mix of
Frontend-owned and Backend-owned keys in one request; each half is routed to
wherever it's actually written.
"""

from fastapi import APIRouter, Request

from shared.exception_handlers import handle_exceptions

from ..frontend_config import load_frontend_config, save_frontend_config

_FRONTEND_OWNED_KEYS = {"networking", "backend_connection"}


def build_router(webui) -> APIRouter:
    router = APIRouter()

    async def _merged_config() -> dict:
        backend_result = await webui.backend_client.get_json("/api/config")
        merged = backend_result["config"]
        frontend_cfg = (
            load_frontend_config(webui.config_file) if webui.config_file else load_frontend_config()
        )
        merged.update(frontend_cfg.to_dict())
        return merged

    @router.get("/api/config")
    @handle_exceptions("get config")
    async def get_config():
        """Return full application config: local Frontend sections + relayed Backend sections."""
        return {"config": await _merged_config()}

    @router.put("/api/config")
    @handle_exceptions("update config", value_error_status=400)
    async def update_config(request: Request):
        """Split a config update between Frontend's local sections and a relayed Backend write."""
        body = await request.json()
        frontend_body = {k: v for k, v in body.items() if k in _FRONTEND_OWNED_KEYS}
        backend_body = {k: v for k, v in body.items() if k not in _FRONTEND_OWNED_KEYS}

        if frontend_body:
            frontend_cfg = (
                load_frontend_config(webui.config_file) if webui.config_file else load_frontend_config()
            )
            if "networking" in frontend_body:
                net = frontend_body["networking"]
                if "allow_network_binding" in net:
                    frontend_cfg.networking.allow_network_binding = net["allow_network_binding"]
                if "acknowledged_risk" in net:
                    frontend_cfg.networking.acknowledged_risk = net["acknowledged_risk"]
            if "backend_connection" in frontend_body:
                bc = frontend_body["backend_connection"]
                if "remote_backend_url" in bc:
                    frontend_cfg.backend_connection.remote_backend_url = bc["remote_backend_url"]
                if "remote_backend_token" in bc:
                    frontend_cfg.backend_connection.remote_backend_token = bc["remote_backend_token"]
            if webui.config_file:
                save_frontend_config(frontend_cfg, webui.config_file)
            else:
                save_frontend_config(frontend_cfg)

        if backend_body:
            await webui.backend_client.request_json("PUT", "/api/config", json=backend_body)

        return {"config": await _merged_config()}

    return router
