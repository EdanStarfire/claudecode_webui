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

    assert t["config"].get("docker_proxy_allowlist_domains") == ["example.com", "api.example.com"]
    assert t["config"].get("assigned_secrets") == ["vault-cred-1"]
    assert t["config"].get("docker_proxy_enabled") is True
    assert t["config"].get("docker_proxy_image") == "proxy:v2"
    assert t["config"].get("docker_home_directory") == "/home/agent"


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

    assert t["config"].get("setting_sources") == ["user", "project"]
    assert t["config"].get("bare_mode") is True
    assert t["config"].get("env_scrub_enabled") is True


@pytest.mark.asyncio
async def test_issue_1229_clear_docker_proxy_allowlist_domains(api_integration_env):
    """PUT with docker_proxy_allowlist_domains=[] must persist the empty list (not be dropped)."""
    client = api_integration_env["client"]

    create_resp = await client.post(
        "/api/templates",
        json={"name": "Allowlist Clear Test", "permission_mode": "default"},
    )
    assert create_resp.status_code == 200, create_resp.text
    template_id = create_resp.json()["template_id"]

    # Populate the field
    await client.put(
        f"/api/templates/{template_id}",
        json={"docker_proxy_allowlist_domains": ["httpbin.org"]},
    )

    # Clear it by sending []
    clear_resp = await client.put(
        f"/api/templates/{template_id}",
        json={"docker_proxy_allowlist_domains": []},
    )
    assert clear_resp.status_code == 200, clear_resp.text

    get_resp = await client.get(f"/api/templates/{template_id}")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["config"].get("docker_proxy_allowlist_domains") == []


@pytest.mark.asyncio
async def test_issue_1229_clear_assigned_secrets(api_integration_env):
    """PUT with assigned_secrets=[] must persist the empty list (not be dropped)."""
    client = api_integration_env["client"]

    create_resp = await client.post(
        "/api/templates",
        json={"name": "Secrets Clear Test", "permission_mode": "default"},
    )
    assert create_resp.status_code == 200, create_resp.text
    template_id = create_resp.json()["template_id"]

    await client.put(
        f"/api/templates/{template_id}",
        json={"assigned_secrets": ["vault-cred-1"]},
    )

    clear_resp = await client.put(
        f"/api/templates/{template_id}",
        json={"assigned_secrets": []},
    )
    assert clear_resp.status_code == 200, clear_resp.text

    get_resp = await client.get(f"/api/templates/{template_id}")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["config"].get("assigned_secrets") == []


@pytest.mark.asyncio
async def test_issue_1229_clear_allowed_tools(api_integration_env):
    """PUT with allowed_tools=[] must persist the empty list (not be dropped)."""
    client = api_integration_env["client"]

    create_resp = await client.post(
        "/api/templates",
        json={"name": "AllowedTools Clear Test", "permission_mode": "default"},
    )
    assert create_resp.status_code == 200, create_resp.text
    template_id = create_resp.json()["template_id"]

    await client.put(
        f"/api/templates/{template_id}",
        json={"allowed_tools": ["Bash", "Read"]},
    )

    clear_resp = await client.put(
        f"/api/templates/{template_id}",
        json={"allowed_tools": []},
    )
    assert clear_resp.status_code == 200, clear_resp.text

    get_resp = await client.get(f"/api/templates/{template_id}")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["config"].get("allowed_tools") == []


@pytest.mark.asyncio
async def test_issue_1230_post_template_with_template_overrides_rejected(api_integration_env):
    """POST /api/templates with template_overrides must be rejected (issue #1230)."""
    client = api_integration_env["client"]

    resp = await client.post(
        "/api/templates",
        json={
            "name": "Legacy Template",
            "permission_mode": "default",
            "template_overrides": {"model": "claude-opus-4-7"},
        },
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_issue_1230_put_template_with_template_overrides_rejected(api_integration_env):
    """PUT /api/templates/{id} with template_overrides must be rejected (issue #1230)."""
    client = api_integration_env["client"]

    # First create a valid template
    create_resp = await client.post(
        "/api/templates",
        json={"name": "Update Target", "permission_mode": "default"},
    )
    assert create_resp.status_code == 200, create_resp.text
    template_id = create_resp.json()["template_id"]

    # Now attempt update with legacy field
    resp = await client.put(
        f"/api/templates/{template_id}",
        json={"template_overrides": {"model": "claude-opus-4-7"}},
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
