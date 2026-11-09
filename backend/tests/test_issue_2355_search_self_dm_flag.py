"""Issue #2355 fix 3: search flags the agent's own marked self-DM posts as agent messages."""

import pytest

from backend.relay.adapter import RelayMessage
from backend.relay.safety import SELF_DM_MARKER
from backend.tests.relay_support import CH_AUTO, OTHER_DM, OTHER_USER, OWNER, SELF_DM, make_env
from backend.tests.test_relay_1c_ii import fence_payload

SHORTCODE = f"{SELF_DM_MARKER} from the agent"
EMOJI = "\U0001F916 [cc-agent] from the agent"


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path, policies={SELF_DM: "auto", CH_AUTO: "auto", OTHER_DM: "auto"})


async def _flags(env, *messages):
    env.adapter.search_results = list(messages)
    result = await env.tools.handle_search("s1", {"query": "x"})
    return {m["ts"]: m["is_agent_message"] for m in fence_payload(result["content"][0]["text"])}


def msg(ts, text, *, channel=SELF_DM, user=OWNER, **kw):
    return RelayMessage(ts=ts, text=text, user=user, channel=channel, **kw)


async def test_marked_owner_message_in_self_dm_is_flagged(env):
    flags = await _flags(
        env, msg("1.1", SHORTCODE), msg("1.2", EMOJI), msg("1.3", "typed by the owner"),
    )
    assert flags == {"1.1": True, "1.2": True, "1.3": False}


async def test_marker_flag_uses_untruncated_text(env):
    flags = await _flags(env, msg("1.1", SHORTCODE + " " + "x" * 2000))
    assert flags == {"1.1": True}


async def test_marker_outside_self_dm_or_from_non_owner_never_flags(env):
    flags = await _flags(
        env,
        msg("1.1", SHORTCODE, channel=CH_AUTO),
        msg("1.2", SHORTCODE, channel=OTHER_DM),
        msg("1.3", SHORTCODE, user=OTHER_USER),
        msg("1.4", SHORTCODE, user=None),
    )
    assert flags == {"1.1": False, "1.2": False, "1.3": False, "1.4": False}


async def test_existing_flagging_is_unchanged(env):
    flags = await _flags(
        env,
        msg("1.1", "plain", channel=CH_AUTO, app_id="A1"),
        msg("1.2", "plain", channel=CH_AUTO, bot_id="B1"),
        msg("1.3", "plain", channel=CH_AUTO, metadata={"event_type": "cc_webui_agent_message"}),
        msg("1.4", "plain", channel=CH_AUTO),
    )
    assert flags == {"1.1": True, "1.2": True, "1.3": True, "1.4": False}


async def test_unknown_self_dm_fails_closed(env):
    env.adapter.fail_open_dm = True
    assert await _flags(env, msg("1.1", SHORTCODE)) == {"1.1": False}


async def test_unknown_owner_fails_closed(env):
    env.gate.relay_manager.relays["relay1"].owner_user_id = None
    assert await _flags(env, msg("1.1", SHORTCODE)) == {"1.1": False}


async def test_thread_reads_keep_their_flagging(env):
    env.adapter.thread_messages = [
        msg("1700000000.000100", SHORTCODE, thread_ts="1700000000.000100"),  # marker alone does not flag in a thread read
        msg("1700000000.000200", "x", thread_ts="1700000000.000100", bot_id="B1"),
    ]
    result = await env.tools.handle_read_thread("s1", {"channel": SELF_DM, "thread_ts": "1700000000.000100"})
    assert not result.get("is_error"), result
    flags = {m["ts"]: m["is_agent_message"] for m in fence_payload(result["content"][0]["text"])}
    assert flags == {"1700000000.000100": False, "1700000000.000200": True}


async def test_self_dm_not_resolved_when_no_result_can_carry_the_marker(env):
    env.adapter.fail_open_dm = True  # would be hit by a needless resolve
    await _flags(env, msg("1.1", "plain", channel=CH_AUTO))
    assert [c for c in env.adapter.calls if c[0] == "open_dm"] == []
