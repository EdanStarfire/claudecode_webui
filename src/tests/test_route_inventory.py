"""Route inventory test — asserts the API surface is stable across refactors (issue #851)."""


def test_route_count_unchanged():
    from src.web_server import create_app
    app = create_app()
    api_routes = [r for r in app.routes if hasattr(r, "methods")]
    assert len(api_routes) == 142, (
        f"Expected 142 routes (+1 usage from #1125, +1 edit-history from #1128, +3 audit from #1127, +1 analytics from #1132, -2 legacy images from #1261, +1 oauth import-as-secret from #1381, -1 cancel-schedule from #1416, +1 reparent-minion from #1422, +1 session-routing from #1427-phase3, +6 provider-catalog from #1427-phase4, +1 queue-history from #1502, +1 session-links from #1530, +1 mark-unread from #1597), got {len(api_routes)}. "
        "A route was added or removed."
    )
