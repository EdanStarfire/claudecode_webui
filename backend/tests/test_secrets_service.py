"""Unit tests for SecretsService (issue #2189 AC5, router-boundary PR B).

SecretsService is constructed directly with fake/stub collaborators — no
SessionCoordinator involved — per the issue's own T2 test scenario ("unit
tests construct each extracted module without the coordinator").

Tests moved verbatim from test_application_service.py where they previously
exercised this logic through ApplicationService's now-removed direct bodies.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.secrets_service import SecretsService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_credential_vault():
    vault = MagicMock()
    vault.list_secrets = AsyncMock(return_value=[])
    vault.resolve_secrets_for_assignment = AsyncMock(return_value=[])
    return vault


@pytest.fixture
def service(mock_credential_vault):
    return SecretsService(
        credential_vault=mock_credential_vault,
        vault_refresh_manager=MagicMock(),
        session_manager=MagicMock(),
        template_manager=MagicMock(),
        profile_manager=MagicMock(),
        mcp_config_manager=MagicMock(),
    )


def _make_session_with_secrets(assigned_secrets=None, secret_placeholders=None):
    s = MagicMock()
    s.config = {"assigned_secrets": assigned_secrets} if assigned_secrets is not None else {}
    s.secret_placeholders = secret_placeholders
    return s


def _make_session_post_1230(assigned_secrets=None, secret_placeholders=None):
    s = MagicMock()
    s.config = {"assigned_secrets": assigned_secrets} if assigned_secrets is not None else {}
    s.secret_placeholders = secret_placeholders or {}
    return s


# ---------------------------------------------------------------------------
# Secrets / Issue #1219
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1219_resolves_from_placeholder_when_assigned_empty(service, mock_credential_vault):
    session = _make_session_with_secrets(
        assigned_secrets=None,
        secret_placeholders={"CC_SECRET_tok_abc": "claudecode_token"},
    )
    service.session_manager.get_session_info = AsyncMock(return_value=session)
    mock_credential_vault.resolve_secrets_for_assignment = AsyncMock(
        return_value=[{"name": "claudecode_token", "value": "tok123"}]
    )

    # Verify production read path returns the expected names before the async call
    assert service._resolve_writeable_secret_names(session) == ["claudecode_token"]

    result = await service.resolve_secrets_for_session("s1")

    mock_credential_vault.resolve_secrets_for_assignment.assert_called_once_with(
        ["claudecode_token"]
    )
    assert any(s["name"] == "claudecode_token" for s in result["secrets"])


@pytest.mark.asyncio
async def test_issue_1219_no_duplicates_when_both_assigned_and_placeholder_set(
    service, mock_credential_vault
):
    session = _make_session_with_secrets(
        assigned_secrets=["claudecode_token"],
        secret_placeholders={"CC_SECRET_tok_abc": "claudecode_token"},
    )
    service.session_manager.get_session_info = AsyncMock(return_value=session)
    mock_credential_vault.resolve_secrets_for_assignment = AsyncMock(
        return_value=[{"name": "claudecode_token", "value": "tok123"}]
    )

    await service.resolve_secrets_for_session("s1")

    names_passed = mock_credential_vault.resolve_secrets_for_assignment.call_args[0][0]
    assert names_passed.count("claudecode_token") == 1


# ---------------------------------------------------------------------------
# Secrets / Issue #1241 — proxy write-back fix for template-linked sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_1241_patch_template_derived_secret_succeeds(service, mock_credential_vault):
    session = _make_session_post_1230(
        assigned_secrets=None,
        secret_placeholders={"CC_SECRET_x": "claudecode_token"},
    )
    service.session_manager.get_session_info = AsyncMock(return_value=session)
    service.update_secret = AsyncMock(return_value={"name": "claudecode_token"})

    result = await service.update_secret_for_session("s1", "claudecode_token", value="new_tok")

    service.update_secret.assert_called_once_with(name="claudecode_token", value="new_tok")
    assert result == {"name": "claudecode_token"}


@pytest.mark.asyncio
async def test_issue_1241_patch_profile_derived_secret_succeeds(service, mock_credential_vault):
    # Profile-derived names flow through secret_placeholders identically to template-derived ones.
    session = _make_session_post_1230(
        assigned_secrets=None,
        secret_placeholders={"CC_SECRET_profile": "profile_token"},
    )
    service.session_manager.get_session_info = AsyncMock(return_value=session)
    service.update_secret = AsyncMock(return_value={"name": "profile_token"})

    result = await service.update_secret_for_session("s1", "profile_token", value="refreshed")

    service.update_secret.assert_called_once_with(name="profile_token", value="refreshed")
    assert result == {"name": "profile_token"}


@pytest.mark.asyncio
async def test_issue_1241_patch_unrelated_secret_rejected(service, mock_credential_vault):
    session = _make_session_post_1230(
        assigned_secrets=None,
        secret_placeholders={"CC_SECRET_x": "claudecode_token"},
    )
    service.session_manager.get_session_info = AsyncMock(return_value=session)

    with pytest.raises(PermissionError):
        await service.update_secret_for_session("s1", "some_other_secret")


@pytest.mark.asyncio
async def test_issue_1241_patch_transitive_sibling_succeeds(service, mock_credential_vault):
    session = _make_session_post_1230(
        assigned_secrets=None,
        secret_placeholders={"CC_SECRET_tok": "oauth_token"},
    )
    service.session_manager.get_session_info = AsyncMock(return_value=session)
    mock_credential_vault.list_secrets = AsyncMock(
        return_value=[
            {
                "name": "oauth_token",
                "refresh": {"refresh_token_secret_name": "oauth_refresh"},
            }
        ]
    )
    service.update_secret = AsyncMock(return_value={"name": "oauth_refresh"})

    result = await service.update_secret_for_session("s1", "oauth_refresh", value="new_refresh")

    service.update_secret.assert_called_once_with(name="oauth_refresh", value="new_refresh")
    assert result == {"name": "oauth_refresh"}


@pytest.mark.asyncio
async def test_issue_1241_patch_session_overridden_secret_succeeds(service, mock_credential_vault):
    session = _make_session_post_1230(
        assigned_secrets=["my_secret"],
        secret_placeholders=None,
    )
    service.session_manager.get_session_info = AsyncMock(return_value=session)
    service.update_secret = AsyncMock(return_value={"name": "my_secret"})

    result = await service.update_secret_for_session("s1", "my_secret", value="direct_val")

    service.update_secret.assert_called_once_with(name="my_secret", value="direct_val")
    assert result == {"name": "my_secret"}


# ---------------------------------------------------------------------------
# list_secrets (issue #1387 health field, issue #1772 usage breakdown)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_secrets_enriches_oauth2_with_health(service, mock_credential_vault):
    mock_credential_vault.list_secrets = AsyncMock(
        return_value=[{"name": "tok", "type": "oauth2", "refresh": None}]
    )
    service.session_manager.list_sessions = AsyncMock(return_value=[])
    service.template_manager.list_templates = AsyncMock(return_value=[])
    service.profile_manager.list_profiles = AsyncMock(return_value=[])
    service.mcp_config_manager.list_configs = AsyncMock(return_value=[])

    result = await service.list_secrets()

    assert result["secrets"][0]["health"] == "valid"
    assert "usage" in result["secrets"][0]


# ---------------------------------------------------------------------------
# refresh_secret lock acquisition (issue #1387)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_secret_returns_none_when_secret_not_found(service, mock_credential_vault):
    mock_credential_vault.get_secret = AsyncMock(return_value=None)

    class _FakeLockCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    service.vault_refresh_manager.get_lock = MagicMock(return_value=_FakeLockCtx())

    result = await service.refresh_secret("missing")

    assert result is None
