"""Route inventory test — asserts the API surface is stable across refactors (issue #851)."""


def test_route_count_unchanged():
    from src.web_server import create_app
    app = create_app()
    api_routes = [r for r in app.routes if hasattr(r, "methods")]
    assert len(api_routes) == 127, (
        f"Expected 127 routes (+1 global refresh, +1 session-scoped PATCH secrets, +1 proxy events from issue-1134), got {len(api_routes)}. "
        "A route was added or removed."
    )
