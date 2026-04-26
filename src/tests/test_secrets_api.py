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
        patch("src.credential_vault.set_secret_value", side_effect=_set),
        patch("src.credential_vault.get_secret_value", side_effect=_get),
        patch("src.credential_vault.delete_secret_value", side_effect=_del),
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
    assert resp.status_code == 204, resp.text
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
    assert r2.status_code == 409
