"""Route inventory test — asserts the API surface is stable across refactors (issue #851).

Issue #498: the single-app route count split across two processes. This file now
counts Backend's own route table only — core.py (/, /health, /api/auth/check) stayed
Frontend-side entirely; config.py exists on BOTH sides now (Frontend's 2 routes do a
merged-read/split-write relay, Backend's own 2 routes own everything but
networking/backend_connection); poll.py's 4 routes are duplicated into
backend/routers/poll.py (Backend owns the EventQueues); interrupt/permission-response
moved from core.py into backend/routers/session_runtime.py (backend-owned state).
"""


def _count_api_routes(app):
    """Recursively count routes exposing `.methods`.

    FastAPI >=0.137 wraps each `include_router()`-registered router in an
    internal `_IncludedRouter` entry instead of flattening its routes into
    `app.routes` directly, so a flat `hasattr(r, "methods")` scan of
    `app.routes` alone undercounts. Recurse into `original_router.routes`
    to reach the same `APIRoute` objects the old flat list exposed.
    """
    count = 0
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        if hasattr(route, "original_router"):
            stack.extend(route.original_router.routes)
        elif hasattr(route, "methods"):
            count += 1
    return count


def test_route_count_unchanged():
    from backend.web_server import create_app
    app = create_app()
    api_routes_count = _count_api_routes(app)
    assert api_routes_count == 154, (
        f"Expected 154 Backend routes post-#498 split (core.py's 3 browser routes "
        f"stayed Frontend-side only; config.py now has its own 2 backend-owned routes "
        f"here PLUS 2 more on the Frontend side doing merged-read/split-write; poll.py's "
        f"4 routes are duplicated into backend/routers/poll.py since Backend owns the "
        f"EventQueues; interrupt + permission-response relocated here from core.py), "
        f"got {api_routes_count}. A route was added or removed."
    )
