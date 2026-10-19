"""Tests for the mcp-2.x/httpx2 transport mismatch (issue #2242).

`mcp` 2.x vendors its own HTTP stack (`httpx2`) and `create_oauth_metadata_request`/
`create_client_registration_request` (mcp.client.auth.utils) both return `httpx2.Request`
objects. `OAuthFlowManager.start_flow` (backend/oauth_manager.py) used to send these
through a plain `httpx.AsyncClient` — `httpx2.AsyncClient` is not a subclass of
`httpx.AsyncClient`, so every send raised `RuntimeError: Attempted to send an sync
request with an AsyncClient instance`, silently swallowed by a bare `except Exception`,
making every MCP server that needs discovery or DCR fall back to guessed endpoints
(or fail outright) since #2177. The fix switches `start_flow`'s client construction to
`httpx2.AsyncClient()`.

Unlike every other OAuth test file in this repo, this one does NOT mock `.send()` or
the client class — AC3/AC4 specifically require proving the fix against a real send
over a real socket, so a vendored-transport regression of this exact shape cannot pass
the test gate again without anyone needing to remember to write this test a second time.

Covers:
- AC1: exactly 3 call sites of create_oauth_metadata_request/create_client_registration_request
  in the whole repo (excluding tests), all inside start_flow's httpx2.AsyncClient scope.
- AC2: WARNING-level logging + prm_error/as_metadata_error/dcr_error summary population
  when a discovery/DCR send fails.
- AC3/T1: the same code sent via httpx.AsyncClient (buggy) raises the sync/async mismatch
  error, and via httpx2.AsyncClient (fixed) succeeds — against a real running local server.
- AC4: the Home Assistant AS-metadata fixture (relative endpoints, no issuer) driven
  through the real, unmodified start_flow(), resolving to the CIMD mechanism.
- T2: a spec-compliant AS-metadata document advertising a registration_endpoint drives
  a real DCR POST against the local server, parsed back into a client_id.
"""

import json
import logging
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
import pytest
from mcp.client.auth.utils import create_oauth_metadata_request

from backend.oauth_manager import OAuthFlowManager

# Reuses #2240's fixture shape (relative endpoints, no issuer — RFC 8414 violations
# that Home Assistant's real server exhibits).
_HOME_ASSISTANT_METADATA = {
    "authorization_endpoint": "/auth/authorize",
    "token_endpoint": "/auth/token",
    "revocation_endpoint": "/auth/revoke",
    "client_id_metadata_document_supported": True,
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "token_endpoint_auth_methods_supported": ["none"],
    "code_challenge_methods_supported": ["S256"],
    "response_types_supported": ["code"],
}

_VALID_CLIENT_METADATA_URL = "https://app.example.com/oauth/client-metadata.json"
_REDIRECT_URI = "https://app.example.com/oauth/callback"


