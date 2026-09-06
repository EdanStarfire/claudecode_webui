"""Tests for src/backend_client.py's Backend reachability wiring (issue #1844).

BackendClient's relay()/get_json()/request_json() must feed the shared
BackendReachabilityTracker on both the failure path (httpx.RequestError —
connection-level, i.e. Backend is actually unreachable) and the success path
(any response received, even a 4xx/5xx body — that's a genuine backend-side
error, not an unreachable-backend condition), while leaving existing
raise/return behavior for callers completely unchanged. Follows the existing
convention of `side_effect=httpx.ConnectError("refused")` seen in
test_oauth_callback_relay.py.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from starlette.requests import Request

from src.backend_client import BackendClient


def _make_client() -> BackendClient:
    client = BackendClient.__new__(BackendClient)  # bypass __init__ (no real AsyncClient/socket)
    from src.backend_reachability import BackendReachabilityTracker
    client.base_url = "http://backend.test"
    client._token = "token"
    client._client = MagicMock()
    client.reachability = BackendReachabilityTracker()
    return client


def _make_request(method: str = "GET", path: str = "/api/sessions", body: bytes = b"") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


# --- relay() ---


@pytest.mark.asyncio
async def test_issue_1844_relay_records_failure_and_reraises_on_request_error():
    client = _make_client()
    client._client.request = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        await client.relay(_make_request(), "/api/sessions")

    assert client.reachability.is_unreachable is True


@pytest.mark.asyncio
async def test_issue_1844_relay_records_success_even_on_error_status_body():
    """A response body carrying a 4xx/5xx is a genuine backend-side error, not an
    unreachable backend — relay() never calls raise_for_status() at all, so this
    must still clear reachability."""
    client = _make_client()
    client.reachability.record_failure(httpx.ConnectError("refused"))
    assert client.reachability.is_unreachable is True

    backend_resp = MagicMock()
    backend_resp.status_code = 500
    backend_resp.headers = {}
    backend_resp.content = b'{"detail": "boom"}'
    client._client.request = AsyncMock(return_value=backend_resp)

    await client.relay(_make_request(), "/api/sessions")

    assert client.reachability.is_unreachable is False


# --- get_json() ---


@pytest.mark.asyncio
async def test_issue_1844_get_json_records_failure_and_reraises_on_request_error():
    client = _make_client()
    client._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        await client.get_json("/api/config")

    assert client.reachability.is_unreachable is True


@pytest.mark.asyncio
async def test_issue_1844_get_json_records_success_before_raise_for_status():
    """A successful connection that gets back a 4xx/5xx must still clear
    reachability, even though raise_for_status() subsequently raises."""
    client = _make_client()
    client.reachability.record_failure(httpx.ConnectError("refused"))

    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock(status_code=500)
    )
    client._client.get = AsyncMock(return_value=resp)

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_json("/api/config")

    assert client.reachability.is_unreachable is False


# --- request_json() ---


@pytest.mark.asyncio
async def test_issue_1844_request_json_records_failure_and_reraises_on_request_error():
    client = _make_client()
    client._client.request = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        await client.request_json("PUT", "/api/config", json={"a": 1})

    assert client.reachability.is_unreachable is True


@pytest.mark.asyncio
async def test_issue_1844_request_json_records_success_before_raise_for_status():
    client = _make_client()
    client.reachability.record_failure(httpx.ConnectError("refused"))

    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "400", request=MagicMock(), response=MagicMock(status_code=400)
    )
    client._client.request = AsyncMock(return_value=resp)

    with pytest.raises(httpx.HTTPStatusError):
        await client.request_json("PUT", "/api/config", json={"a": 1})

    assert client.reachability.is_unreachable is False


@pytest.mark.asyncio
async def test_issue_1847_request_json_omits_timeout_kwarg_when_not_given():
    """Existing callers (e.g. config.py's PUT /api/config) must keep this client's
    normal per-request default — explicitly passing `timeout=None` to httpx disables
    the timeout entirely rather than falling back to the client default, so the
    kwarg must be omitted outright, not passed as None."""
    client = _make_client()
    resp = MagicMock()
    resp.json.return_value = {"ok": True}
    client._client.request = AsyncMock(return_value=resp)

    await client.request_json("PUT", "/api/config", json={"a": 1})

    _, kwargs = client._client.request.call_args
    assert "timeout" not in kwargs


@pytest.mark.asyncio
async def test_issue_1847_request_json_forwards_explicit_timeout():
    """Callers awaiting a Backend operation with its own long synchronous work
    budget (e.g. the restart endpoint's git operations + uv sync) must be able to
    give the client-side call matching headroom."""
    client = _make_client()
    resp = MagicMock()
    resp.json.return_value = {"ok": True}
    client._client.request = AsyncMock(return_value=resp)

    await client.request_json("POST", "/api/system/restart", json={}, timeout=210.0)

    _, kwargs = client._client.request.call_args
    assert kwargs["timeout"] == 210.0


# --- health() / ready() ---


@pytest.mark.asyncio
async def test_issue_1844_health_records_failure_and_returns_false():
    client = _make_client()
    client._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    assert await client.health() is False
    assert client.reachability.is_unreachable is True


@pytest.mark.asyncio
async def test_issue_1844_health_records_success_and_returns_true():
    client = _make_client()
    client.reachability.record_failure(httpx.ConnectError("refused"))
    resp = MagicMock()
    resp.status_code = 200
    client._client.get = AsyncMock(return_value=resp)

    assert await client.health() is True
    assert client.reachability.is_unreachable is False


@pytest.mark.asyncio
async def test_issue_1844_ready_records_failure_and_returns_false():
    client = _make_client()
    client._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    assert await client.ready() is False
    assert client.reachability.is_unreachable is True


@pytest.mark.asyncio
async def test_issue_1844_ready_records_success_and_returns_true():
    client = _make_client()
    client.reachability.record_failure(httpx.ConnectError("refused"))
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ready": True}
    client._client.get = AsyncMock(return_value=resp)

    assert await client.ready() is True
    assert client.reachability.is_unreachable is False
