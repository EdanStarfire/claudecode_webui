"""
Generic Pattern-A relay forwarding helper (issue #498 Batch 2+).

Session-domain routes (Batch 1) dispatch through SessionCoordinator/
SessionBackend directly, since the Hub also needs to keep local session_manager
state in sync for REMOTE sessions. Every other relay-eligible router (diff,
edit_history, archives, projects, legion, fleet, files, filesystem,
permissions, system's docker-status, proxy) has no such Hub-local state to
maintain — REMOTE's response IS the answer, verbatim. For those, `forward()`
is a generic proxy: take the incoming request, replay it against REMOTE's
mirrored `/api/backend/<path>` route, and return the response byte-for-byte.
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


async def forward(coordinator, request: Request) -> Response:
    """Forward `request` to REMOTE's mirrored `/api/backend/<path>` route.

    Strips the leading `/api` from `request.url.path`, issues the same
    method/query-params/body against `coordinator.backend`'s REMOTE client,
    and returns the response verbatim (status code + body). A connection
    failure (REMOTE unreachable) becomes a 502 rather than propagating —
    the Hub process must never crash because REMOTE is down (contract §8).
    """
    path = request.url.path
    if path.startswith("/api/"):
        path = path[len("/api"):]
    body = await request.body()

    resp = await coordinator.backend.relay_request(
        request.method, path, params=list(request.query_params.multi_items()), content=body
    )
    if resp is None:
        return JSONResponse(status_code=502, content={"detail": "REMOTE unreachable"})

    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return JSONResponse(status_code=resp.status_code, content=resp.json())
        except ValueError:
            logger.warning(f"REMOTE response for {path} declared JSON but failed to parse")
    return Response(
        status_code=resp.status_code, content=resp.content, media_type=content_type or None
    )
