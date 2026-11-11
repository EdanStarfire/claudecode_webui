"""envelope_id in message metadata and instruction audit rows (issue #2103 3d-ii)."""

from __future__ import annotations

import pytest

from backend.relay.inbound.service import comm_id_for
from backend.tests.relay_inbound_support import (
    RELAY_ID,
    SELF_DM,
    audit_events,
    envelope,
    make_inbound,
    message_payload,
    settle,
)
from backend.tests.relay_monitor_support import CHANNEL, emit, fire_window, monitor, started


@pytest.fixture
async def env(tmp_path):
    e = make_inbound(tmp_path)
    e.relay_service.policies[CHANNEL] = "read_only"
    e.t = await started(e)
    yield e
    await e.service.stop()


async def test_monitor_envelope_metadata_carries_the_comm_id(env):
    await monitor(env)
    await emit(env, env.t, "deploy now", seq=1)
    await fire_window(env)
    call = env.port.calls[-1]
    meta = call["metadata"]["relay_inbound"]
    assert meta["envelope_id"] == call["comm_id"]
    assert meta["kind"] == "monitor" and meta["count"] == 1
    assert call["metadata"]["comm"]["from_display_name"] == "Slack monitor"  # CommCard shape unchanged


async def test_instruction_metadata_and_audit_carry_the_comm_id(env):
    ts = env.clock.ts(seq=1)
    await env.t.emit(envelope(message_payload("!my-agent run it", ts), "E1"))
    await settle(env)
    call = env.sink.calls[0]
    expected = comm_id_for(RELAY_ID, SELF_DM, ts)
    assert call["metadata"]["relay_inbound"]["envelope_id"] == expected == call["comm_id"]
    assert call["metadata"]["relay_inbound"]["kind"] == "instruction"
    delivered = [r for r in audit_events(env, "inbound_instruction") if r["outcome"] == "delivered"]
    assert delivered and delivered[0]["envelope_id"] == expected


async def test_monitor_audit_rows_still_carry_envelope_id(env):
    await monitor(env)
    await emit(env, env.t, "deploy now", seq=1)
    await fire_window(env)
    await env.service.watcher.poll_once()
    comm_id = env.port.calls[-1]["comm_id"]
    env.port.statuses[(env.port.calls[-1]["session_id"], comm_id)] = "delivered"
    await env.service.watcher.poll_once()
    rows = audit_events(env, "monitor_delivered")
    assert rows and rows[-1]["envelope_id"] == comm_id
