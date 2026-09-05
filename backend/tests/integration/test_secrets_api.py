"""
API tests for secrets CRUD endpoints — issue #827.

Tests: GET /api/secrets, POST /api/secrets, PATCH /api/secrets/{name},
       DELETE /api/secrets/{name}

Keyring is mocked so tests run without OS keyring.
"""

from unittest.mock import patch

import pytest


@pytest.fixture
def mock_keyring():
    """Patch keyring functions for all secrets API tests."""
    _store = {}

    def _set(name, value, service_name="cc_webui"):
        _store[name] = value

    def _get(name, service_name="cc_webui"):
        return _store.get(name)

    def _del(name, service_name="cc_webui"):
        existed = name in _store
        _store.pop(name, None)
        return existed

    with (
        patch("backend.credential_vault.set_secret_value", side_effect=_set),
        patch("backend.credential_vault.get_secret_value", side_effect=_get),
        patch("backend.credential_vault.delete_secret_value", side_effect=_del),
    ):
        yield _store


@pytest.mark.asyncio
async def test_issue_827_list_secrets_empty(api_integration_env, mock_keyring):
    """GET /api/secrets returns empty list when no secrets exist."""
    client = api_integration_env["client"]
    resp = await client.get("/api/secrets")
    assert resp.status_code == 200
    data = resp.json()
    assert "secrets" in data
    assert isinstance(data["secrets"], list)


