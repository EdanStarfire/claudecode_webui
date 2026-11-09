"""Real Socket Mode transport wiring, without network (issue #2103 stage 3a, D1)."""

from __future__ import annotations

import logging
import sys

import pytest

from backend.relay.adapter import RelayError
from backend.relay.inbound.slack_socket import SlackSocketTransport
from backend.relay.inbound.transport import (
    RawEnvelope,
    TransportAuthError,
    TransportUnavailableError,
)

SECRET = "xapp-1-SECRETVALUE-do-not-leak"


def transport(token=SECRET):
    def provider():
        if token is None:
            raise RelayError("not_authed", "token")
        return token

    t = SlackSocketTransport(provider)
    states: list[str] = []
    envs: list[RawEnvelope] = []

    async def on_env(env):
        envs.append(env)

    t.set_handlers(on_env, states.append)
    return t, states, envs


async def test_missing_library_reports_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "slack_sdk.socket_mode.aiohttp", None)
    t, _, _ = transport()
    with pytest.raises(TransportUnavailableError) as exc:
        await t.connect()
    assert exc.value.code == "slack_sdk_missing"


async def test_absent_secret_is_an_auth_error():
    t, _, _ = transport(token=None)
    with pytest.raises(TransportAuthError):
        await t.connect()


@pytest.mark.parametrize("error,expected", [
    ("invalid_auth", TransportAuthError),
    ("token_revoked", TransportAuthError),
    ("account_inactive", TransportAuthError),
    ("internal_error", TransportUnavailableError),
])
async def test_wss_url_failure_stops_sdk_retry_loop(monkeypatch, caplog, error, expected):
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web.async_client import AsyncWebClient

    calls = []

    async def failing(self, *, app_token, **kw):
        calls.append(app_token)
        raise SlackApiError("failed", {"error": error})

    monkeypatch.setattr(AsyncWebClient, "apps_connections_open", failing)
    t, states, _ = transport()
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(expected) as exc:
            await t.connect()
    assert len(calls) == 1  # the SDK's endless every-few-seconds retry is not allowed to start
    assert SECRET not in str(exc.value) and SECRET not in caplog.text
    assert exc.value.code == error or expected is TransportUnavailableError
    await t.disconnect()


async def test_network_error_is_unavailable_not_a_retry_storm(monkeypatch):
    from slack_sdk.web.async_client import AsyncWebClient

    calls = []

    async def failing(self, *, app_token, **kw):
        calls.append(1)
        raise OSError("connection refused")

    monkeypatch.setattr(AsyncWebClient, "apps_connections_open", failing)
    t, _, _ = transport()
    with pytest.raises(TransportUnavailableError) as exc:
        await t.connect()
    assert exc.value.code == "network_error" and len(calls) == 1
    await t.disconnect()


class FakeClient:
    def __init__(self):
        self.responses = []

    async def send_socket_mode_response(self, response):
        self.responses.append(response)


async def test_listener_acks_after_handler_and_reports_hello():
    t, states, envs = transport()
    client = FakeClient()
    await t._listener(client, {"type": "hello"}, "{}")
    assert states == ["connected"] and envs[0].type == "hello" and not client.responses
    msg = {"type": "events_api", "envelope_id": "E1", "payload": {"event": {}}, "retry_attempt": 2}
    await t._listener(client, msg, "{}")
    assert envs[1].envelope_id == "E1" and envs[1].retry_attempt == 2
    assert client.responses == [{"envelope_id": "E1"}]
    await t._listener(client, {"type": "slash_commands", "envelope_id": "E2"}, "{}")
    assert client.responses[-1] == {"envelope_id": "E2"} and len(envs) == 2  # acked, not processed


async def test_handler_failure_skips_the_ack():
    t, _, _ = transport()

    async def boom(env):
        raise RuntimeError("disk full")

    t.set_handlers(boom, lambda s: None)
    client = FakeClient()
    with pytest.raises(RuntimeError):
        await t._listener(
            client, {"type": "events_api", "envelope_id": "E1", "payload": {}}, "{}"
        )
    assert client.responses == []
