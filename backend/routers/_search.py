"""Shared response mapping for the whole-history search routes (issue #2030, stage 3 PR 3B)."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request

from ..history_search import SearchQueryError
from ..message_history_reader import CursorInvalidError
from ..search_scan import SearchBusyError

SUPERSEDED_PAYLOAD: dict[str, Any] = {
    "results": [], "next_cursor": None, "exhausted": False, "superseded": True,
}


async def run_search(
    request: Request, call: Callable[[Callable[[], Awaitable[bool]]], Awaitable[dict | None]]
) -> dict[str, Any]:
    """Run ``call(is_disconnected)``: 400 bad query, 409 bad/stale cursor, 503 ``search_busy``;
    a superseded / abandoned / disconnected search answers 200 ``{superseded: true}`` (never an
    error: the browser has already moved on)."""
    try:
        payload = await call(request.is_disconnected)
    except SearchQueryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except CursorInvalidError as e:
        raise HTTPException(status_code=409, detail="cursor_invalid") from e
    except SearchBusyError as e:
        raise HTTPException(
            status_code=503, detail="search_busy", headers={"Retry-After": "1"}
        ) from e
    return SUPERSEDED_PAYLOAD if payload is None else payload
