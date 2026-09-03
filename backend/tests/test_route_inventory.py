"""Route inventory test — asserts the API surface is stable across refactors (issue #851).

Issue #498: the single-app route count split across two processes. This file now
counts Backend's own route table only — core.py (/, /health, /api/auth/check) and
config.py stayed Frontend-side; poll.py's 4 routes are duplicated into
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
    assert api_routes_count == 152, (
        f"Expected 152 Backend routes post-#498 split (core.py's 3 browser routes and "
        f"config.py's 2 routes stayed Frontend-side; poll.py's 4 routes are duplicated "
        f"into backend/routers/poll.py since Backend owns the EventQueues; interrupt + "
        f"permission-response relocated here from core.py), got {api_routes_count}. "
        "A route was added or removed."
    )
