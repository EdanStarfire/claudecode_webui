"""
Unit tests for issue #1051 / #1053 / #827 — per-session credential injection via proxy.

Architecture: vault-based placeholder injection.
- Secrets are stored in SecretsVault by name (issue #827).
- Each secret defines assigned_secrets names that are resolved at session start.
- The proxy replaces placeholder values in headers with real credentials.
- Unauthenticated calls (no placeholder in headers) are untouched.

Tests:
1.  SessionConfig assigned_secrets field round-trip (issue #827)
2.  SessionConfig assigned_secrets default to None
3.  SessionInfo backward compat (missing assigned_secrets defaults to None)
4.  SessionConfig → SessionInfo propagation via SessionManager.create_session()
5.  docker_utils with delivery_envs dict sets CLAUDE_DOCKER_DELIVERY_ENVS as JSON string
6.  docker_utils with delivery_envs=None omits CLAUDE_DOCKER_DELIVERY_ENVS
9.  addon.py: no placeholder in headers → headers unchanged (no injection)
10. addon.py: correct placeholder in Authorization header → replaced with real value
11. addon.py: different value in header (not placeholder) → headers unchanged
12. addon.py: placeholder injected for any allowed host (no per-credential host guard)
13. addon.py: access log records credential name, never real_value
14. addon.py: graceful no-op when session token files are absent
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

def test_session_config_assigned_secrets_round_trip():
    """assigned_secrets survives Pydantic validation and serialisation."""
    config = SessionConfig(assigned_secrets=["github_token", "npm_token"])
    assert config.assigned_secrets == ["github_token", "npm_token"]

    data = config.model_dump()
    restored = SessionConfig(**data)
    assert restored.assigned_secrets == ["github_token", "npm_token"]


# ---------------------------------------------------------------------------
# 2. SessionConfig assigned_secrets default to None
# ---------------------------------------------------------------------------

def test_session_config_assigned_secrets_default_none():
    """assigned_secrets defaults to None when not supplied."""
    config = SessionConfig()
    assert config.assigned_secrets is None


# ---------------------------------------------------------------------------
# 3. SessionInfo backward compat — old docker_proxy_credential_names field silently dropped
# ---------------------------------------------------------------------------

def test_session_info_backward_compat_no_assigned_secrets():
    """SessionInfo.from_dict without assigned_secrets defaults to None."""
    from datetime import UTC, datetime

    from src.session_manager import SessionInfo, SessionState

    data = {
        "session_id": "back-compat-creds-test",
        "state": SessionState.CREATED.value,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        # assigned_secrets intentionally absent
    }
    info = SessionInfo.from_dict(data)
    assert info.assigned_secrets is None


# ---------------------------------------------------------------------------
# 4. SessionConfig → SessionInfo propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_config_assigned_secrets_propagation(tmp_path):
    """assigned_secrets propagates from SessionConfig to SessionInfo."""
    from src.session_manager import SessionInfo, SessionManager

    sm = SessionManager(data_dir=tmp_path)
    await sm.initialize()

    config = SessionConfig(assigned_secrets=["github_token"])
    await sm.create_session("creds-session-1", config)

    info = await sm.get_session_info("creds-session-1")
    assert info.assigned_secrets == ["github_token"]

    d = info.to_dict()
    assert d["assigned_secrets"] == ["github_token"]

    restored = SessionInfo.from_dict(d)
    assert restored.assigned_secrets == ["github_token"]


# ---------------------------------------------------------------------------
# 5-6. docker_utils env var logic (issue #1134: inline delivery_envs replaces file)
# ---------------------------------------------------------------------------

def test_resolve_docker_cli_path_delivery_envs_dict_set():
    """delivery_envs dict serialises to CLAUDE_DOCKER_DELIVERY_ENVS as JSON string."""
    envs = {"GH_TOKEN": "CC_SECRET_github_token_abcd1234", "NPM_TOKEN": "CC_SECRET_npm_token_ef567890"}
    _, env = resolve_docker_cli_path(delivery_envs=envs)
    assert "CLAUDE_DOCKER_DELIVERY_ENVS" in env
    assert json.loads(env["CLAUDE_DOCKER_DELIVERY_ENVS"]) == envs


def test_resolve_docker_cli_path_delivery_envs_none_omitted():
    """delivery_envs=None omits CLAUDE_DOCKER_DELIVERY_ENVS."""
    _, env = resolve_docker_cli_path(delivery_envs=None)
    assert "CLAUDE_DOCKER_DELIVERY_ENVS" not in env


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


def _make_addon_with_records(entries: list[dict]):
    """Build a ProxyAddon with pre-populated _records dict.

    Each entry: {placeholder, name, type, real_value, host_pattern (optional)}.
    Issue #1134: secrets are fetched via REST at load() — this helper pre-populates
    _records directly to avoid a live REST call in unit tests.
    """
    import asyncio as _asyncio

    _ensure_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    f = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)
    f.allowed_domains = {"api.github.com", "api.anthropic.com"}
    f._records = {
        entry["placeholder"]: {
            "name": entry["name"],
            "type": entry.get("type", "bearer"),
            "value": entry["real_value"],
            "target_hosts": [entry["host_pattern"]] if entry.get("host_pattern") else [],
        }
        for entry in entries
    }
    f._refresh_locks = {ph: _asyncio.Lock() for ph in f._records}
    f._session_id = "test-session"
    f._session_token = "test-token"
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
    f = _make_addon_with_records([{
        "placeholder": "CC_SECRET_github_token_abcd1234",
        "name": "github_token",
        "host_pattern": "api.github.com",
        "type": "bearer",
        "real_value": "ghp_real_token",
    }])
    flow = _flow("api.github.com")  # no Authorization header at all
    name = f._inject_credentials(flow)
    assert name is None
    assert "Authorization" not in flow.request.headers


def test_addon_no_placeholder_with_headers_unchanged():
    """Request with some non-placeholder Authorization header → unchanged."""
    f = _make_addon_with_records([{
        "placeholder": "CC_SECRET_github_token_abcd1234",
        "name": "github_token",
        "host_pattern": "api.github.com",
        "type": "bearer",
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
    """Agent sends placeholder in header → proxy replaces entire header with real credential."""
    f = _make_addon_with_records([{
        "placeholder": "CC_SECRET_github_token_abcd1234",
        "name": "github_token",
        "host_pattern": "api.github.com",
        "type": "bearer",
        "real_value": "ghp_real_token",
    }])
    flow = _flow("api.github.com", {"Authorization": "Bearer CC_SECRET_github_token_abcd1234"})
    name = f._inject_credentials(flow)
    assert name == "github_token"
    assert flow.request.headers["Authorization"] == "Bearer ghp_real_token"


def test_addon_placeholder_without_format_prefix():
    """Placeholder as bare header value (generic type) → replaced with raw real_value."""
    f = _make_addon_with_records([{
        "placeholder": "CC_SECRET_anthropic_key_abcd1234",
        "name": "anthropic_key",
        "host_pattern": "api.anthropic.com",
        "type": "generic",
        "real_value": "sk-ant-real",
    }])
    flow = _flow("api.anthropic.com", {"x-api-key": "CC_SECRET_anthropic_key_abcd1234"})
    name = f._inject_credentials(flow)
    assert name == "anthropic_key"
    assert flow.request.headers["x-api-key"] == "sk-ant-real"


# ---------------------------------------------------------------------------
# 11. Different (non-placeholder) value → unchanged
# ---------------------------------------------------------------------------

def test_addon_wrong_value_leaves_headers_unchanged():
    """Request with a valid-looking but non-placeholder Bearer token ��� unchanged."""
    f = _make_addon_with_records([{
        "placeholder": "CC_SECRET_github_token_abcd1234",
        "name": "github_token",
        "host_pattern": "api.github.com",
        "type": "bearer",
        "real_value": "ghp_real_token",
    }])
    flow = _flow("api.github.com", {"Authorization": "Bearer ghp_totally_different"})
    name = f._inject_credentials(flow)
    assert name is None
    assert flow.request.headers["Authorization"] == "Bearer ghp_totally_different"


# ---------------------------------------------------------------------------
# 12. No per-credential host guard — allowlist is the gate
# ---------------------------------------------------------------------------

def test_addon_placeholder_injected_for_any_allowed_host():
    """Issue #1134: per-credential host_pattern guard removed; allowlist is the security gate.

    _inject_credentials injects the placeholder for any request regardless of target_hosts
    on the record. The allowlist check in request() is the host security boundary.
    """
    f = _make_addon_with_records([{
        "placeholder": "CC_SECRET_github_token_abcd1234",
        "name": "github_token",
        "host_pattern": "api.github.com",
        "type": "bearer",
        "real_value": "ghp_real_token",
    }])
    f.allowed_domains.add("other.allowed.com")
    flow = _flow("other.allowed.com", {"Authorization": "Bearer CC_SECRET_github_token_abcd1234"})
    name = f._inject_credentials(flow)
    assert name == "github_token"
    assert flow.request.headers["Authorization"] == "Bearer ghp_real_token"


# ---------------------------------------------------------------------------
# 13. Access log records credential name, never real_value
# ---------------------------------------------------------------------------

def test_addon_access_log_credential_name_not_value(tmp_path):
    """_write_access_log records credential_used as name string, real_value never logged."""
    _ensure_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    f = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)
    f._session_id = "log-test"
    f.allowed_domains = set()
    f._records = {}
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
# 14. Graceful no-op when session token files absent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_addon_load_no_session_files_leaves_records_empty():
    """Issue #1134: load() is a no-op and leaves _records empty when session files absent."""
    _ensure_mitmproxy_stubs()
    from src.docker.proxy import addon as addon_mod

    f = addon_mod.ProxyAddon.__new__(addon_mod.ProxyAddon)
    f.allowed_domains = set()
    f._records = {}
    f._session_id = ""
    f._session_token = ""
    f._log_file = None
    f.logger = MagicMock()

    with patch("src.docker.proxy.addon.Path") as mock_path_cls:
        mock_path = MagicMock()
        mock_path.read_text.side_effect = FileNotFoundError("no file")
        mock_path_cls.return_value = mock_path
        await f.load(None)

    assert f._records == {}
