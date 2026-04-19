"""
Unit tests for issue #1051 / #1053 — per-session credential injection via proxy.

Architecture: vault-based placeholder injection.
- Credentials are stored in CredentialVault by name (issue #1053).
- Each credential defines a delivery (how placeholder reaches agent container)
  and injection (what proxy watches for in outgoing request headers).
- The proxy replaces placeholder values in headers with real credentials.
- Unauthenticated calls (no placeholder in headers) are untouched.

Tests:
1.  SessionConfig credential_names field round-trip (issue #1053)
2.  SessionConfig credential_names default to None
3.  SessionInfo backward compat (missing docker_proxy_credential_names defaults to None)
4.  SessionConfig → SessionInfo propagation via SessionManager.create_session()
5.  docker_utils with proxy_credentials_file sets CLAUDE_DOCKER_PROXY_CREDS_FILE
6.  docker_utils with proxy_credentials_file=None omits env var
7.  docker_utils with delivery_env_file sets CLAUDE_DOCKER_DELIVERY_ENV_FILE
8.  docker_utils with delivery_env_file=None omits env var
9.  addon.py: no placeholder in headers → headers unchanged (no injection)
10. addon.py: correct placeholder in Authorization header → replaced with real value
11. addon.py: different value in header (not placeholder) → headers unchanged
12. addon.py: placeholder on wrong host (optional guard) → not injected
13. addon.py: access log records credential name, never real_value
14. addon.py: graceful no-op when credentials.json is absent
"""

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from src.docker_utils import resolve_docker_cli_path
from src.session_config import SessionConfig

# ---------------------------------------------------------------------------
# Shared credential fixture (new schema)
# ---------------------------------------------------------------------------

def _make_cred_entry(
    name: str = "github_token",
    host_pattern: str = "api.github.com",
    delivery_type: str = "env",
    delivery_var: str = "GH_TOKEN",
    header: str = "Authorization",
    fmt: str = "Bearer {value}",
    real_value: str = "ghp_real_token",
    placeholder: str = "PH_GITHUB_TOKEN_abcd1234",
) -> dict:
    return {
        "name": name,
        "host_pattern": host_pattern,
        "delivery": {"type": delivery_type, "var": delivery_var},
        "injection": {"header": header, "format": fmt, "real_value": real_value},
        # 'placeholder' is generated at runtime by session_coordinator, but tests
        # may supply it directly when constructing sidecar entries for addon tests.
        "_placeholder": placeholder,
    }


# ---------------------------------------------------------------------------
# 1. SessionConfig credential_names field round-trip (issue #1053)
# ---------------------------------------------------------------------------

def test_session_config_credentials_round_trip():
    """docker_proxy_credential_names survives Pydantic validation and serialisation."""
    config = SessionConfig(docker_proxy_credential_names=["github_token", "npm_token"])
    assert config.docker_proxy_credential_names == ["github_token", "npm_token"]

    data = config.model_dump()
    restored = SessionConfig(**data)
    assert restored.docker_proxy_credential_names == ["github_token", "npm_token"]


# ---------------------------------------------------------------------------
# 2. SessionConfig credentials default to None
# ---------------------------------------------------------------------------

def test_session_config_credentials_default_none():
    """docker_proxy_credential_names defaults to None when not supplied."""
    config = SessionConfig()
    assert config.docker_proxy_credential_names is None


# ---------------------------------------------------------------------------
# 3. SessionInfo backward compat — old docker_proxy_credentials field silently dropped
# ---------------------------------------------------------------------------

def test_session_info_backward_compat_no_credentials():
    """SessionInfo.from_dict without docker_proxy_credential_names defaults to None."""
    from datetime import UTC, datetime

    from src.session_manager import SessionInfo, SessionState

    data = {
        "session_id": "back-compat-creds-test",
        "state": SessionState.CREATED.value,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        # docker_proxy_credential_names intentionally absent
    }
    info = SessionInfo.from_dict(data)
    assert info.docker_proxy_credential_names is None


# ---------------------------------------------------------------------------
# 4. SessionConfig → SessionInfo propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_config_credentials_propagation(tmp_path):
    """docker_proxy_credential_names propagates from SessionConfig to SessionInfo."""
    from src.session_manager import SessionInfo, SessionManager

    sm = SessionManager(data_dir=tmp_path)
    await sm.initialize()

    config = SessionConfig(docker_proxy_credential_names=["github_token"])
    await sm.create_session("creds-session-1", config)

    info = await sm.get_session_info("creds-session-1")
    assert info.docker_proxy_credential_names == ["github_token"]

    d = info.to_dict()
    assert d["docker_proxy_credential_names"] == ["github_token"]

    restored = SessionInfo.from_dict(d)
    assert restored.docker_proxy_credential_names == ["github_token"]


