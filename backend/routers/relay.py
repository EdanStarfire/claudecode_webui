"""Relay endpoints: /api/relays/* and /api/sessions/{id}/relay/* (issue #2103).

Responses never contain token values or vault secret names (only has_* booleans).
"""

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions

from ..relay.handles import HandleConflictError
from ..relay.service import SessionNotFoundError
from ..relay_manager import (
    OAUTH_HTTPS_LIMITATION,
    RelayConflictError,
    RelayNotFoundError,
    RelayTokenError,
)
from ._models import (
    RelayCreateRequest,
    RelayHandleRequest,
    RelayOAuthInitiateRequest,
    RelayPolicyRequest,
    RelayTokensRequest,
    RelayUpdateRequest,
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _redirect_uri_allowed(uri: str) -> bool:
    parsed = urlparse(uri)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return parsed.scheme == "http" and parsed.hostname in _LOCAL_HOSTS


def build_router(webui) -> APIRouter:
    router = APIRouter()

    def manager():
        return webui.service.relay_manager

    def service():
        return webui.service.relay_service

    async def _public(relay_id: str) -> dict:
        record = await manager().get_relay(relay_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Relay not found")
        return record.to_public_dict()

    # ---- Relay asset CRUD ----

    @router.get("/api/relays")
    @handle_exceptions("list relays")
    async def list_relays():
        return {"relays": [r.to_public_dict() for r in await manager().list_relays()]}

    @router.post("/api/relays", status_code=201)
    @handle_exceptions("create relay", value_error_status=400)
    async def create_relay(request: RelayCreateRequest):
        try:
            record = await manager().create_relay(
                name=request.name,
                adapter_type=request.adapter_type,
                oauth_client_id=request.oauth_client_id,
                oauth_client_secret=request.oauth_client_secret,
            )
        except RelayConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return record.to_public_dict()

    @router.get("/api/relays/{relay_id}")
    @handle_exceptions("get relay")
    async def get_relay(relay_id: str):
        return await _public(relay_id)

    @router.put("/api/relays/{relay_id}")
    @handle_exceptions("update relay", value_error_status=400)
    async def update_relay(relay_id: str, request: RelayUpdateRequest):
        try:
            record = await manager().update_relay(
                relay_id,
                name=request.name,
                oauth_client_id=request.oauth_client_id,
                oauth_client_secret=request.oauth_client_secret,
            )
        except RelayNotFoundError as e:
            raise HTTPException(status_code=404, detail="Relay not found") from e
        return record.to_public_dict()

    @router.delete("/api/relays/{relay_id}")
    @handle_exceptions("delete relay")
    async def delete_relay(relay_id: str):
        if not await manager().delete_relay(relay_id):
            raise HTTPException(status_code=404, detail="Relay not found")
        return {"deleted": True}

    # ---- Credentials ----

    @router.put("/api/relays/{relay_id}/tokens")
    @handle_exceptions("store relay tokens", value_error_status=400)
    async def put_tokens(relay_id: str, request: RelayTokensRequest):
        try:
            record = await manager().set_tokens(
                relay_id, user_token=request.user_token, app_token=request.app_token
            )
        except RelayNotFoundError as e:
            raise HTTPException(status_code=404, detail="Relay not found") from e
        except RelayTokenError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return record.to_public_dict()

    @router.post("/api/relays/{relay_id}/test")
    @handle_exceptions("test relay")
    async def test_relay(relay_id: str):
        try:
            return await manager().test_relay(relay_id)
        except RelayNotFoundError as e:
            raise HTTPException(status_code=404, detail="Relay not found") from e

    @router.post("/api/relays/{relay_id}/oauth/initiate")
    @handle_exceptions("initiate relay oauth", value_error_status=400)
    async def oauth_initiate(relay_id: str, request: RelayOAuthInitiateRequest):
        if not _redirect_uri_allowed(request.redirect_uri):
            raise HTTPException(status_code=400, detail=OAUTH_HTTPS_LIMITATION)
        try:
            return await manager().oauth_initiate(relay_id, request.redirect_uri)
        except RelayNotFoundError as e:
            raise HTTPException(status_code=404, detail="Relay not found") from e

    @router.get("/api/relays/{relay_id}/oauth/status")
    @handle_exceptions("relay oauth status")
    async def oauth_status(relay_id: str, state: str):
        if await manager().get_relay(relay_id) is None:
            raise HTTPException(status_code=404, detail="Relay not found")
        return manager().oauth_status(state)

    # ---- Per-session attachment ----

    @router.get("/api/sessions/{session_id}/relay")
    @handle_exceptions("get session relay")
    async def get_session_relay(session_id: str):
        try:
            return await service().get_session_relay(session_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail="Session not found") from e

    @router.put("/api/sessions/{session_id}/relay/handle")
    @handle_exceptions("set relay handle", value_error_status=422)
    async def put_handle(session_id: str, request: RelayHandleRequest):
        try:
            handle = await service().set_handle(session_id, request.handle)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail="Session not found") from e
        except HandleConflictError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {"relay_handle": handle}

    @router.put("/api/sessions/{session_id}/relay/policies/{channel_id}")
    @handle_exceptions("set relay policy", value_error_status=422)
    async def put_policy(session_id: str, channel_id: str, request: RelayPolicyRequest):
        try:
            await service().set_policy(session_id, channel_id, request.policy)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail="Session not found") from e
        return await service().get_session_relay(session_id)

    @router.delete("/api/sessions/{session_id}/relay/policies/{channel_id}")
    @handle_exceptions("delete relay policy", value_error_status=422)
    async def delete_policy(session_id: str, channel_id: str):
        try:
            await service().delete_policy(session_id, channel_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail="Session not found") from e
        return await service().get_session_relay(session_id)

    return router
