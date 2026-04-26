"""Route inventory test — asserts the API surface is stable across refactors (issue #851)."""


def test_route_count_unchanged():
    from src.web_server import create_app
    app = create_app()
    api_routes = [r for r in app.routes if hasattr(r, "methods")]
    assert len(api_routes) == 124, (
        f"Expected 124 routes (+5 secrets, +1 backend-status, -3 proxy creds from issue-827), got {len(api_routes)}. "
        "A route was added or removed."
    )
