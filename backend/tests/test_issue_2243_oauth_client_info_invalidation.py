"""Tests for stale stored OAuth client info invalidation (issue #2243).

Two independent, non-overlapping mechanisms, each tested in isolation:
- AC1 (UC1): McpConfigManager.update_config() invalidates stored client info when
  oauth_client_id/oauth_client_secret changes or is explicitly cleared.
- AC2 (UC2): OAuthFlowManager.start_flow()'s cached-read branch discards stored
  client info whose origin no longer matches what discovery would select now.
"""

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.auth import OAuthClientInformationFull

from backend.mcp_config_manager import McpConfigManager, McpServerType
from backend.oauth_manager import FernetTokenStore, OAuthFlowManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client_info(client_id: str = "cid") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=["http://localhost/oauth/callback"],  # type: ignore[arg-type]
        token_endpoint_auth_method="none",
    )


def _make_asm_mock(
    registration_endpoint: str | None,
    client_id_metadata_document_supported: bool = False,
) -> MagicMock:
    asm = MagicMock()
    asm.token_endpoint = "https://auth.example.com/token"
    asm.authorization_endpoint = "https://auth.example.com/authorize"
    asm.registration_endpoint = registration_endpoint
    asm.client_id_metadata_document_supported = client_id_metadata_document_supported
    asm.scopes_supported = None
    return asm


def _make_http_client_class() -> MagicMock:
    """Patched httpx2.AsyncClient class whose instances respond with 200 to any send()."""
    http_resp = MagicMock()
    http_resp.status_code = 200

    instance = AsyncMock()
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    instance.send = AsyncMock(return_value=http_resp)

    cls_mock = MagicMock()
    cls_mock.return_value = instance
    return cls_mock