# ---------------------------------------------------------------------------
# 5-8. docker_utils env var logic
# ---------------------------------------------------------------------------

def test_resolve_docker_cli_path_credentials_file_set(tmp_path):
    """proxy_credentials_file sets CLAUDE_DOCKER_PROXY_CREDS_FILE."""
    creds_file = str(tmp_path / "credentials.json")
    _, env = resolve_docker_cli_path(proxy_credentials_file=creds_file)
    assert env.get("CLAUDE_DOCKER_PROXY_CREDS_FILE") == creds_file


def test_resolve_docker_cli_path_credentials_file_none():
    """proxy_credentials_file=None omits CLAUDE_DOCKER_PROXY_CREDS_FILE."""
    _, env = resolve_docker_cli_path(proxy_credentials_file=None)
    assert "CLAUDE_DOCKER_PROXY_CREDS_FILE" not in env


def test_resolve_docker_cli_path_delivery_env_file_set(tmp_path):
    """delivery_env_file sets CLAUDE_DOCKER_DELIVERY_ENV_FILE."""
    env_file = str(tmp_path / "delivery_envs.json")
    _, env = resolve_docker_cli_path(delivery_env_file=env_file)
    assert env.get("CLAUDE_DOCKER_DELIVERY_ENV_FILE") == env_file


def test_resolve_docker_cli_path_delivery_env_file_none():
    """delivery_env_file=None omits CLAUDE_DOCKER_DELIVERY_ENV_FILE."""
    _, env = resolve_docker_cli_path(delivery_env_file=None)
    assert "CLAUDE_DOCKER_DELIVERY_ENV_FILE" not in env


# ---------------------------------------------------------------------------
# Addon test helpers — minimal mitmproxy stubs
# ---------------------------------------------------------------------------

def _ensure_mitmproxy_stubs():
    """Install minimal mitmproxy stubs if the real package is absent."""
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

    # Force reload so stubs are used on first import
    if "src.docker.proxy.addon" in sys.modules:
        del sys.modules["src.docker.proxy.addon"]


def _make_filter_with_placeholder_map(entries: list[dict]):
    """Build a DomainFilter with a pre-populated placeholder_map.

    Each entry: {placeholder, name, host_pattern, header, format, real_value}.
    """
    _ensure_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    f = addon_mod.DomainFilter.__new__(addon_mod.DomainFilter)
    f.allowed_domains = {"api.github.com", "api.anthropic.com"}
    f.placeholder_map = {
        entry["placeholder"]: {
            "name": entry["name"],
            "host_pattern": entry.get("host_pattern"),
            "header": entry["header"],
            "format": entry.get("format", "{value}"),
            "real_value": entry["real_value"],
        }
        for entry in entries
    }
    f.session_id = "test-session"
    f._log_file = None
    f.logger = MagicMock()
    return f


def _flow(host: str, headers: dict | None = None):
    """Create a minimal HTTPFlow stub."""
    _ensure_mitmproxy_stubs()
    flow = sys.modules["mitmproxy.http"].HTTPFlow(host)
    flow.request.headers = dict(headers or {})
    return flow


# ---------------------------------------------------------------------------
# 9. No placeholder in headers → headers unchanged
# ---------------------------------------------------------------------------

def test_addon_no_placeholder_leaves_headers_unchanged():
    """Unauthenticated request to api.github.com — no injection."""
    f = _make_filter_with_placeholder_map([{
        "placeholder": "PH_GITHUB_TOKEN_abcd1234",
        "name": "github_token",
        "host_pattern": "api.github.com",
        "header": "Authorization",
        "format": "Bearer {value}",
        "real_value": "ghp_real_token",
    }])
    flow = _flow("api.github.com")  # no Authorization header at all
    name = f._inject_credentials(flow)
    assert name is None
    assert "Authorization" not in flow.request.headers


def test_addon_no_placeholder_with_headers_unchanged():
    """Request with some non-placeholder Authorization header → unchanged."""
    f = _make_filter_with_placeholder_map([{
        "placeholder": "PH_GITHUB_TOKEN_abcd1234",
        "name": "github_token",
        "host_pattern": "api.github.com",
        "header": "Authorization",
        "format": "Bearer {value}",
        "real_value": "ghp_real_token",
    }])
    flow = _flow("api.github.com", {"Authorization": "Bearer some_other_value"})
    name = f._inject_credentials(flow)
    assert name is None
    assert flow.request.headers["Authorization"] == "Bearer some_other_value"


# ---------------------------------------------------------------------------
# 10. Correct placeholder in header → replaced with real value
# ---------------------------------------------------------------------------

