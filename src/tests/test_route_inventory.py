"""Route inventory test — asserts the API surface is stable across refactors (issue #851)."""


def test_route_count_unchanged():
    from src.web_server import create_app
    app = create_app()
    api_routes = [r for r in app.routes if hasattr(r, "methods")]
    assert len(api_routes) == 133, (
        f"Expected 133 routes (+1 usage from #1125, +1 edit-history from #1128, +3 audit from #1127, +1 analytics from #1132), got {len(api_routes)}. "
        "A route was added or removed."
    )
