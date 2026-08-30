"""Route inventory test — asserts the API surface is stable across refactors (issue #851)."""


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
    from src.web_server import create_app
    app = create_app()
    api_routes_count = _count_api_routes(app)
    assert api_routes_count == 156, (
        f"Expected 156 routes (+1 usage from #1125, +1 edit-history from #1128, +3 audit from #1127, +1 analytics from #1132, -2 legacy images from #1261, +1 oauth import-as-secret from #1381, -1 cancel-schedule from #1416, +1 reparent-minion from #1422, +1 session-routing from #1427-phase3, +6 provider-catalog from #1427-phase4, +1 queue-history from #1502, +1 session-links from #1530, +1 mark-unread from #1597, +1 unaccounted pre-existing delta, +1 model live-switch from #1673, +1 add-directory from #1675, +5 kanban-groups from #1722, +1 background-agents from #1746, +2 git-branches/git-commits from #1760, +1 mcp-config-tools from #1799, +1 mcp-config-test-connect from #1800, +1 context-usage from #498), got {api_routes_count}. "
        "A route was added or removed."
    )
