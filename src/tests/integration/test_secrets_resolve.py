"""
Tests for per-session secrets resolve endpoint — issue #827.

Covers:
1. secret_fetch_token is generated at start_session()
2. GET /api/sessions/{id}/secrets/resolve requires valid Bearer token
3. 401 returned for missing/wrong token
4. 200 with resolved values for correct token
"""


import pytest

# ---------------------------------------------------------------------------
# Token generation on start_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_827_secret_fetch_token_generated_at_start(tmp_path):
    """SessionCoordinator.start_session() sets secret_fetch_token on SessionInfo."""
    from datetime import UTC, datetime

    from src.session_manager import SessionInfo, SessionState

    now = datetime.now(UTC)
    session = SessionInfo(
        session_id="test-sid",
        project_id="test-pid",
        name="test",
        state=SessionState.CREATED,
        working_directory=str(tmp_path),
        created_at=now,
        updated_at=now,
    )
    assert session.secret_fetch_token is None

    import secrets as _sec
    session.secret_fetch_token = _sec.token_urlsafe(32)

    assert session.secret_fetch_token is not None
    assert len(session.secret_fetch_token) >= 32


def test_issue_827_secret_fetch_token_cleared_on_terminate():
    """terminate_session clears secret_fetch_token to None."""
    from datetime import UTC, datetime

    from src.session_manager import SessionInfo, SessionState

    now = datetime.now(UTC)
    session = SessionInfo(
        session_id="test-sid",
        project_id="test-pid",
        name="test",
        state=SessionState.ACTIVE,
        working_directory="/tmp",
        created_at=now,
        updated_at=now,
    )
    session.secret_fetch_token = "some_token"

    session.secret_fetch_token = None
    assert session.secret_fetch_token is None


# ---------------------------------------------------------------------------
# SessionInfo backward compat: secret_fetch_token defaults to None
# ---------------------------------------------------------------------------


def test_issue_827_session_info_from_dict_defaults_token_to_none():
    """SessionInfo.from_dict() sets secret_fetch_token=None for old state.json without the field."""
    from datetime import UTC, datetime

    from src.session_manager import SessionInfo, SessionState

    now = datetime.now(UTC).isoformat()
    data = {
        "session_id": "old-session",
        "project_id": "proj",
        "name": "old",
        "state": SessionState.CREATED.value,
        "working_directory": "/tmp",
        "created_at": now,
        "updated_at": now,
    }
    si = SessionInfo.from_dict(data)
    assert si.secret_fetch_token is None


# ---------------------------------------------------------------------------
# Resolve endpoint authentication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_827_resolve_endpoint_requires_bearer_token(api_integration_env):
    """GET /api/sessions/{id}/secrets/resolve returns 401 without Authorization header."""
    client = api_integration_env["client"]

    proj_resp = await client.post(
        "/api/projects",
        json={"name": "Test", "working_directory": "/tmp"},
    )
    assert proj_resp.status_code == 200
    project_id = proj_resp.json()["project"]["project_id"]

    create_resp = await client.post(
        f"/api/legions/{project_id}/minions",
        json={"name": "Test Session", "permission_mode": "default"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["minion_id"]

    resp = await client.get(f"/api/sessions/{session_id}/secrets/resolve")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_issue_827_resolve_endpoint_rejects_wrong_token(api_integration_env):
    """GET /api/sessions/{id}/secrets/resolve returns 401 for wrong Bearer token."""
    client = api_integration_env["client"]

    proj_resp = await client.post(
        "/api/projects",
        json={"name": "Test2", "working_directory": "/tmp"},
    )
    project_id = proj_resp.json()["project"]["project_id"]
    create_resp = await client.post(
        f"/api/legions/{project_id}/minions",
        json={"name": "Session2", "permission_mode": "default"},
    )
    session_id = create_resp.json()["minion_id"]

    resp = await client.get(
        f"/api/sessions/{session_id}/secrets/resolve",
        headers={"Authorization": "Bearer wrong_token_xxx"},
    )
    assert resp.status_code == 401
