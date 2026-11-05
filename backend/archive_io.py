"""Dedicated small thread pool for whole-file archive work (issue #2312).

Distilling a session's history and copying its artifacts into an archive read/write the whole
``messages.jsonl`` (100+ MB for long sessions). Run on the event loop that froze every other
session's requests for ~0.5-1 s, so the work goes to a worker thread instead.

A separate 2-worker pool, not the loop's default executor: the default executor is shared with
latency-sensitive callers (``asyncio.to_thread`` for SDK lookups, git, etc.) that must not queue
behind several multi-hundred-ms archive jobs, and not the cold-scan pool (#2311), which is
reserved for cold snapshot scans.
"""

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")

ARCHIVE_IO_WORKERS = 2

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=ARCHIVE_IO_WORKERS, thread_name_prefix="archive-io"
            )
        return _executor


async def run_archive_io(fn: Callable[..., T], *args) -> T:
    """Run blocking archive work ``fn(*args)`` on the archive pool."""
    return await asyncio.get_running_loop().run_in_executor(_get_executor(), fn, *args)
