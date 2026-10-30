"""Shared validation and error mapping of the windowed history reads (issue #2030, stage 3 PR 3A):
``window=tail`` / ``before=`` (stage 1), ``around=`` / ``around_id=`` and ``after=`` (+``until=``)."""

from collections.abc import Awaitable

from fastapi import HTTPException

from ..message_history_reader import (
    WINDOW_MODES,
    ArchiveNotFoundError,
    CursorInvalidError,
    MessageNotFoundError,
    ResolveBusyError,
    is_plain_id,
)


def validate_window_params(
    *, window: str | None, before: str | None, around: str | None, around_id: str | None,
    after: str | None, until: str | None, before_bytes: int | None, after_bytes: int | None,
) -> None:
    """422 for an unknown ``window``, more than one mode, an empty cursor/id, or a modifier
    (``until`` / ``before_bytes`` / ``after_bytes``) without its mode."""
    if window is not None and window != "tail":
        raise HTTPException(status_code=422, detail="window must be 'tail'")
    given = {
        k: v for k, v in zip(
            WINDOW_MODES, (window, before, around, around_id, after), strict=True
        ) if v is not None
    }
    if len(given) > 1:
        raise HTTPException(
            status_code=422, detail=f"{' and '.join(given)} are mutually exclusive"
        )
    for name, value in (
        ("around", around), ("around_id", around_id), ("after", after), ("until", until)
    ):
        if value is not None and not value.strip():
            raise HTTPException(status_code=422, detail=f"{name} must not be empty")
    if around_id is not None and not is_plain_id(around_id):
        raise HTTPException(
            status_code=422,
            detail="around_id must be 1-256 ASCII characters without quotes, backslashes or "
            "control characters (a message_id or tool_use_id)",
        )
    if until is not None and after is None:
        raise HTTPException(status_code=422, detail="until requires after")
    if (before_bytes is not None or after_bytes is not None) and around is None and (
        around_id is None
    ):
        raise HTTPException(
            status_code=422, detail="before_bytes/after_bytes require around or around_id"
        )


async def map_window_errors(call: Awaitable[dict]) -> dict:
    """Await a windowed read: 409 ``cursor_invalid``, 404 ``message_not_found``, 503
    ``resolve_busy`` (the id lookup could not get a pool slot in time)."""
    try:
        return await call
    except CursorInvalidError as e:
        raise HTTPException(status_code=409, detail="cursor_invalid") from e
    except ArchiveNotFoundError as e:
        raise HTTPException(status_code=404, detail="Archive not found") from e
    except MessageNotFoundError as e:
        raise HTTPException(status_code=404, detail="message_not_found") from e
    except ResolveBusyError as e:
        raise HTTPException(
            status_code=503, detail="resolve_busy", headers={"Retry-After": "2"}
        ) from e
