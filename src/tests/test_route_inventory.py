"""Route inventory test — asserts the API surface is stable across refactors (issue #851)."""


def test_route_count_unchanged():
    from src.web_server import create_app
    app = create_app()
    api_routes = [r for r in app.routes if hasattr(r, "methods")]
    assert len(api_routes) == 128, (
        f"Expected 128 routes (+1 edit-history from issue-1128), got {len(api_routes)}. "
        "A route was added or removed."
    )
