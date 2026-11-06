"""Off-loop JSON serialization of large history responses (issue #2336).

FastAPI serializes a route's returned dict on the event loop (``jsonable_encoder`` then
``JSONResponse.render``); for a 4 MiB history page that is ~300 ms during which every other
session's requests stall. A response whose size hint is at or above ``OFFLOAD_MIN_BYTES`` is
built on a small dedicated pool instead; the body is produced by the very same two calls
(``JSONResponse(jsonable_encoder(content))``), so the bytes, content-type and content-length are
identical to the inline path. Smaller responses keep the inline path (no thread hop).

A separate pool, not the loop's default executor (latency-sensitive callers) and not the cold-scan
pool (#2311) or archive pool (#2312).
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

OFFLOAD_MIN_BYTES = 128 * 1024
SERIALIZE_WORKERS = 2
_TURN_ENTRY_BYTES = 72  # ~ ``[cursor, message_id, size_hint]`` as JSON

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


class SizedPayload(dict):
    """A response dict carrying an estimate of its serialized size (not itself serialized)."""

    approx_json_bytes: int = 0

    def __init__(self, data: dict, approx_json_bytes: int):
        super().__init__(data)
        self.approx_json_bytes = approx_json_bytes


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=SERIALIZE_WORKERS, thread_name_prefix="json-serialize"
            )
        return _executor


def estimate_json_bytes(content: Any) -> int:
    """Cheap serialized-size estimate: the carried hint, or entries x average for a turn index."""
    hint = getattr(content, "approx_json_bytes", None)
    if hint is not None:
        return hint
    if isinstance(content, dict):
        turns = content.get("turns")
        if isinstance(turns, list):
            return len(turns) * _TURN_ENTRY_BYTES
    return 0


def _build(content: Any) -> JSONResponse:
    return JSONResponse(jsonable_encoder(content))


async def offload_large_json(content: Any) -> Any:
    """``content`` unchanged when small (FastAPI serializes it inline as before), else the
    ``JSONResponse`` built on the serialize pool."""
    if estimate_json_bytes(content) < OFFLOAD_MIN_BYTES:
        return content
    return await asyncio.get_running_loop().run_in_executor(_get_executor(), _build, content)
