"""
Unit tests for issue #1051 — per-session credential injection via proxy.

Tests:
1. SessionConfig credential field round-trip (set → to_dict → from_dict)
2. SessionInfo backward compat (missing docker_proxy_credentials defaults to None)
3. SessionConfig → SessionInfo propagation via SessionManager.create_session()
4. docker_utils.resolve_docker_cli_path() with proxy_credentials_file sets env var
5. docker_utils.resolve_docker_cli_path() with proxy_credentials_file=None omits env var
6. addon.py credential matching (exact host, subdomain, no match)
7. addon.py header stripping and injection
8. addon.py access log includes credential_used name, never value
9. addon.py graceful behavior when credentials.json is absent
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.docker_utils import resolve_docker_cli_path
from src.session_config import SessionConfig

# ---------------------------------------------------------------------------
# 1. SessionConfig credential field round-trip
# ---------------------------------------------------------------------------

def test_session_config_credentials_round_trip():
    """docker_proxy_credentials survives Pydantic validation and serialisation."""
    creds = [
        {"host_pattern": "api.anthropic.com", "header": "x-api-key", "value": "sk-ant-test", "name": "anthropic_key"},
    ]
    config = SessionConfig(docker_proxy_credentials=creds)
    assert config.docker_proxy_credentials == creds

    # Pydantic .model_dump() round-trip
    data = config.model_dump()
    restored = SessionConfig(**data)
    assert restored.docker_proxy_credentials == creds


def test_session_config_credentials_default_none():
    """docker_proxy_credentials defaults to None when not supplied."""
    config = SessionConfig()
    assert config.docker_proxy_credentials is None


# ---------------------------------------------------------------------------
# 2. SessionInfo backward compat
# ---------------------------------------------------------------------------

def test_session_info_backward_compat_no_credentials():
    """SessionInfo.from_dict without docker_proxy_credentials defaults to None."""
    from datetime import UTC, datetime

    from src.session_manager import SessionInfo, SessionState

    data = {
        "session_id": "back-compat-creds-test",
        "state": SessionState.CREATED.value,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        # docker_proxy_credentials intentionally absent
    }
    info = SessionInfo.from_dict(data)
    assert info.docker_proxy_credentials is None


# ---------------------------------------------------------------------------
# 3. SessionConfig → SessionInfo propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_config_credentials_propagation(tmp_path):
    """docker_proxy_credentials propagates from SessionConfig to SessionInfo."""
    from src.session_manager import SessionManager

    sm = SessionManager(data_dir=tmp_path)
    await sm.initialize()

    creds = [
        {"host_pattern": "api.github.com", "header": "Authorization", "value": "Bearer ghp_test", "name": "github_token"},
    ]
    config = SessionConfig(docker_proxy_credentials=creds)
    await sm.create_session("creds-session-1", config)

    info = await sm.get_session_info("creds-session-1")
    assert info.docker_proxy_credentials == creds

    # Verify to_dict/from_dict round-trip persists the field
    d = info.to_dict()
    assert d["docker_proxy_credentials"] == creds

    from src.session_manager import SessionInfo
    restored = SessionInfo.from_dict(d)
    assert restored.docker_proxy_credentials == creds


# ---------------------------------------------------------------------------
# 4. docker_utils env var — credentials file set
# ---------------------------------------------------------------------------

def test_resolve_docker_cli_path_credentials_file_set(tmp_path):
    """resolve_docker_cli_path with proxy_credentials_file sets CLAUDE_DOCKER_PROXY_CREDS_FILE."""
    creds_file = str(tmp_path / "credentials.json")
    _, env = resolve_docker_cli_path(proxy_credentials_file=creds_file)
    assert env.get("CLAUDE_DOCKER_PROXY_CREDS_FILE") == creds_file


# ---------------------------------------------------------------------------
# 5. docker_utils env var — credentials file None
# ---------------------------------------------------------------------------

def test_resolve_docker_cli_path_credentials_file_none():
    """resolve_docker_cli_path with proxy_credentials_file=None omits the env var."""
    _, env = resolve_docker_cli_path(proxy_credentials_file=None)
    assert "CLAUDE_DOCKER_PROXY_CREDS_FILE" not in env


# ---------------------------------------------------------------------------
# Helper: build a DomainFilter with credentials loaded from a dict (no Docker)
# ---------------------------------------------------------------------------

def _make_filter_with_credentials(creds: list[dict]) -> "DomainFilter":  # noqa: F821
    """Instantiate DomainFilter and populate credentials without touching the filesystem."""
    import sys
    import types

    # Minimal mitmproxy stubs so addon.py can be imported without the real package
    if "mitmproxy" not in sys.modules:
        mitmproxy_pkg = types.ModuleType("mitmproxy")
        mitmproxy_http = types.ModuleType("mitmproxy.http")
        mitmproxy_ctx = types.ModuleType("mitmproxy.ctx")

        class _Response:
            status_code = 200
            content = b""

            @staticmethod
            def make(status, body, headers):
                r = _Response()
                r.status_code = status
                r.content = body.encode() if isinstance(body, str) else body
                return r

        class _HTTPFlow:
            def __init__(self, host):
                self.request = MagicMock()
                self.request.pretty_host = host
                self.request.path = "/test"
                self.request.method = "GET"
                self.request.scheme = "https"
                self.request.port = 443
                self.request.headers = {}
                self.response = None
                self.client_conn = MagicMock()
                self.client_conn.peername = ("127.0.0.1", 12345)
                self.metadata = {}

        mitmproxy_http.HTTPFlow = _HTTPFlow
        mitmproxy_http.Response = _Response
        mitmproxy_ctx.log = MagicMock()

        sys.modules["mitmproxy"] = mitmproxy_pkg
        sys.modules["mitmproxy.http"] = mitmproxy_http
        sys.modules["mitmproxy.ctx"] = mitmproxy_ctx

    # Force reload so the stubs are used
    if "src.docker.proxy.addon" in sys.modules:
        del sys.modules["src.docker.proxy.addon"]

    from src.docker.proxy import addon as addon_mod

    f = addon_mod.DomainFilter.__new__(addon_mod.DomainFilter)
    f.allowed_domains = {"api.github.com", "api.anthropic.com"}
    f.credentials = {
        entry["host_pattern"]: {
            "header": entry["header"],
            "value": entry["value"],
            "name": entry["name"],
        }
        for entry in creds
    }
    f.session_id = "test-session"
    f._log_file = None
    f.logger = MagicMock()
    return f


# ---------------------------------------------------------------------------
# 6. addon.py credential matching
# ---------------------------------------------------------------------------

def test_addon_credential_match_exact():
    """Exact host_pattern matches the host."""
    creds = [{"host_pattern": "api.github.com", "header": "Authorization", "value": "Bearer x", "name": "gh"}]
    f = _make_filter_with_credentials(creds)
    result = f._match_credential("api.github.com")
    assert result is not None
    assert result["name"] == "gh"


def test_addon_credential_match_subdomain():
    """Subdomain of host_pattern is matched (same logic as _is_allowed)."""
    creds = [{"host_pattern": "github.com", "header": "Authorization", "value": "Bearer x", "name": "gh"}]
    f = _make_filter_with_credentials(creds)
    result = f._match_credential("api.github.com")
    assert result is not None
    assert result["name"] == "gh"


def test_addon_credential_no_match():
    """Unrelated host returns None (no credential)."""
    creds = [{"host_pattern": "api.github.com", "header": "Authorization", "value": "Bearer x", "name": "gh"}]
    f = _make_filter_with_credentials(creds)
    result = f._match_credential("api.openai.com")
    assert result is None


# ---------------------------------------------------------------------------
# 7. addon.py header stripping and injection
# ---------------------------------------------------------------------------

def test_addon_inject_credentials_strips_and_injects():
    """_inject_credentials strips existing header and injects real value."""
    import sys

    from src.docker.proxy import addon as addon_mod  # noqa: F401 — may be already imported

    creds = [{"host_pattern": "api.anthropic.com", "header": "x-api-key", "value": "sk-ant-real", "name": "anthropic_key"}]
    f = _make_filter_with_credentials(creds)

    flow = sys.modules["mitmproxy.http"].HTTPFlow("api.anthropic.com")
    # Agent sent a fake key
    flow.request.headers = {"x-api-key": "sk-ant-fake"}

    name = f._inject_credentials(flow)
    assert name == "anthropic_key"
    assert flow.request.headers.get("x-api-key") == "sk-ant-real"


def test_addon_inject_credentials_no_match_leaves_headers():
    """_inject_credentials returns None and does not touch headers for unmatched host."""
    import sys

    from src.docker.proxy import addon as addon_mod  # noqa: F401

    creds = [{"host_pattern": "api.anthropic.com", "header": "x-api-key", "value": "sk-ant-real", "name": "anthropic_key"}]
    f = _make_filter_with_credentials(creds)

    flow = sys.modules["mitmproxy.http"].HTTPFlow("api.openai.com")
    flow.request.headers = {"Authorization": "Bearer openai-key"}

    name = f._inject_credentials(flow)
    assert name is None
    assert flow.request.headers.get("Authorization") == "Bearer openai-key"


# ---------------------------------------------------------------------------
# 8. addon.py access log includes credential_used name, never value
# ---------------------------------------------------------------------------

def test_addon_access_log_credential_name_not_value(tmp_path):
    """_write_access_log records credential name in credential_used, never the secret value."""
    import sys

    from src.docker.proxy import addon as addon_mod  # noqa: F401

    creds = [{"host_pattern": "api.github.com", "header": "Authorization", "value": "Bearer secret-ghp", "name": "github_token"}]
    f = _make_filter_with_credentials(creds)

    log_file = tmp_path / "access.log"
    f._log_file = log_file.open("a", buffering=1)

    flow = sys.modules["mitmproxy.http"].HTTPFlow("api.github.com")
    flow.response = MagicMock()
    flow.response.status_code = 200
    flow.response.content = b"ok"

    f._write_access_log(flow, allowed=True, credential_used="github_token")
    f._log_file.flush()
    f._log_file.close()

    entry = json.loads(log_file.read_text().strip())
    assert entry["credential_used"] == "github_token"
    assert "secret-ghp" not in log_file.read_text()


# ---------------------------------------------------------------------------
# 9. addon.py graceful no-op when credentials.json is absent
# ---------------------------------------------------------------------------

def test_addon_load_credentials_no_file():
    """_load_credentials is a no-op when /etc/proxy/credentials.json does not exist."""
    creds = []
    f = _make_filter_with_credentials(creds)
    # Credentials dict should remain empty (no exception raised)
    with patch("src.docker.proxy.addon.Path") as mock_path_cls:
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_path_cls.return_value = mock_path
        f._load_credentials()
    # credentials unchanged (empty)
    assert f.credentials == {}
