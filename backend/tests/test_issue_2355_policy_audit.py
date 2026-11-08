"""Issue #2355 fix 1: every relay policy change goes through one audited write path."""

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.relay.audit import RelayAuditLog
from backend.relay.handles import RelayHandleIndex
from backend.relay.service import RelayService
from backend.session_config import SessionConfig
from backend.session_manager import SessionManager

CH1, CH2 = "C0AAAAAAA1", "C0AAAAAAA2"


@pytest.fixture
async def env(tmp_path):
    sessions = SessionManager(tmp_path)
    await sessions.initialize()
    audit = RelayAuditLog(tmp_path)
    relays = {"r1": SimpleNamespace(adapter_type="slack", name="Slack")}
    service = RelayService(
        SimpleNamespace(relays=relays), RelayHandleIndex(), sessions, None, None, audit=audit
    )
    service.attach_policy_audit(sessions)
    workdir = Path(tmp_path) / "w"
    workdir.mkdir()
    await sessions.create_session(
        "s1", SessionConfig(working_directory=str(workdir)), name="Agent One"
    )
    return SimpleNamespace(sessions=sessions, service=service, audit=audit, workdir=workdir)


async def _rows(env):
    await env.service.drain_audit()
    return list(reversed([r for r in await env.audit.query("r1") if r["event"] == "policy_change"]))


def test_issue_2355_update_relay_policy_source_is_required_without_default():
    sig = inspect.signature(SessionManager.update_relay_policy)
    source = sig.parameters["source"]
    assert source.kind is inspect.Parameter.KEYWORD_ONLY
    assert source.default is inspect.Parameter.empty
    for fn in (RelayService.set_policy, RelayService.delete_policy):
        assert inspect.signature(fn).parameters["source"].default is inspect.Parameter.empty


async def test_issue_2355_editor_set_change_remove_chain_old_to_new(env):
    sm = env.sessions
    await sm.update_relay_policy("s1", "r1", CH1, "auto", source="editor")
    await sm.update_relay_policy("s1", "r1", CH1, "blocked", source="editor")
    await sm.update_relay_policy("s1", "r1", CH1, None, source="editor")
    rows = await _rows(env)
    assert [(r["old_policy"], r["policy"]) for r in rows] == [
        (None, "auto"), ("auto", "blocked"), ("blocked", None)
    ]
    r = rows[0]
    assert r["source"] == "editor" and r["tier"] == "session" and r["session_id"] == "s1"
    assert r["channel_id"] == CH1 and r["session_name"] == "Agent One" and r["outcome"] == "ok"


async def test_issue_2355_noop_write_records_nothing(env):
    sm = env.sessions
    await sm.update_relay_policy("s1", "r1", CH1, "auto", source="editor")
    await sm.update_relay_policy("s1", "r1", CH1, "auto", source="editor")
    await sm.update_relay_policy("s1", "r1", CH2, None, source="editor")  # removing the absent
    assert len(await _rows(env)) == 1


async def test_issue_2355_prompt_record_keeps_fields_and_gains_source_only(env):
    await env.sessions.update_relay_policy(
        "s1", "r1", CH1, "auto", source="prompt",
        audit_extra={"tool": "post", "channel_name": "#general", "approval": "prompt_promote"},
    )
    (r,) = await _rows(env)
    assert r["source"] == "prompt" and r["tool"] == "post" and r["approval"] == "prompt_promote"
    assert r["channel_name"] == "#general" and r["policy"] == "auto"
    assert r["old_policy"] is None and r["tier"] is None  # pre-#2355 shape + source only


async def test_issue_2355_config_replace_diffs_per_target(env):
    sm = env.sessions
    await sm.update_session(
        "s1", _replace_config={"relay_channel_policies": {"r1": {CH1: "auto", CH2: "ask"}}}
    )
    await sm.update_session(
        "s1", _replace_config={"relay_channel_policies": {"r1": {CH1: "blocked"}}}
    )
    rows = await _rows(env)
    assert [(r["channel_id"], r["old_policy"], r["policy"], r["source"]) for r in rows] == [
        (CH1, None, "auto", "config"), (CH2, None, "ask", "config"),
        (CH1, "auto", "blocked", "config"), (CH2, "ask", None, "config"),
    ]


