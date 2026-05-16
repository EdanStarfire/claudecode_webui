"""Provider catalog admin endpoints: /api/provider-catalog/*

Extra top-level fields on PUT are silently dropped on round-trip; semantic
validation is handled by ProviderCatalogManager._validate_entry.
"""

from fastapi import APIRouter, HTTPException

from ..exception_handlers import handle_exceptions
from ._models import ProviderCatalogEntryRequest


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/provider-catalog")
    @handle_exceptions("list provider catalog entries")
    async def list_entries():
        entries = await webui.provider_catalog_manager.list_entries()
        cfg = await webui.app_config_manager.get_config()
        return {
            "entries": entries,
            "pending_changes": cfg.provider_catalog.pending_changes,
        }

    @router.post("/api/provider-catalog", status_code=201)
    @handle_exceptions("create provider catalog entry", value_error_status=400)
    async def create_entry(request: ProviderCatalogEntryRequest):
        entry = await webui.provider_catalog_manager.create_entry(request.model_dump())
        return {"entry": entry}

    @router.put("/api/provider-catalog/{entry_id}")
    @handle_exceptions("update provider catalog entry", value_error_status=400)
    async def update_entry(entry_id: str, request: ProviderCatalogEntryRequest):
        body = request.model_dump()
        body["id"] = entry_id  # path wins over body for the id field
        try:
            entry = await webui.provider_catalog_manager.update_entry(entry_id, body)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"entry": entry}

    @router.delete("/api/provider-catalog/{entry_id}")
    @handle_exceptions("delete provider catalog entry")
    async def delete_entry(entry_id: str):
        try:
            await webui.provider_catalog_manager.delete_entry(entry_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"deleted": True}

    @router.post("/api/provider-catalog/restart")
    @handle_exceptions("restart litellm proxy")
    async def restart_proxy():
        # pending_changes is only cleared on success; a rebuild failure leaves it
        # set so the admin can see the error via /status and retry.
        await webui.litellm_proxy_manager.rebuild()
        await webui.provider_catalog_manager.clear_pending_changes()
        return _status_payload(pending_changes=False)

    @router.get("/api/provider-catalog/status")
    @handle_exceptions("get provider catalog status")
    async def get_status():
        cfg = await webui.app_config_manager.get_config()
        return _status_payload(pending_changes=cfg.provider_catalog.pending_changes)

    def _status_payload(*, pending_changes: bool) -> dict:
        mgr = webui.litellm_proxy_manager
        return {
            "running": mgr.is_running,
            "port": mgr.port,
            "pending_changes": pending_changes,
            "model_count": mgr.model_count,
            "last_restart": mgr.last_restart.isoformat() if mgr.last_restart else None,
            "last_error": mgr.last_error,
        }

    return router