async def _http_manager_config(manager: McpConfigManager, name: str = "server", **kwargs):
    return await manager.create_config(
        name=name,
        server_type=McpServerType.HTTP,
        url="https://mcp.example.com/v1/mcp",
        shared_connection=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# UC1 — AC1 only: invalidate-on-config-change (no Connect involved)
# ---------------------------------------------------------------------------


async def test_uc1_update_config_clearing_oauth_client_id_invalidates_before_any_connect(
    tmp_path: Path,
):
    """Clearing oauth_client_id invalidates stored client info immediately —
    before start_flow() ever runs — proving AC1's hook (not AC2's mismatch check)
    is what cleared it."""
    oauth_manager = OAuthFlowManager(tmp_path)
    config_manager = McpConfigManager(tmp_path)
    cfg = await _http_manager_config(config_manager, oauth_client_id="old-client")

    store = oauth_manager.get_token_store(cfg.id)
    await store.set_client_info(_make_client_info("old-client"), origin="pre_registered")
    assert await store.get_client_info() is not None

    await config_manager.update_config(
        cfg.id, oauth_client_id=None, oauth_manager=oauth_manager
    )

    # Invalidated by AC1 alone — no start_flow() call has happened yet.
    assert await store.get_client_info() is None
    assert store.get_client_info_origin() is None

    # A subsequent Connect re-discovers fresh (DCR here, since discovery below
    # advertises no registration_endpoint — path-derived fallback is used).
    not_found = MagicMock(status_code=404)
    dcr_resp = MagicMock(status_code=201)

    async def _dcr_aread():
        return json.dumps(
            {
                "client_id": "fresh-dcr-client",
                "redirect_uris": ["http://localhost/oauth/callback"],
                "token_endpoint_auth_method": "none",
            }
        ).encode()

    dcr_resp.aread = _dcr_aread

    client_instance = AsyncMock()
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    client_instance.send = AsyncMock(
        side_effect=[not_found, not_found, not_found, dcr_resp]
    )

    with patch("backend.oauth_manager.httpx2.AsyncClient", return_value=client_instance):
        await oauth_manager.start_flow(
            server_id=cfg.id,
            server_url="https://mcp.example.com/v1/mcp",
            redirect_uri="http://localhost/oauth/callback",
        )

    summary = oauth_manager.get_discovery_summary(cfg.id)
    assert summary["mechanism"] in ("dcr", "cimd")
    assert summary["mechanism"] != "cached"
    assert "origin" not in summary


# ---------------------------------------------------------------------------
# UC2 — AC2 only: mechanism-match check at Connect time (no config change)
# ---------------------------------------------------------------------------


async def test_uc2_start_flow_discards_stale_dcr_origin_when_server_now_cimd_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """A server that previously required DCR and now advertises CIMD-only support
    gets its stale dcr-origin client info discarded at Connect time — no config
    change involved, so AC1 could not have been responsible."""
    oauth_manager = OAuthFlowManager(tmp_path)
    server_id = "srv-cimd-upgrade"
    store = oauth_manager.get_token_store(server_id)
    await store.set_client_info(_make_client_info("old-dcr-client"), origin="dcr")

    mock_asm = _make_asm_mock(
        registration_endpoint=None, client_id_metadata_document_supported=True
    )

    with (
        patch(
            "backend.oauth_manager.handle_protected_resource_response",
            AsyncMock(return_value=None),
        ),
        patch.object(
            OAuthFlowManager, "_fetch_as_metadata", AsyncMock(return_value=(True, mock_asm, False))
        ),
        patch("backend.oauth_manager.httpx2.AsyncClient", _make_http_client_class()),
        caplog.at_level(logging.INFO, logger="backend.oauth_manager"),
    ):
        await oauth_manager.start_flow(
            server_id=server_id,
            server_url="https://mcp.example.com/v1/mcp",
            redirect_uri="http://localhost/oauth/callback",
            client_metadata_url="https://app.example.com/oauth/client-metadata.json",
        )

    summary = oauth_manager.get_discovery_summary(server_id)
    assert summary["mechanism"] == "cimd"
    assert "Discarding stored OAuth client info" in caplog.text

    # The store now holds the freshly-discovered CIMD client info, not the stale DCR one.
    new_info = await store.get_client_info()
    assert new_info is not None
    assert new_info.client_id != "old-dcr-client"
    assert store.get_client_info_origin() == "cimd"


# ---------------------------------------------------------------------------
# Supporting / regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fernet_store_client_info_origin_round_trip(tmp_path: Path):
    store = FernetTokenStore(tmp_path, "server1")
    assert store.get_client_info_origin() is None

    await store.set_client_info(_make_client_info(), origin="dcr")
    assert store.get_client_info_origin() == "dcr"
    assert await store.get_client_info() is not None

    store.clear_client_info()
    assert store.get_client_info_origin() is None
    assert await store.get_client_info() is None


async def test_legitimate_dcr_match_keeps_cached_and_does_not_re_register(tmp_path: Path):
    """A server that still requires DCR keeps its cached client info — the AC2
    mismatch check must not flag a legitimate dcr-to-dcr match as stale."""
    oauth_manager = OAuthFlowManager(tmp_path)
    server_id = "srv-still-dcr"
    store = oauth_manager.get_token_store(server_id)
    await store.set_client_info(_make_client_info("existing-dcr-client"), origin="dcr")

    mock_asm = _make_asm_mock(
        registration_endpoint="https://auth.example.com/register",
        client_id_metadata_document_supported=False,
    )

    with (
        patch(
            "backend.oauth_manager.handle_protected_resource_response",
            AsyncMock(return_value=None),
        ),
        patch.object(
            OAuthFlowManager, "_fetch_as_metadata", AsyncMock(return_value=(True, mock_asm, False))
        ),
        patch("backend.oauth_manager.httpx2.AsyncClient", _make_http_client_class()),
        patch(
            "backend.oauth_manager.handle_registration_response", AsyncMock()
        ) as mock_register,
        patch(
            "backend.oauth_manager.create_client_info_from_metadata_url", MagicMock()
        ) as mock_cimd,
    ):
        await oauth_manager.start_flow(
            server_id=server_id,
            server_url="https://mcp.example.com/v1/mcp",
            redirect_uri="http://localhost/oauth/callback",
        )

    summary = oauth_manager.get_discovery_summary(server_id)
    assert summary["mechanism"] == "cached"
    assert summary["origin"] == "dcr"
    mock_register.assert_not_called()
    mock_cimd.assert_not_called()

    # Stored client info is untouched (still the original client_id).
    unchanged_info = await store.get_client_info()
    assert unchanged_info.client_id == "existing-dcr-client"


@pytest.mark.asyncio
async def test_update_config_omitted_oauth_client_id_does_not_invalidate(tmp_path: Path):
    """Sparse update (oauth_client_id not present in the PATCH) must not invalidate."""
    oauth_manager = OAuthFlowManager(tmp_path)
    config_manager = McpConfigManager(tmp_path)
    cfg = await _http_manager_config(config_manager, oauth_client_id="cid")

    store = oauth_manager.get_token_store(cfg.id)
    await store.set_client_info(_make_client_info("cid"), origin="pre_registered")

    await config_manager.update_config(
        cfg.id, name="renamed-server", oauth_manager=oauth_manager
    )

    assert await store.get_client_info() is not None


@pytest.mark.asyncio
async def test_update_config_same_oauth_client_id_value_does_not_invalidate(tmp_path: Path):
    """Re-submitting the same oauth_client_id value (a no-op save) must not invalidate."""
    oauth_manager = OAuthFlowManager(tmp_path)
    config_manager = McpConfigManager(tmp_path)
    cfg = await _http_manager_config(config_manager, oauth_client_id="cid")

    store = oauth_manager.get_token_store(cfg.id)
    await store.set_client_info(_make_client_info("cid"), origin="pre_registered")

    await config_manager.update_config(
        cfg.id, oauth_client_id="cid", oauth_manager=oauth_manager
    )

    assert await store.get_client_info() is not None
