"""Issue #2103 ui-2b: runtime relay_summary on the session payload + template inheritance."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.relay.handles import RelayHandleIndex
from backend.relay.service import RelayService
from backend.session_config import SessionConfig
from backend.session_manager import SessionManager

CH1, CH2, CH3 = "C0AAAAAAA1", "C0AAAAAAA2", "C0AAAAAAA3"


class _Templates:
    def __init__(self):
        self.t = SimpleNamespace(
            template_id="t1", profile_ids={"relay": "p1"},
            config={"relay_id": "r1", "relay_channel_policies": {"r1": {CH1: "ask"}}},
        )

    async def get_template(self, tid):
        return self.t if tid == "t1" else None

    async def list_templates(self):
        return [self.t]


class _Profiles:
    def __init__(self):
        self.p = SimpleNamespace(config={"relay_channel_policies": {"r1": {CH3: "blocked"}}})

    async def get_profile(self, pid):
        return self.p if pid == "p1" else None


@pytest.fixture
async def env(tmp_path):
    sessions = SessionManager(tmp_path)
    await sessions.initialize()
    relays = {"r1": SimpleNamespace(adapter_type="slack", name="Slack")}
    templates, profiles = _Templates(), _Profiles()
    service = RelayService(
        SimpleNamespace(relays=relays), RelayHandleIndex(), sessions, templates, profiles
    )
    return SimpleNamespace(
        sessions=sessions, service=service, templates=templates, profiles=profiles,
        relays=relays, tmp=tmp_path,
    )


async def _create(env, sid="s1", **cfg):
    workdir = Path(env.tmp) / "w"
    workdir.mkdir(exist_ok=True)
    await env.sessions.create_session(
        sid, SessionConfig(working_directory=str(workdir), **cfg), name="Agent One"
    )
    return await env.sessions.get_session_info(sid)


async def test_summary_absent_without_relay(env):
    info = await _create(env)
    await env.service.refresh_summary(info.session_id)
    assert info.relay_summary is None
    assert info.to_dict()["relay_summary"] is None


async def test_summary_counts_nested_policies_and_tracks_edits(env):
    info = await _create(env, relay_id="r1")
    await env.service.set_policy("s1", "r1", CH1, "auto", source="editor")
    await env.service.set_policy("s1", "r1", CH2, "ask", source="editor")
    s = info.relay_summary
    assert s["relay_id"] == "r1" and s["adapter_type"] == "slack" and s["policy_count"] == 2
    await env.service.delete_policy("s1", "r1", CH1, source="editor")
    assert info.relay_summary["policy_count"] == 1
    await env.service.set_handle("s1", "my-handle")
    assert info.relay_summary["handle"] == "my-handle"


async def test_summary_cleared_when_relay_removed(env):
    info = await _create(env, relay_id="r1")
    await env.service.refresh_summary("s1")
    assert info.relay_summary is not None
    env.relays.clear()  # relay deleted
    await env.service.refresh_all_summaries()
    assert info.relay_summary is None


async def test_summary_is_runtime_only_never_persisted(env):
    info = await _create(env, relay_id="r1")
    await env.service.set_policy("s1", "r1", CH1, "auto", source="editor")
    state = json.loads((env.sessions.sessions_dir / "s1" / "state.json").read_text())
    assert "relay_summary" not in state
    assert info.to_dict()["relay_summary"]["policy_count"] == 1


async def test_summary_set_broadcasts_only_on_change(env):
    await _create(env, relay_id="r1")
    assert await env.sessions.set_relay_summary("s1", {"a": 1}) is True
    assert await env.sessions.set_relay_summary("s1", {"a": 1}) is False


async def test_issue_2103_template_policies_inherited_live_not_copied(env):
    """Editing the template reaches an existing session; the session config holds no copy."""
    info = await _create(env, template_id="t1")
    assert "relay_channel_policies" not in info.config
    await env.service.refresh_summary("s1")
    # template ask(CH1) + profile blocked(CH3)
    assert info.relay_summary["policy_count"] == 2
    assert {k: v.policy for k, v in
            (await env.service.effective_relay_policies(info, "r1")).items()} == {
        CH1: "ask", CH3: "blocked"}

    env.templates.t.config["relay_channel_policies"] = {"r1": {CH1: "auto", CH2: "ask"}}
    await env.service.refresh_for_template("t1")
    eff = await env.service.effective_relay_policies(info, "r1")
    assert {k: v.policy for k, v in eff.items()} == {CH1: "auto", CH2: "ask", CH3: "blocked"}
    assert info.relay_summary["policy_count"] == 3
    assert "relay_channel_policies" not in info.config

    # profile edit propagates through the template that references it
    env.profiles.p.config = {}
    await env.service.refresh_for_profile("p1")
    assert info.relay_summary["policy_count"] == 2

    # a session-tier override wins for that target only
    await env.service.set_policy("s1", "r1", CH1, "blocked", source="editor")
    eff = await env.service.effective_relay_policies(info, "r1")
    assert eff[CH1].policy == "blocked" and eff[CH1].source == "session"
    assert eff[CH2].source == "template"