async def test_issue_2355_identical_or_omitting_config_writes_nothing(env):
    sm = env.sessions
    cfg = {"relay_channel_policies": {"r1": {CH1: "auto"}}}
    await sm.update_session("s1", _replace_config=cfg)
    await sm.update_session("s1", _replace_config=cfg)
    await sm.update_session("s1", model="m")  # unrelated field
    assert len(await _rows(env)) == 1


async def test_issue_2355_create_time_overrides_are_config_with_old_null(env, monkeypatch):
    from backend.relay import validation

    monkeypatch.setattr(validation, "_relay_manager", None)  # structure-only validation
    await env.sessions.create_session(
        "s2", SessionConfig(
            working_directory=str(env.workdir), relay_channel_policies={"r1": {CH1: "auto"}}
        ),
    )
    (r,) = await _rows(env)
    assert (r["session_id"], r["source"], r["old_policy"], r["policy"]) == (
        "s2", "config", None, "auto"
    )


async def test_issue_2355_override_pass_after_create_counts_as_created(env):
    await env.sessions.create_session(
        "s3", SessionConfig(working_directory=str(env.workdir), template_id="t1"),
    )
    await env.sessions.update_session(
        "s3", _replace_config={"relay_channel_policies": {"r1": {CH1: "ask"}}},
    )
    (r,) = await _rows(env)
    assert r["source"] == "config" and r["old_policy"] is None


async def test_issue_2355_callback_failure_never_breaks_the_write(env):
    def boom(*_a):
        raise RuntimeError("callback bug")

    env.sessions.add_policy_change_callback(boom)
    assert await env.sessions.update_relay_policy("s1", "r1", CH1, "auto", source="editor")
    info = await env.sessions.get_session_info("s1")
    assert info.config["relay_channel_policies"] == {"r1": {CH1: "auto"}}
    assert len(await _rows(env)) == 1  # the other callback still ran


async def test_issue_2355_audit_failure_never_raises(env):
    async def failing(*_a, **_k):
        raise OSError("disk full")

    env.audit.record = failing
    assert await env.sessions.update_relay_policy("s1", "r1", CH1, "auto", source="editor")
    await env.service.drain_audit()  # swallowed


async def test_issue_2355_tier_edit_one_record_per_changed_target_no_session_id(env):
    env.service.audit_tier_policy_change(
        "template", "t1", "Tmpl",
        {"relay_channel_policies": {"r1": {CH1: "ask", CH2: "auto"}}},
        {"relay_channel_policies": {"r1": {CH1: "blocked", CH2: "auto"}}},
    )
    env.service.audit_tier_policy_change("profile", "p1", "Prof", {}, {})  # unchanged
    (r,) = await _rows(env)
    assert (r["tier"], r["tier_id"], r["tier_name"], r["source"]) == (
        "template", "t1", "Tmpl", "template"
    )
    assert r["session_id"] is None and (r["old_policy"], r["policy"]) == ("ask", "blocked")


async def test_issue_2355_channel_name_is_best_effort(env):
    async def namer(relay_id, channel_id):
        return "#general"

    env.service.channel_namer = namer
    await env.sessions.update_relay_policy("s1", "r1", CH1, "auto", source="editor")
    (r,) = await _rows(env)
    assert r["channel_name"] == "#general"


async def test_issue_2355_override_pass_flag_cleared_on_start_and_delete(env, monkeypatch):
    from backend.relay import validation

    monkeypatch.setattr(validation, "_relay_manager", None)
    sm = env.sessions
    await sm.create_session("s4", SessionConfig(working_directory=str(env.workdir), template_id="t1"))
    assert "s4" in sm._awaiting_override_pass
    await sm.start_session("s4")
    assert "s4" not in sm._awaiting_override_pass
    await sm.create_session("s5", SessionConfig(working_directory=str(env.workdir), template_id="t1"))
    await sm.delete_session("s5")
    assert "s5" not in sm._awaiting_override_pass