@pytest.mark.asyncio
async def test_issue_827_create_secret(api_integration_env, mock_keyring):
    """POST /api/secrets creates a secret and returns 201 with metadata only."""
    client = api_integration_env["client"]

    resp = await client.post(
        "/api/secrets",
        json={
            "name": "github-token",
            "type": "api_key",
            "target_hosts": ["api.github.com"],
            "value": "ghp_super_secret",
            "inject_env": "GITHUB_TOKEN",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "github-token"
    assert data["type"] == "api_key"
    assert "value" not in data
    assert "real_value" not in data
    assert mock_keyring.get("github-token") == "ghp_super_secret"


@pytest.mark.asyncio
async def test_issue_827_list_after_create(api_integration_env, mock_keyring):
    """GET /api/secrets returns the created secret."""
    client = api_integration_env["client"]

    await client.post(
        "/api/secrets",
        json={
            "name": "my-secret",
            "type": "generic",
            "target_hosts": ["example.com"],
            "value": "val123",
        },
    )

    resp = await client.get("/api/secrets")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()["secrets"]]
    assert "my-secret" in names


@pytest.mark.asyncio
async def test_issue_827_update_secret_metadata(api_integration_env, mock_keyring):
    """PATCH /api/secrets/{name} updates metadata without requiring a new value."""
    client = api_integration_env["client"]

    await client.post(
        "/api/secrets",
        json={
            "name": "update-me",
            "type": "generic",
            "target_hosts": ["old.example.com"],
            "value": "original",
        },
    )

    resp = await client.patch(
        "/api/secrets/update-me",
        json={"target_hosts": ["new.example.com"], "type": "bearer"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["type"] == "bearer"
    assert "new.example.com" in data["target_hosts"]
    # original value preserved in keyring
    assert mock_keyring.get("update-me") == "original"


@pytest.mark.asyncio
async def test_issue_827_update_secret_value(api_integration_env, mock_keyring):
    """PATCH /api/secrets/{name} with value rotates the keyring entry."""
    client = api_integration_env["client"]

    await client.post(
        "/api/secrets",
        json={
            "name": "rotate-me",
            "type": "generic",
            "target_hosts": ["example.com"],
            "value": "original_value",
        },
    )

    resp = await client.patch(
        "/api/secrets/rotate-me",
        json={"value": "rotated_value"},
    )
    assert resp.status_code == 200, resp.text
    assert mock_keyring.get("rotate-me") == "rotated_value"


@pytest.mark.asyncio
async def test_issue_827_delete_secret(api_integration_env, mock_keyring):
    """DELETE /api/secrets/{name} removes the secret."""
    client = api_integration_env["client"]

    await client.post(
        "/api/secrets",
        json={
            "name": "delete-me",
            "type": "generic",
            "target_hosts": ["example.com"],
            "value": "goodbye",
        },
    )

    resp = await client.delete("/api/secrets/delete-me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True
    assert "delete-me" not in mock_keyring

    # Verify it no longer appears in list
    list_resp = await client.get("/api/secrets")
    names = [s["name"] for s in list_resp.json()["secrets"]]
    assert "delete-me" not in names


@pytest.mark.asyncio
async def test_issue_827_delete_nonexistent_returns_404(api_integration_env, mock_keyring):
    """DELETE /api/secrets/{name} returns 404 when secret does not exist."""
    client = api_integration_env["client"]
    resp = await client.delete("/api/secrets/no-such-secret")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_issue_827_create_duplicate_returns_409(api_integration_env, mock_keyring):
    """POST /api/secrets with duplicate name returns 409."""
    client = api_integration_env["client"]

    payload = {
        "name": "dup-secret",
        "type": "generic",
        "target_hosts": ["example.com"],
        "value": "val1",
    }
    r1 = await client.post("/api/secrets", json=payload)
    assert r1.status_code == 201

    payload["value"] = "val2"
    r2 = await client.post("/api/secrets", json=payload)
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_issue_1240_delete_case_variant_returns_200(api_integration_env, mock_keyring):
    """DELETE /api/secrets/{NAME} with different case returns 200 and secret disappears."""
    client = api_integration_env["client"]

    await client.post(
        "/api/secrets",
        json={
            "name": "github-token",
            "type": "api_key",
            "target_hosts": ["api.github.com"],
            "value": "ghp_secret",
        },
    )

    resp = await client.delete("/api/secrets/GITHUB-TOKEN")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True

    list_resp = await client.get("/api/secrets")
    names = [s["name"] for s in list_resp.json()["secrets"]]
    assert "github-token" not in names


@pytest.mark.asyncio
async def test_issue_1772_list_secrets_includes_usage(api_integration_env, mock_keyring):
    """GET /api/secrets enriches each secret with a usage breakdown.

    Covers: session assignment, MCP config ${secret:NAME} refs, and OAuth2
    refresh dependency recognition (client_secret_secret_name /
    refresh_token_secret_name) — the exact scenario issue #1772 calls out,
    where the client-secret/refresh-token siblings are never directly
    assigned to anything.
    """
    client = api_integration_env["client"]
    create_test_project = api_integration_env["create_test_project"]
    create_test_session = api_integration_env["create_test_session"]

    project = await create_test_project()

    # Secret directly assigned to a session.
    await client.post(
        "/api/secrets",
        json={
            "name": "session-secret",
            "type": "generic",
            "target_hosts": ["example.com"],
            "value": "sval",
        },
    )
    # Secret referenced from an MCP server config header.
    await client.post(
        "/api/secrets",
        json={
            "name": "mcp-secret",
            "type": "generic",
            "target_hosts": ["example.com"],
            "value": "mval",
        },
    )
    # OAuth2 refresh dependency siblings — never directly assigned anywhere.
    await client.post(
        "/api/secrets",
        json={
            "name": "oauth-client-secret",
            "type": "generic",
            "target_hosts": ["example.com"],
            "value": "cval",
        },
    )
    await client.post(
        "/api/secrets",
        json={
            "name": "oauth-refresh-token",
            "type": "generic",
            "target_hosts": ["example.com"],
            "value": "rval",
        },
    )
    resp = await client.post(
        "/api/secrets",
        json={
            "name": "github-oauth",
            "type": "oauth2",
            "target_hosts": ["example.com"],
            "value": "access-tok",
            "scrub": {"matcher_regex": "token"},
            "refresh": {
                "token_url": "https://example.com/token",
                "client_id": "client-id",
                "refresh_token_secret_name": "oauth-refresh-token",
                "client_secret_secret_name": "oauth-client-secret",
            },
        },
    )
    assert resp.status_code == 201, resp.text

    await create_test_session(
        project["project_id"], name="Usage Session", assigned_secrets=["session-secret"]
    )

    mcp_resp = await client.post(
        "/api/mcp-configs",
        json={
            "name": "Usage MCP Server",
            "type": "http",
            "url": "https://mcp.example.com",
            "headers": {"Authorization": "Bearer ${secret:mcp-secret}"},
        },
    )
    assert mcp_resp.status_code == 200, mcp_resp.text

    list_resp = await client.get("/api/secrets")
    assert list_resp.status_code == 200
    by_name = {s["name"]: s for s in list_resp.json()["secrets"]}

    assert by_name["session-secret"]["usage"]["sessions"] == 1
    assert by_name["session-secret"]["usage"]["total"] == 1

    assert by_name["mcp-secret"]["usage"]["mcp_servers"] == 1
    assert by_name["mcp-secret"]["usage"]["total"] == 1

    assert by_name["oauth-client-secret"]["usage"]["oauth2_dependents"] == ["github-oauth"]
    assert by_name["oauth-client-secret"]["usage"]["total"] == 1

    assert by_name["oauth-refresh-token"]["usage"]["oauth2_dependents"] == ["github-oauth"]
    assert by_name["oauth-refresh-token"]["usage"]["total"] == 1

    assert by_name["github-oauth"]["usage"]["total"] == 0


@pytest.mark.asyncio
async def test_issue_1240_create_slug_collision_returns_400(api_integration_env, mock_keyring):
    """POST /api/secrets with a slug-collision name returns 400."""
    client = api_integration_env["client"]

    r1 = await client.post(
        "/api/secrets",
        json={
            "name": "my-token",
            "type": "generic",
            "target_hosts": ["example.com"],
            "value": "val1",
        },
    )
    assert r1.status_code == 201

    # "my_token" slugifies to "my_token", "my-token" slugifies to "my-token" — different slugs.
    # To force a real collision we create a secret that is identical (exact duplicate).
    r2 = await client.post(
        "/api/secrets",
        json={
            "name": "my-token",
            "type": "generic",
            "target_hosts": ["example.com"],
            "value": "val2",
        },
    )
    assert r2.status_code == 400
    assert "already exists" in r2.text
