"""Relay endpoints: /api/relays/*, /api/sessions/{id}/relay and /api/sessions/{id}/relays/{rid}/*
(issue #2103, #2346).

Responses never contain token values. Credentials are references to existing vault secrets:
the record exposes the referenced secret NAMES plus has_* booleans (false when the secret is gone).
"""

import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from shared.exception_handlers import handle_exceptions

from ..relay.adapter import RelayError
from ..relay.adapters import get_spec
from ..relay.directory import DEFAULT_PAGE, DirectoryLookupError, entry_label
from ..relay.handles import HandleConflictError
from ..relay.inbound.constants import MONITOR_KINDS
from ..relay.safety import safe_error_code
from ..relay.service import RelayNotAttachedError, SessionNotFoundError
from ..relay.slack_scopes import build_manifest_response
from ..relay_manager import (
    OAUTH_HTTPS_LIMITATION,
    RelayConflictError,
    RelayNotFoundError,
    RelayTokenError,
)
from ._models import (
    RelayCreateRequest,
    RelayHandleRequest,
    RelayMonitorPatchRequest,
    RelayOAuthInitiateRequest,
    RelayPolicyRequest,
    RelaySecretsRequest,
    RelayUpdateRequest,
)

MAX_LABEL_IDS = 50
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
        return manager().public_dict(record)

    # ---- Relay asset CRUD ----

    @router.get("/api/relays")
    @handle_exceptions("list relays")
    async def list_relays():
        return {"relays": [manager().public_dict(r) for r in await manager().list_relays()]}

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
        return manager().public_dict(record)

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
                inbound_enabled=request.inbound_enabled,
            )
        except RelayNotFoundError as e:
            raise HTTPException(status_code=404, detail="Relay not found") from e
        return manager().public_dict(record)

    @router.get("/api/relays/{relay_id}/inbound/status")
    @handle_exceptions("relay inbound status")
    async def inbound_status(relay_id: str):
        """Live inbound connection status (in memory; sanitised detail code only)."""
        record = await manager().get_relay(relay_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Relay not found")
        status = webui.service.relay_inbound.status_for(relay_id)
        return {
            "relay_id": relay_id, "enabled": record.inbound_enabled,
            "status": status["status"], "detail": status["detail"],
            "connected_at": status.get("connected_at"),
        }

    @router.delete("/api/relays/{relay_id}")
    @handle_exceptions("delete relay")
    async def delete_relay(relay_id: str):
        if not await manager().delete_relay(relay_id):
            raise HTTPException(status_code=404, detail="Relay not found")
        await service().refresh_all_summaries()
        return {"deleted": True}

    # ---- Credentials ----

    @router.put("/api/relays/{relay_id}/secrets")
    @handle_exceptions("set relay credential secrets", value_error_status=400)
    async def put_secrets(relay_id: str, request: RelaySecretsRequest):
        """Reference existing vault secrets as the relay credentials (names only)."""

        try:
            record = await manager().set_secret_refs(
                relay_id,
                user_token_secret=request.user_token_secret,
                app_token_secret=request.app_token_secret,
            )
        except RelayNotFoundError as e:
            raise HTTPException(status_code=404, detail="Relay not found") from e
        except RelayTokenError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return manager().public_dict(record)

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

    @router.get("/api/relays/{relay_id}/manifest")
    @handle_exceptions("get relay manifest")
    async def get_manifest(relay_id: str, include_inbound: bool = True):
        if await manager().get_relay(relay_id) is None:
            raise HTTPException(status_code=404, detail="Relay not found")
        return build_manifest_response(include_inbound)

    @router.get("/api/relays/{relay_id}/policy-model")
    @handle_exceptions("get relay policy model")
    async def get_policy_model(relay_id: str):
        """The adapter-owned policy model (values, target format, actions) for a relay."""
        record = await manager().get_relay(relay_id)
        spec = get_spec(record.adapter_type) if record else None
        if spec is None:
            raise HTTPException(status_code=404, detail="Relay not found")
        return {"relay_id": relay_id, "adapter_type": spec.adapter_type,
                **spec.policy_model.describe()}

    # ---- Conversations (settings UI picker and policy-row labels) ----

    @router.get("/api/relays/{relay_id}/conversations")
    @handle_exceptions("get relay conversations")
    async def get_conversations(
        relay_id: str,
        q: str | None = None,
        kinds: str | None = None,
        limit: int = DEFAULT_PAGE,
        cursor: str | None = None,
        ids: str | None = None,
    ):
        """Searchable conversations/people for the policy editor, or labels for ``ids=``.

        Session-agnostic: no policies are applied, so blocked and unlisted entries both appear
        (the editor overlays status itself). Slack is only touched on a snapshot refresh.
        """
        if await manager().get_relay(relay_id) is None:
            raise HTTPException(status_code=404, detail="Relay not found")
        gate = webui.service.relay_gate
        ctx = gate.relay_context(relay_id)
        if ctx is None:
            raise HTTPException(
                status_code=409, detail="Relay is not usable (no user token or unsupported adapter)"
            )

        if ids is not None:
            wanted = list(dict.fromkeys(i.strip() for i in ids.split(",") if i.strip()))
            if len(wanted) > MAX_LABEL_IDS:
                raise HTTPException(status_code=400, detail=f"At most {MAX_LABEL_IDS} ids")
            valid = []
            for target in wanted:
                try:
                    valid.append(ctx.spec.policy_model.validate_target(target))
                except ValueError:
                    pass  # a malformed stored target keeps its ID as label; it must not fail the rest
            sem = asyncio.Semaphore(5)

            async def one(target: str) -> tuple[str, str]:
                async with sem:
                    return target, await gate.names.channel_label(
                        ctx.adapter, relay_id, target, ctx.owner_user_id
                    )

            labels = {t: t for t in wanted}
            labels.update(dict(await asyncio.gather(*(one(t) for t in valid))))
            return {"labels": labels}

        kind_list = tuple(k.strip() for k in kinds.split(",") if k.strip()) if kinds else None
        try:
            page = await gate.directory.search(
                ctx, q, kind_list, limit, cursor, {}, with_dm_id=True
            )
        except DirectoryLookupError as e:
            raise HTTPException(
                status_code=400, detail={"code": e.code, "message": e.message}
            ) from e
        except RelayError as e:
            raise HTTPException(
                status_code=502,
                detail={"code": safe_error_code(e.code), "message": "Slack lookup failed"},
            ) from e
        entries = []
        for row in page.entries:
            row = {k: v for k, v in row.items() if k != "policy"}
            row["label"] = entry_label(row, ctx.owner_user_id)
            entries.append(row)
        return {"entries": entries, "next_cursor": page.next_cursor, "truncated": page.truncated}

    # ---- Audit log ----

    @router.get("/api/relays/{relay_id}/audit")
    @handle_exceptions("get relay audit", value_error_status=400)
    async def get_audit(
        relay_id: str,
        session_id: str | None = None,
        channel: str | None = None,
        limit: int = 100,
        before: str | None = None,
    ):
        if await manager().get_relay(relay_id) is None:
            raise HTTPException(status_code=404, detail="Relay not found")
        records = await webui.service.relay_audit.query(
            relay_id, session_id=session_id, channel=channel, limit=limit, before=before
        )
        return {"records": records}

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

    def _policy_errors(e: Exception) -> HTTPException:
        if isinstance(e, SessionNotFoundError):
            return HTTPException(status_code=404, detail="Session not found")
        if isinstance(e, RelayNotFoundError):
            return HTTPException(status_code=404, detail="Relay not found")
        return HTTPException(status_code=409, detail=str(e))

    @router.put("/api/sessions/{session_id}/relays/{relay_id}/policies/{target}")
    @handle_exceptions("set relay policy", value_error_status=422)
    async def put_policy(session_id: str, relay_id: str, target: str, request: RelayPolicyRequest):
        try:
            await service().set_policy(
                session_id, relay_id, target, request.policy, source="editor"
            )
        except (SessionNotFoundError, RelayNotFoundError, RelayNotAttachedError) as e:
            raise _policy_errors(e) from e
        return await service().get_session_relay(session_id)

    @router.delete("/api/sessions/{session_id}/relays/{relay_id}/policies/{target}")
    @handle_exceptions("delete relay policy", value_error_status=422)
    async def delete_policy(session_id: str, relay_id: str, target: str):
        try:
            await service().delete_policy(session_id, relay_id, target, source="editor")
        except (SessionNotFoundError, RelayNotFoundError, RelayNotAttachedError) as e:
            raise _policy_errors(e) from e
        return await service().get_session_relay(session_id)

    # ---- Monitors (inbound 3b-ii): owner list / pause / resume / delete ----

    monitor_states = ("active", "paused", "suspended")

    async def _monitor_service(relay_id: str):
        if await manager().get_relay(relay_id) is None:
            raise HTTPException(status_code=404, detail="Relay not found")
        inbound = webui.service.relay_inbound.service_for(relay_id)
        if inbound is None:
            raise HTTPException(status_code=409, detail="Inbound is not running for this relay")
        return inbound

    async def _require_session_relay(session_id: str, relay_id: str) -> None:
        try:
            await service().require_attached(session_id, relay_id)
        except (SessionNotFoundError, RelayNotFoundError, RelayNotAttachedError) as e:
            raise _policy_errors(e) from e

    def _check_filters(status: str | None, kind: str | None) -> None:
        if status is not None and status not in monitor_states:
            raise HTTPException(status_code=422, detail="status must be active, paused or suspended")
        if kind is not None and kind not in MONITOR_KINDS:
            raise HTTPException(status_code=422, detail="unknown monitor kind")

    async def _list_monitors(
        relay_id: str, session_id: str | None, status: str | None, kind: str | None,
        limit: int, offset: int,
    ) -> dict:
        _check_filters(status, kind)
        inbound = await _monitor_service(relay_id)
        rows, total = await inbound.public_monitors(
            session_id, status=status, kind=kind, limit=limit, offset=max(0, offset)
        )
        return {"relay_id": relay_id, "monitors": rows, "total": total}

    async def _patch_monitor(
        relay_id: str, monitor_id: str, session_id: str | None, request: RelayMonitorPatchRequest
    ) -> dict:
        inbound = await _monitor_service(relay_id)
        rec = inbound.store.get_monitor(monitor_id)
        if rec is None or (session_id is not None and rec.get("session_id") != session_id):
            raise HTTPException(status_code=404, detail="Monitor not found")
        await inbound.set_paused(monitor_id, request.paused, source="owner")
        return await inbound.public_one(monitor_id) or {}

    async def _delete_monitor(relay_id: str, monitor_id: str, session_id: str | None) -> dict:
        inbound = await _monitor_service(relay_id)
        rec = inbound.store.get_monitor(monitor_id)
        if rec is None or (session_id is not None and rec.get("session_id") != session_id):
            raise HTTPException(status_code=404, detail="Monitor not found")
        await inbound.delete_monitor(monitor_id, source="owner")
        return {"deleted": True, "monitor_id": monitor_id}

    @router.get("/api/relays/{relay_id}/monitors")
    @handle_exceptions("list relay monitors")
    async def list_relay_monitors(
        relay_id: str, session_id: str | None = None, status: str | None = None,
        kind: str | None = None, limit: int = 100, offset: int = 0,
    ):
        return await _list_monitors(relay_id, session_id, status, kind, limit, offset)

    @router.get("/api/sessions/{session_id}/relays/{relay_id}/monitors")
    @handle_exceptions("list session monitors")
    async def list_session_monitors(
        session_id: str, relay_id: str, status: str | None = None, kind: str | None = None,
        limit: int = 100, offset: int = 0,
    ):
        await _require_session_relay(session_id, relay_id)
        return await _list_monitors(relay_id, session_id, status, kind, limit, offset)

    @router.patch("/api/relays/{relay_id}/monitors/{monitor_id}")
    @handle_exceptions("update relay monitor")
    async def patch_relay_monitor(relay_id: str, monitor_id: str, request: RelayMonitorPatchRequest):
        return await _patch_monitor(relay_id, monitor_id, None, request)

    @router.patch("/api/sessions/{session_id}/relays/{relay_id}/monitors/{monitor_id}")
    @handle_exceptions("update session monitor")
    async def patch_session_monitor(
        session_id: str, relay_id: str, monitor_id: str, request: RelayMonitorPatchRequest
    ):
        await _require_session_relay(session_id, relay_id)
        return await _patch_monitor(relay_id, monitor_id, session_id, request)

    @router.delete("/api/relays/{relay_id}/monitors/{monitor_id}")
    @handle_exceptions("delete relay monitor")
    async def delete_relay_monitor(relay_id: str, monitor_id: str):
        return await _delete_monitor(relay_id, monitor_id, None)

    @router.delete("/api/sessions/{session_id}/relays/{relay_id}/monitors/{monitor_id}")
    @handle_exceptions("delete session monitor")
    async def delete_session_monitor(session_id: str, relay_id: str, monitor_id: str):
        await _require_session_relay(session_id, relay_id)
        return await _delete_monitor(relay_id, monitor_id, session_id)

    return router
