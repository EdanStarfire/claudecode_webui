"""
Integration tests for template CRUD API — issue #1116.

Verifies that PUT /api/templates/{id} correctly persists all SessionConfig fields,
particularly the 8 fields that were previously silently dropped by Pydantic
because they were missing from TemplateUpdateRequest.
"""

import pytest


@pytest.mark.asyncio
async def test_issue_1116_put_template_persists_docker_proxy_allowlist_domains(
    api_integration_env,
):
    """PUT /api/templates/{id} must persist docker_proxy_allowlist_domains."""
    client = api_integration_env["client"]

    # Create template
    create_resp = await client.post(
        "/api/templates",
        json={
            "name": "Proxy Test Template",
            "permission_mode": "default",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    template_id = create_resp.json()["template_id"]

    # Update with docker proxy fields
    update_resp = await client.put(
        f"/api/templates/{template_id}",
        json={
            "docker_proxy_allowlist_domains": ["example.com", "api.example.com"],
            "assigned_secrets": ["vault-cred-1"],
            "docker_proxy_enabled": True,
            "docker_proxy_image": "proxy:v2",
            "docker_home_directory": "/home/agent",
        },
    )
    assert update_resp.status_code == 200, update_resp.text

    # Fetch and verify
    get_resp = await client.get(f"/api/templates/{template_id}")
    assert get_resp.status_code == 200, get_resp.text
    t = get_resp.json()

    assert t["docker_proxy_allowlist_domains"] == ["example.com", "api.example.com"]
    assert t["assigned_secrets"] == ["vault-cred-1"]
    assert t["docker_proxy_enabled"] is True
    assert t["docker_proxy_image"] == "proxy:v2"
    assert t["docker_home_directory"] == "/home/agent"


@pytest.mark.asyncio
async def test_issue_1116_put_template_persists_runtime_flags(api_integration_env):
    """PUT /api/templates/{id} must persist setting_sources, bare_mode, env_scrub_enabled."""
    client = api_integration_env["client"]

    create_resp = await client.post(
        "/api/templates",
        json={
            "name": "Runtime Flags Template",
            "permission_mode": "default",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    template_id = create_resp.json()["template_id"]

    update_resp = await client.put(
        f"/api/templates/{template_id}",
        json={
            "setting_sources": ["user", "project"],
            "bare_mode": True,
            "env_scrub_enabled": True,
        },
    )
    assert update_resp.status_code == 200, update_resp.text

    get_resp = await client.get(f"/api/templates/{template_id}")
    assert get_resp.status_code == 200, get_resp.text
    t = get_resp.json()

    assert t["setting_sources"] == ["user", "project"]
    assert t["bare_mode"] is True
    assert t["env_scrub_enabled"] is True
