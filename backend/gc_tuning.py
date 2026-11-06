"""One-off GC heap freeze at Backend startup (issue #2336).

The Backend imports ~880k GC-tracked objects (SDK, FastAPI, pydantic, ...). A full (gen-2)
collection traverses all of them: ~280 ms with the GIL held, which stalls the event loop. Large
history pages allocate enough short-lived objects to trigger a gen-2 pass, and the stall landed
in whichever request happened to allocate (the "340 ms inside jsonable_encoder" of #2313). It
cannot be moved to a worker thread.

``gc.freeze()`` moves every object alive after startup into a permanent generation the collector
no longer traverses (tracked objects 884k -> 16k, gen-2 pass ~280 ms -> a few ms). Cost: frozen
objects are never collected. They are the module-level objects that live for the whole process
anyway (code, classes, caches built at import and initialize), so resident memory is unchanged
(measured: RSS peak equal with and without). Anything allocated afterwards is collected normally;
cyclic garbage created before the freeze is dropped by the ``gc.collect()`` that precedes it.
Known limit: an object alive at freeze time that later becomes cyclic-only garbage (e.g. a
startup-loaded session record replaced after a delete) is reclaimed by refcounting only.

Applied once, only by the real server lifespan (``web_server.create_app``); the test conftest
stubs it so the pytest process heap is never frozen
(``BACKEND_DISABLE_GC_FREEZE=1`` turns it off; the root ``conftest.py`` sets it).
"""

import gc
import logging
import os

logger = logging.getLogger(__name__)

_frozen = False


def freeze_startup_heap() -> bool:
    """Collect, then freeze the heap; True when applied now, False if already applied/disabled."""
    global _frozen
    if _frozen or os.environ.get("BACKEND_DISABLE_GC_FREEZE"):
        return False
    gc.collect()
    gc.freeze()
    _frozen = True
    logger.info("GC heap frozen after startup (%d objects)", gc.get_freeze_count())
    return True
