"""
Tests for CredentialVault — Issue #1053: domain allowlist and credential management.

Covers:
1. create_credential returns metadata without value
2. list_credentials excludes secret values
3. delete_credential removes both metadata and secret files
4. resolve_credentials returns full objects (with values) for sidecar assembly
5. create_credential raises ValueError on duplicate name
6. Secret file permissions are 0o600
"""

import os
import stat

import pytest

from ..credential_vault import CredentialVault


@pytest.fixture
def vault(tmp_path):
    """Return a CredentialVault backed by a temp directory."""
    return CredentialVault(tmp_path)


def _sample_cred(**overrides):
    """Return minimal credential kwargs."""
    base = {
        "name": "test_token",
        "host_pattern": "api.example.com",
        "header_name": "Authorization",
        "value_format": "Bearer {value}",
        "real_value": "super_secret_123",
        "delivery": {"type": "env", "var": "TEST_TOKEN"},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_issue_1053_create_credential_returns_metadata_without_value(vault):
    """POST /api/proxy/credentials response must never contain real_value."""
    result = await vault.create_credential(**_sample_cred())
    assert "real_value" not in result
    assert result["name"] == "test_token"
    assert result["host_pattern"] == "api.example.com"
    assert result["header_name"] == "Authorization"
    assert "created_at" in result
    assert "updated_at" in result


@pytest.mark.asyncio
async def test_issue_1053_list_credentials_excludes_values(vault):
    """GET /api/proxy/credentials response list must never contain real_value."""
    await vault.create_credential(**_sample_cred())
    await vault.create_credential(**_sample_cred(name="another", host_pattern="other.com"))

    creds = await vault.list_credentials()
    assert len(creds) == 2
    for cred in creds:
        assert "real_value" not in cred


@pytest.mark.asyncio
async def test_issue_1053_delete_credential_removes_both_files(vault):
    """delete_credential must remove both .json metadata and .secret value files."""
    from ..slug_utils import slugify
    await vault.create_credential(**_sample_cred())

    slug = slugify("test_token")
    assert (vault._creds_dir / f"{slug}.json").exists()
    assert (vault._creds_dir / f"{slug}.secret").exists()

    deleted = await vault.delete_credential("test_token")

    assert deleted is True
    assert not (vault._creds_dir / f"{slug}.json").exists()
    assert not (vault._creds_dir / f"{slug}.secret").exists()


@pytest.mark.asyncio
async def test_issue_1053_delete_nonexistent_returns_false(vault):
    """delete_credential returns False when credential does not exist."""
    result = await vault.delete_credential("does_not_exist")
    assert result is False


@pytest.mark.asyncio
async def test_issue_1053_resolve_credentials_returns_full_objects(vault):
    """resolve_credentials (internal) must return objects with real_value for sidecar assembly."""
    await vault.create_credential(**_sample_cred())
    resolved = await vault.resolve_credentials(["test_token"])
    assert len(resolved) == 1
    assert resolved[0]["real_value"] == "super_secret_123"
    assert resolved[0]["name"] == "test_token"


@pytest.mark.asyncio
async def test_issue_1053_create_duplicate_name_fails(vault):
    """create_credential must raise ValueError when name already exists."""
    await vault.create_credential(**_sample_cred())
    with pytest.raises(ValueError, match="already exists"):
        await vault.create_credential(**_sample_cred())


@pytest.mark.asyncio
async def test_issue_1053_file_permissions_secret(vault):
    """Secret file must have 0o600 permissions."""
    await vault.create_credential(**_sample_cred())
    from ..slug_utils import slugify
    slug = slugify("test_token")
    secret_path = vault._creds_dir / f"{slug}.secret"
    # 0o600 → '-rw-------'
    assert oct(stat.S_IMODE(os.stat(secret_path).st_mode)) == "0o600"


@pytest.mark.asyncio
async def test_issue_1053_resolve_unknown_name_skipped(vault):
    """resolve_credentials skips names not found in vault and logs a warning."""
    await vault.create_credential(**_sample_cred())
    # Mix of valid and invalid names
    resolved = await vault.resolve_credentials(["test_token", "nonexistent"])
    assert len(resolved) == 1
    assert resolved[0]["name"] == "test_token"


@pytest.mark.asyncio
async def test_issue_1053_get_credential_value_internal(vault):
    """get_credential_value must return the raw secret for internal use."""
    await vault.create_credential(**_sample_cred())
    value = await vault.get_credential_value("test_token")
    assert value == "super_secret_123"


@pytest.mark.asyncio
async def test_issue_1053_get_credential_value_unknown_returns_none(vault):
    """get_credential_value returns None for unknown names."""
    value = await vault.get_credential_value("no_such_cred")
    assert value is None
