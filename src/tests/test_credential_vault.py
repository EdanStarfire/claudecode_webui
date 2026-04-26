"""
Tests for SecretsVault (issue #827) — host-level secrets via keyring.

Replaces issue #1053 CredentialVault tests (plaintext .secret files).
Keyring calls are mocked so tests run in headless CI without OS keyring.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from ..credential_vault import SecretsVault
from ..models.secret_record import SecretRecord, SecretType


@pytest.fixture
def vault(tmp_path):
    """SecretsVault backed by a temp directory with mocked keyring."""
    with (
        patch("src.credential_vault.set_secret_value") as _set,
        patch("src.credential_vault.get_secret_value") as _get,
        patch("src.credential_vault.delete_secret_value") as _del,
    ):
        _get.return_value = "super_secret_123"
        yield SecretsVault(tmp_path), _set, _get, _del


def _sample_record(name="test_token") -> SecretRecord:
    now = datetime.now(UTC)
    return SecretRecord(
        name=name,
        type=SecretType.API_KEY,
        target_hosts=["api.example.com"],
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_issue_827_create_secret_returns_metadata_without_value(vault):
    """create_secret response must never contain the secret value."""
    sv, _set, _get, _del = vault
    result = await sv.create_secret(_sample_record(), "super_secret_123")
    assert "value" not in result
    assert "real_value" not in result
    assert result["name"] == "test_token"
    assert result["type"] == "api_key"
    assert "created_at" in result
    _set.assert_called_once_with("test_token", "super_secret_123")


@pytest.mark.asyncio
async def test_issue_827_list_secrets_excludes_values(vault):
    """list_secrets must never contain secret values."""
    sv, _set, _get, _del = vault
    await sv.create_secret(_sample_record("tok1"), "val1")
    await sv.create_secret(_sample_record("tok2"), "val2")

    secrets = await sv.list_secrets()
    assert len(secrets) == 2
    for s in secrets:
        assert "value" not in s
        assert "real_value" not in s


@pytest.mark.asyncio
async def test_issue_827_delete_secret_removes_metadata_and_calls_keyring(vault):
    """delete_secret removes the metadata JSON and calls delete_secret_value."""
    sv, _set, _get, _del = vault
    await sv.create_secret(_sample_record(), "secret_val")

    meta_path = sv._creds_dir / "test_token.json"
    assert meta_path.exists()

    deleted = await sv.delete_secret("test_token")

    assert deleted is True
    assert not meta_path.exists()
    _del.assert_called_once_with("test_token")


@pytest.mark.asyncio
async def test_issue_827_delete_nonexistent_returns_false(vault):
    sv, _, _, _ = vault
    result = await sv.delete_secret("does_not_exist")
    assert result is False


@pytest.mark.asyncio
async def test_issue_827_resolve_secrets_returns_values(vault):
    """resolve_secrets_for_assignment returns dicts with 'value' key."""
    sv, _set, _get, _del = vault
    _get.return_value = "super_secret_123"
    await sv.create_secret(_sample_record(), "super_secret_123")

    resolved = await sv.resolve_secrets_for_assignment(["test_token"])
    assert len(resolved) == 1
    assert resolved[0]["value"] == "super_secret_123"
    assert resolved[0]["name"] == "test_token"


@pytest.mark.asyncio
async def test_issue_827_resolve_unknown_name_skipped(vault):
    """resolve_secrets_for_assignment skips names not found in vault."""
    sv, _set, _get, _del = vault
    await sv.create_secret(_sample_record(), "super_secret_123")

    resolved = await sv.resolve_secrets_for_assignment(["test_token", "nonexistent"])
    assert len(resolved) == 1
    assert resolved[0]["name"] == "test_token"


@pytest.mark.asyncio
async def test_issue_827_create_duplicate_name_fails(vault):
    """create_secret raises ValueError when name already exists."""
    sv, _set, _get, _del = vault
    await sv.create_secret(_sample_record(), "first_value")
    with pytest.raises(ValueError, match="already exists"):
        await sv.create_secret(_sample_record(), "second_value")


@pytest.mark.asyncio
async def test_issue_827_metadata_file_permissions(vault):
    """Metadata JSON file must be written with 0o600 permissions."""
    import os
    import stat

    sv, _set, _get, _del = vault
    await sv.create_secret(_sample_record(), "secret_val")
    meta_path = sv._creds_dir / "test_token.json"
    assert oct(stat.S_IMODE(os.stat(meta_path).st_mode)) == "0o600"


@pytest.mark.asyncio
async def test_issue_827_update_secret_replaces_value(vault):
    """update_secret calls set_secret_value when a new value is provided."""
    sv, _set, _get, _del = vault
    await sv.create_secret(_sample_record(), "original_value")

    now = datetime.now(UTC)
    updated_record = SecretRecord(
        name="test_token",
        type=SecretType.BEARER,
        target_hosts=["api.example.com", "new.example.com"],
        created_at=now,
        updated_at=now,
    )
    result = await sv.update_secret("test_token", updated_record, value="new_value")

    assert result is not None
    assert result["type"] == "bearer"
    # set_secret_value called once for create, once for update
    assert _set.call_count == 2
    assert _set.call_args_list[-1][0] == ("test_token", "new_value")


@pytest.mark.asyncio
async def test_issue_827_update_secret_no_value_skips_keyring(vault):
    """update_secret with value=None must not call set_secret_value again."""
    sv, _set, _get, _del = vault
    await sv.create_secret(_sample_record(), "original_value")

    now = datetime.now(UTC)
    updated_record = SecretRecord(
        name="test_token",
        type=SecretType.GENERIC,
        target_hosts=["api.example.com"],
        created_at=now,
        updated_at=now,
    )
    await sv.update_secret("test_token", updated_record, value=None)
    # Only the initial create should have called set_secret_value
    assert _set.call_count == 1


def test_issue_1134_resolve_credentials_shim_removed():
    """Issue #1134: resolve_credentials() legacy shim is removed from SecretsVault."""
    from ..credential_vault import SecretsVault
    assert not hasattr(SecretsVault, "resolve_credentials"), (
        "resolve_credentials() shim must be removed in #1134"
    )


@pytest.mark.asyncio
async def test_issue_827_credential_vault_alias():
    """CredentialVault alias must point to SecretsVault."""
    from ..credential_vault import CredentialVault
    assert CredentialVault is SecretsVault