def _free_port() -> int:
    """Bind-then-close port allocation, mirroring test_docker_utils.py's own helper."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Scenario:
    """Mutable canned-response table read by the request handler at request time —
    tests set attributes on the yielded instance after the server/thread are already
    running."""

    def __init__(self):
        self.as_metadata: dict | None = None
        self.dcr_response: dict | None = None
        self.dcr_requests: list[dict] = []


def _make_handler_class(scenario: _Scenario) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - silence per-request stderr noise
            pass

        def _write_json(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/.well-known/oauth-protected-resource":
                # RFC 9728 not supported — matches Home Assistant's real behavior.
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/.well-known/oauth-authorization-server":
                if scenario.as_metadata is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self._write_json(200, scenario.as_metadata)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            if self.path in ("/register", "/oauth/register"):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                scenario.dcr_requests.append(json.loads(raw))
                self._write_json(201, scenario.dcr_response)
                return
            self.send_response(404)
            self.end_headers()

    return Handler


@pytest.fixture
def oauth_test_server():
    """Starts a real ThreadingHTTPServer bound to 127.0.0.1 serving canned OAuth
    discovery/DCR responses. Teardown is explicit (server.shutdown() + thread.join())
    rather than relying on daemon=True alone, per the plan's own risk callout about
    leaking threads across the test session."""
    scenario = _Scenario()
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler_class(scenario))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield scenario, f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        # A bounded join() that times out would otherwise fail silently, leaking a
        # live server thread across the rest of the test session instead of failing
        # the test that caused it.
        assert not thread.is_alive(), "oauth_test_server's background thread did not stop"


# ---------------------------------------------------------------------------
# AC1: exactly 3 call sites, all inside start_flow's httpx2.AsyncClient scope
# ---------------------------------------------------------------------------


def _iter_production_python_files(repo_root: Path):
    excluded_dirs = {".git", "node_modules", "frontend", ".venv", "venv"}
    for path in repo_root.rglob("*.py"):
        parts = path.relative_to(repo_root).parts
        if "tests" in parts:
            continue
        if any(part in excluded_dirs for part in parts):
            continue
        yield path


def test_ac1_exactly_three_call_sites_all_in_oauth_manager():
    repo_root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"\b(?:create_oauth_metadata_request|create_client_registration_request)\(")

    call_sites = []
    for path in _iter_production_python_files(repo_root):
        text = path.read_text()
        call_sites.extend([path.relative_to(repo_root)] * len(pattern.findall(text)))

    assert len(call_sites) == 3
    assert all(str(p) == "backend/oauth_manager.py" for p in call_sites)


def test_ac1_start_flow_sends_through_httpx2_async_client():
    import inspect

    src = inspect.getsource(OAuthFlowManager.start_flow)

    match = re.search(r"async with (\w+)\.AsyncClient\(\) as http:", src)
    assert match is not None, "start_flow must open an `async with ...AsyncClient() as http:` scope"
    assert match.group(1) == "httpx2"

    # Both call sites for create_oauth_metadata_request and the one for
    # create_client_registration_request must be inside that same scope.
    assert src.count("create_oauth_metadata_request(") == 2
    assert src.count("create_client_registration_request(") == 1


# ---------------------------------------------------------------------------
# AC3/T1: unmocked send — httpx.AsyncClient fails, httpx2.AsyncClient succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac3_httpx_async_client_fails_httpx2_async_client_succeeds(oauth_test_server):
    """Proves AC3's "fails on current main, passes after the fix" property as an
    executable fact: the exact same SDK-built request, sent for real against a real
    running server, fails under the buggy client construction and succeeds under the
    fixed one."""
    scenario, base_url = oauth_test_server
    scenario.as_metadata = dict(_HOME_ASSISTANT_METADATA)
    discovery_url = f"{base_url}/.well-known/oauth-authorization-server"

    # Buggy construction (pre-fix): httpx2.Request sent via httpx.AsyncClient.
    buggy_request = create_oauth_metadata_request(discovery_url)
    async with httpx.AsyncClient() as buggy_client:
        with pytest.raises(RuntimeError, match="AsyncClient instance"):
            await buggy_client.send(buggy_request)

    # Fixed construction: httpx2.Request sent via httpx2.AsyncClient.
    fixed_request = create_oauth_metadata_request(discovery_url)
    async with httpx2.AsyncClient() as fixed_client:
        response = await fixed_client.send(fixed_request)
        assert response.status_code == 200
        body = json.loads(await response.aread())
        assert body == _HOME_ASSISTANT_METADATA


# ---------------------------------------------------------------------------
# AC4: Home Assistant fixture through the real, unmodified start_flow()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac4_home_assistant_fixture_through_real_start_flow(oauth_test_server, tmp_path):
    scenario, base_url = oauth_test_server
    scenario.as_metadata = dict(_HOME_ASSISTANT_METADATA)

    manager = OAuthFlowManager(tmp_path)
    auth_url = await manager.start_flow(
        server_id="srv-ha",
        server_url=base_url,
        redirect_uri=_REDIRECT_URI,
        client_metadata_url=_VALID_CLIENT_METADATA_URL,
    )

    assert auth_url.startswith(f"{base_url}/auth/authorize?")
    query = parse_qs(urlparse(auth_url).query)
    assert query["client_id"][0] == _VALID_CLIENT_METADATA_URL

    summary = manager.get_discovery_summary("srv-ha")
    assert summary is not None
    assert summary["prm_found"] is False
    assert summary["as_metadata_found"] is True
    assert summary["as_metadata_normalized"] is True
    assert summary["mechanism"] == "cimd"
    assert "prm_error" not in summary
    assert "as_metadata_error" not in summary


# ---------------------------------------------------------------------------
# T2: spec-compliant document with registration_endpoint -> real DCR POST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_dcr_registration_through_real_start_flow(oauth_test_server, tmp_path):
    scenario, base_url = oauth_test_server
    scenario.as_metadata = {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/authorize",
        "token_endpoint": f"{base_url}/token",
        "registration_endpoint": f"{base_url}/register",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "response_types_supported": ["code"],
    }
    scenario.dcr_response = {"client_id": "test-client-id"}

    manager = OAuthFlowManager(tmp_path)
    auth_url = await manager.start_flow(
        server_id="srv-dcr",
        server_url=base_url,
        redirect_uri=_REDIRECT_URI,
    )

    assert auth_url.startswith(f"{base_url}/authorize?")
    query = parse_qs(urlparse(auth_url).query)
    assert query["client_id"][0] == "test-client-id"

    assert len(scenario.dcr_requests) == 1
    assert scenario.dcr_requests[0]["redirect_uris"] == [_REDIRECT_URI]

    store = manager.get_token_store("srv-dcr")
    client_info = await store.get_client_info()
    assert client_info is not None
    assert client_info.client_id == "test-client-id"

    summary = manager.get_discovery_summary("srv-dcr")
    assert summary is not None
    assert summary["mechanism"] == "dcr"
    assert "dcr_error" not in summary


# ---------------------------------------------------------------------------
# AC2: ERROR-level logging + summary error keys on transport failure
#
# Promoted to logger.error() rather than the plan's literal "WARNING" wording:
# shared/logging_config.py routes the bare `backend.oauth_manager` logger's records
# to root, whose only handlers (error.log + console) are both hardcoded to ERROR —
# a .warning() call here is silently dropped without --debug-oauth, defeating AC2's
# actual intent ("visible without --debug-oauth"). Every other always-visible call
# in this file (refresh_token, decrypt-failure) already uses .error() for the same
# reason. Decided by WebUI-Agent on review.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac2_error_logged_and_summary_error_keys_populated_on_transport_failure(
    tmp_path, caplog
):
    # Nothing is listening on this port — connection refused, deterministically and
    # quickly, without depending on DNS-failure behavior in a given environment.
    unreachable_url = f"http://127.0.0.1:{_free_port()}"
    manager = OAuthFlowManager(tmp_path)

    with caplog.at_level(logging.ERROR, logger="backend.oauth_manager"):
        auth_url = await manager.start_flow(
            server_id="srv-unreachable",
            server_url=unreachable_url,
            redirect_uri=_REDIRECT_URI,
            # Pre-registered client sidesteps DCR entirely, isolating this test to the
            # PRM/AS-metadata discovery failures (DCR's own failure path never raises
            # here, so start_flow completes and the summary gets persisted).
            pre_registered_client_id="pre-registered-id",
        )

    assert auth_url is not None

    summary = manager.get_discovery_summary("srv-unreachable")
    assert summary is not None
    assert summary["prm_found"] is False
    assert summary["as_metadata_found"] is False
    assert summary["prm_error"]["type"]
    assert summary["as_metadata_error"]["type"]

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("PRM discovery" in r.getMessage() for r in error_records)
    assert any("AS metadata discovery" in r.getMessage() for r in error_records)


@pytest.mark.asyncio
async def test_ac2_stale_as_metadata_error_cleared_when_retry_succeeds(
    oauth_test_server, tmp_path
):
    """A transient transport failure on an earlier discovery candidate must not leave
    a stale as_metadata_error in the summary once a later candidate in the same step
    succeeds — the error key mirrors as_metadata_found's final-state semantics, not
    an append-only log of every intermediate attempt."""
    scenario, base_url = oauth_test_server
    scenario.as_metadata = dict(_HOME_ASSISTANT_METADATA)

    unreachable_as_url = f"http://127.0.0.1:{_free_port()}/.well-known/oauth-authorization-server"
    working_as_url = f"{base_url}/.well-known/oauth-authorization-server"

    manager = OAuthFlowManager(tmp_path)
    with patch(
        "backend.oauth_manager.build_oauth_authorization_server_metadata_discovery_urls",
        return_value=[unreachable_as_url, working_as_url],
    ):
        await manager.start_flow(
            server_id="srv-retry",
            server_url=base_url,
            redirect_uri=_REDIRECT_URI,
            client_metadata_url=_VALID_CLIENT_METADATA_URL,
        )

    summary = manager.get_discovery_summary("srv-retry")
    assert summary is not None
    assert summary["as_metadata_found"] is True
    assert "as_metadata_error" not in summary