def test_addon_placeholder_in_header_injects_real_value():
    """Agent sends placeholder → proxy replaces with formatted real credential."""
    f = _make_filter_with_placeholder_map([{
        "placeholder": "PH_GITHUB_TOKEN_abcd1234",
        "name": "github_token",
        "host_pattern": "api.github.com",
        "header": "Authorization",
        "format": "Bearer {value}",
        "real_value": "ghp_real_token",
    }])
    flow = _flow("api.github.com", {"Authorization": "Bearer PH_GITHUB_TOKEN_abcd1234"})
    name = f._inject_credentials(flow)
    assert name == "github_token"
    assert flow.request.headers["Authorization"] == "Bearer ghp_real_token"


def test_addon_placeholder_without_format_prefix():
    """Placeholder as bare header value (format='{value}') → replaced with raw real_value."""
    f = _make_filter_with_placeholder_map([{
        "placeholder": "PH_ANT_abcd1234",
        "name": "anthropic_key",
        "host_pattern": "api.anthropic.com",
        "header": "x-api-key",
        "format": "{value}",
        "real_value": "sk-ant-real",
    }])
    flow = _flow("api.anthropic.com", {"x-api-key": "PH_ANT_abcd1234"})
    name = f._inject_credentials(flow)
    assert name == "anthropic_key"
    assert flow.request.headers["x-api-key"] == "sk-ant-real"


# ---------------------------------------------------------------------------
# 11. Different (non-placeholder) value → unchanged
# ---------------------------------------------------------------------------

def test_addon_wrong_value_leaves_headers_unchanged():
    """Request with a valid-looking but non-placeholder Bearer token → unchanged."""
    f = _make_filter_with_placeholder_map([{
        "placeholder": "PH_GITHUB_TOKEN_abcd1234",
        "name": "github_token",
        "host_pattern": "api.github.com",
        "header": "Authorization",
        "format": "Bearer {value}",
        "real_value": "ghp_real_token",
    }])
    flow = _flow("api.github.com", {"Authorization": "Bearer ghp_totally_different"})
    name = f._inject_credentials(flow)
    assert name is None
    assert flow.request.headers["Authorization"] == "Bearer ghp_totally_different"


# ---------------------------------------------------------------------------
# 12. Placeholder on wrong host → not injected (optional guard)
# ---------------------------------------------------------------------------

def test_addon_placeholder_on_wrong_host_not_injected():
    """Placeholder seen on an unexpected host → guard rejects injection."""
    f = _make_filter_with_placeholder_map([{
        "placeholder": "PH_GITHUB_TOKEN_abcd1234",
        "name": "github_token",
        "host_pattern": "api.github.com",
        "header": "Authorization",
        "format": "Bearer {value}",
        "real_value": "ghp_real_token",
    }])
    # Add evil.com to allowed domains so the request isn't blocked first
    f.allowed_domains.add("evil.com")
    flow = _flow("evil.com", {"Authorization": "Bearer PH_GITHUB_TOKEN_abcd1234"})
    name = f._inject_credentials(flow)
    assert name is None
    # Header should remain as-is (the placeholder is NOT replaced on the wrong host)
    assert flow.request.headers["Authorization"] == "Bearer PH_GITHUB_TOKEN_abcd1234"


# ---------------------------------------------------------------------------
# 13. Access log records credential name, never real_value
# ---------------------------------------------------------------------------

def test_addon_access_log_credential_name_not_value(tmp_path):
    """_write_access_log records credential_used as name string, real_value never logged."""
    _ensure_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    f = addon_mod.DomainFilter.__new__(addon_mod.DomainFilter)
    f.session_id = "log-test"
    f.allowed_domains = set()
    f.placeholder_map = {}
    f.logger = MagicMock()

    log_file = tmp_path / "access.log"
    f._log_file = log_file.open("a", buffering=1)

    flow = _flow("api.github.com")
    flow.response = MagicMock()
    flow.response.status_code = 200
    flow.response.content = b"ok"

    real_secret = "ghp_super_secret_real_token"
    f._write_access_log(flow, allowed=True, credential_used="github_token")
    f._log_file.flush()
    f._log_file.close()

    log_content = log_file.read_text()
    entry = json.loads(log_content.strip())
    assert entry["credential_used"] == "github_token"
    assert real_secret not in log_content


# ---------------------------------------------------------------------------
# 14. Graceful no-op when credentials.json absent
# ---------------------------------------------------------------------------

def test_addon_load_credentials_no_file():
    """_load_credentials is a no-op and leaves placeholder_map empty when file absent."""
    _ensure_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    f = addon_mod.DomainFilter.__new__(addon_mod.DomainFilter)
    f.allowed_domains = set()
    f.placeholder_map = {}
    f.session_id = "no-file-test"
    f._log_file = None
    f.logger = MagicMock()

    with patch("src.docker.proxy.addon.Path") as mock_path_cls:
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_path_cls.return_value = mock_path
        f._load_credentials()

    assert f.placeholder_map == {}
