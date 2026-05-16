"""
Session routing endpoint: GET /api/sessions/{id}/routing

Returns per-session LiteLLM hostname rewrites and virtual key for Docker proxy
sidecars (Phase 3 of issue #1427).

Auth: same per-session Bearer token as /secrets/resolve — reused from secrets.py.
"""

from fastapi import APIRouter, Depends

from ..exception_handlers import handle_exceptions
from .secrets import _build_session_token_auth

_EMPTY_ROUTING = {"hostname_rewrites": {}, "virtual_key": None}


def build_router(webui) -> APIRouter:
    router = APIRouter()
    session_token_auth = _build_session_token_auth(webui)

    @router.get("/api/sessions/{session_id}/routing")
    @handle_exceptions("get session routing")
    async def get_session_routing(
        session_id: str = Depends(session_token_auth),
    ):
        """Return LiteLLM routing config for the proxy sidecar.

        Returns 200 with empty rewrites when no catalog is selected — the sidecar
        always receives the same shape regardless of whether routing is active.

        Authentication: Authorization: Bearer {session_token} (per-session token).
        Global AuthMiddleware is bypassed via the /routing EXEMPT_SUFFIXES entry.
        """
        proxy_mgr = webui.litellm_proxy_manager
        routing = proxy_mgr.get_session_routing(session_id)
        if routing is None:
            return _EMPTY_ROUTING

        return {
            "hostname_rewrites": proxy_mgr.build_hostname_rewrites(),
            "virtual_key": routing["virtual_key"],
        }

    return router
