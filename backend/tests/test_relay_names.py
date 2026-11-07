"""Issue #2103 outbound-1b: lazy bounded name resolution and owner-facing labels."""

import asyncio

from backend.relay.names import NameResolver, clean_name
from backend.tests.relay_support import (
    CH_ASK,
    OTHER_DM,
    OTHER_USER,
    OWNER,
    SELF_DM,
    FakeAdapter,
    make_env,
)


def calls(adapter, name):
    return [c for c in adapter.calls if c[0] == name]


async def test_cache_hit_avoids_second_call():
    adapter, names = FakeAdapter(), NameResolver()
    assert await names.user_name(adapter, "r", OTHER_USER) == "Alice Example"
    assert await names.user_name(adapter, "r", OTHER_USER) == "Alice Example"
    assert len(calls(adapter, "user_info")) == 1
    assert await names.channel_label(adapter, "r", CH_ASK) == "#ask-ch"
    assert await names.channel_label(adapter, "r", CH_ASK) == "#ask-ch"
    assert len(calls(adapter, "conversation_info")) == 1


async def test_failure_falls_back_to_id_and_is_not_hammered():
    adapter, names = FakeAdapter(), NameResolver()
    adapter.fail_lookups = True
    assert await names.channel_label(adapter, "r", OTHER_DM) == OTHER_DM
    assert await names.user_name(adapter, "r", OTHER_USER) is None
    assert await names.channel_label(adapter, "r", OTHER_DM) == OTHER_DM
    assert len(calls(adapter, "conversation_info")) == 1  # failure TTL: no retry storm
    assert len(calls(adapter, "user_info")) == 1


async def test_failure_is_retried_after_ttl():
    now = [0.0]
    adapter, names = FakeAdapter(), NameResolver(clock=lambda: now[0])
    adapter.fail_lookups = True
    assert await names.user_name(adapter, "r", OTHER_USER) is None
    adapter.fail_lookups = False
    now[0] += 61
    assert await names.user_name(adapter, "r", OTHER_USER) == "Alice Example"


async def test_slow_lookup_times_out_and_falls_back():
    class Slow(FakeAdapter):
        async def conversation_info(self, channel):
            await asyncio.sleep(5)

    names = NameResolver(timeout=0.05)
    assert await names.channel_label(Slow(), "r", CH_ASK) == CH_ASK


async def test_labels_for_dms():
    adapter, names = FakeAdapter(), NameResolver()
    assert await names.channel_label(adapter, "r", SELF_DM, OWNER) == "your self-DM"
    assert await names.channel_label(adapter, "r", OTHER_DM, OWNER) == "DM with Alice Example"
    adapter.users.pop(OTHER_USER)
    other = NameResolver()
    assert await other.channel_label(adapter, "r", OTHER_DM, OWNER) == f"DM with {OTHER_USER}"


async def test_cache_is_bounded_lru():
    adapter, names = FakeAdapter(), NameResolver(max_size=2)
    for uid in ("U0AAAAAAA", "U0BBBBBBB", "U0CCCCCCC"):
        await names.user_name(adapter, "r", uid)
    await names.user_name(adapter, "r", "U0AAAAAAA")  # evicted -> looked up again
    assert len(calls(adapter, "user_info")) == 4


async def test_user_names_dedupes_and_caps_lookups():
    adapter, names = FakeAdapter(), NameResolver()
    ids = [OTHER_USER, OTHER_USER, None] + [f"U0X{i:06d}" for i in range(40)]
    out = await names.user_names(adapter, "r", ids, limit=5)
    assert len(out) == 5 and len(calls(adapter, "user_info")) == 5


def test_clean_name_strips_control_zero_width_and_caps():
    assert clean_name("Al​ice‮\x00") == "Alice"
    assert clean_name("x" * 500) == "x" * 80
    assert clean_name("​ ") is None


async def test_prompt_carries_label_and_self_dm(tmp_path):
    env = make_env(tmp_path, policies={SELF_DM: "ask", OTHER_DM: "ask", CH_ASK: "ask"})
    me = await env.gate.evaluate("s1", "mcp__relay__post", {"channel": SELF_DM, "text": "x"})
    assert me.prompt["channel_label"] == "your self-DM"
    other = await env.gate.evaluate("s1", "mcp__relay__post", {"channel": OTHER_DM, "text": "x"})
    assert other.prompt["channel_label"] == "DM with Alice Example"
    chan = await env.gate.evaluate("s1", "mcp__relay__post", {"channel": CH_ASK, "text": "x"})
    assert chan.prompt["channel_label"] == "#ask-ch" and chan.prompt["channel_name"] == "ask-ch"


async def test_prompt_falls_back_to_id_when_lookup_fails(tmp_path):
    env = make_env(tmp_path)
    env.adapter.fail_lookups = True
    gr = await env.gate.evaluate("s1", "mcp__relay__post", {"channel": CH_ASK, "text": "x"})
    assert gr.kind == "prompt" and gr.prompt["channel_label"] == CH_ASK


async def test_audit_records_resolved_label(tmp_path):
    from backend.tests.relay_support import CH_AUTO

    env = make_env(tmp_path)
    await env.tools.handle_post("s1", "post", {"channel": CH_AUTO, "text": "hi"})
    await env.tools.handle_post("s1", "post", {"channel": SELF_DM, "text": "hi"})
    recs = await env.audit.query("relay1")
    assert recs[0]["channel_name"] == "your self-DM" and recs[1]["channel_name"] == "#auto-ch"


async def test_agent_visible_errors_never_contain_channel_names(tmp_path):
    from backend.tests.relay_support import CH_RO

    env = make_env(tmp_path)
    denied = await env.tools.handle_post("s1", "post", {"channel": CH_RO, "text": "hi"})
    ask = await env.tools.handle_post("s1", "post", {"channel": CH_ASK, "text": "hi"})
    hook = await env.gate.evaluate("s1", "mcp__relay__post", {"channel": CH_RO, "text": "hi"})
    blob = str(denied) + str(ask) + str(hook.message)
    assert "ro-ch" not in blob and "ask-ch" not in blob and CH_RO in blob
