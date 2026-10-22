"""Principal: the authenticated identity behind the current request (issue #2268 AC3).

A seam for #1818's eventual multi-principal model — today there is always exactly
one implicit Principal per request (the single local user), set once by
AuthMiddleware.dispatch() after the bearer token validates, read on demand via
ApplicationService.current_principal(). Not persisted anywhere this stage (purely
an in-memory, request-scoped construct) — #1818 adds persistence once there's an
actual second principal worth distinguishing on disk.

Backed by a contextvars.ContextVar rather than a plain module attribute so
concurrent requests on the same event loop never see each other's Principal —
verified directly against the installed Starlette/anyio version that
BaseHTTPMiddleware's call_next() (which runs the downstream app in a separate
anyio task) still propagates a ContextVar set before call_next() is awaited,
since task creation copies the calling context at that point.
"""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    id: str
    backend_id: str


_current_principal: ContextVar[Principal | None] = ContextVar("current_principal", default=None)


def set_current_principal(principal: Principal | None) -> None:
    _current_principal.set(principal)


def get_current_principal() -> Principal | None:
    return _current_principal.get()
