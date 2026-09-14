"""Unit tests for HookConfigManager (issue #1629).

Covers HookConfig/HookEntry CRUD, get_configs_by_ids skip semantics, and
to_sdk_hooks_payload() flattening — single event, multi-event expansion,
disabled entries/configs excluded, matcher omission, command vs http shapes.
"""

import tempfile
from pathlib import Path

import pytest

from backend.hook_config_manager import HookConfig, HookConfigManager, HookEntry


def _entry(**kwargs) -> dict:
    """Minimal hook entry dict, as accepted by create_config()/update_config()."""
    base = {
        "events": ["PreToolUse"],
        "matcher": "Bash",
        "enabled": True,
        "type": "command",
        "command": "echo hi",
        "timeout": None,
    }
    base.update(kwargs)
    return base


@pytest.fixture
def manager():
    with tempfile.TemporaryDirectory() as tmp:
        yield HookConfigManager(Path(tmp))


# ---------------------------------------------------------------------------
# HookEntry / HookConfig dataclass round-trip
# ---------------------------------------------------------------------------


def test_issue_1629_hook_entry_from_dict_assigns_id_when_missing():
    entry = HookEntry.from_dict({"events": ["Stop"]})
    assert entry.id
    assert entry.events == ["Stop"]
    assert entry.type == "command"
    assert entry.enabled is True


def test_issue_1629_hook_entry_from_dict_replaces_explicit_null_id():
    """Regression: request models (HookEntryModel) dump an explicit `id: None` for
    new entries rather than omitting the key — `dict.setdefault` would leave that
    None in place, persisting `"id": null` to disk instead of generating a UUID."""
    entry = HookEntry.from_dict({"id": None, "events": ["Stop"], "enabled": None, "type": None})
    assert entry.id
    assert entry.enabled is True
    assert entry.type == "command"


def test_issue_1629_hook_config_to_dict_from_dict_round_trip():
    config = HookConfig(
        id="cfg-1",
        name="Audit Logging",
        slug="audit_logging",
        hooks=[HookEntry.from_dict(_entry())],
    )
    data = config.to_dict()
    restored = HookConfig.from_dict(data)
    assert restored.id == "cfg-1"
    assert restored.name == "Audit Logging"
    assert len(restored.hooks) == 1
    assert restored.hooks[0].command == "echo hi"


# ---------------------------------------------------------------------------
# HookConfigManager CRUD
# ---------------------------------------------------------------------------


async def test_issue_1629_create_get_list_config(manager):
    config = await manager.create_config(name="Audit Logging", hooks=[_entry()])
    assert config.slug == "audit_logging"
    assert len(config.hooks) == 1

    fetched = await manager.get_config(config.id)
    assert fetched is config

    listed = await manager.list_configs()
    assert listed == [config]


async def test_issue_1629_create_rejects_empty_name(manager):
    with pytest.raises(ValueError):
        await manager.create_config(name="   ")


async def test_issue_1629_create_rejects_duplicate_slug(manager):
    await manager.create_config(name="Audit Logging")
    with pytest.raises(ValueError):
        await manager.create_config(name="Audit Logging")


async def test_issue_1629_update_config_renames_slug_and_persists(manager):
    config = await manager.create_config(name="Old Name")
    old_file = manager.configs_dir / "old_name.json"
    assert old_file.exists()

    updated = await manager.update_config(config.id, name="New Name")
    assert updated.slug == "new_name"
    assert not old_file.exists()
    assert (manager.configs_dir / "new_name.json").exists()


async def test_issue_1629_update_config_replaces_hooks_wholesale(manager):
    config = await manager.create_config(name="Audit Logging", hooks=[_entry()])
    updated = await manager.update_config(
        config.id, hooks=[_entry(events=["Stop"], command="other")]
    )
    assert len(updated.hooks) == 1
    assert updated.hooks[0].events == ["Stop"]
    assert updated.hooks[0].command == "other"


async def test_issue_1629_update_missing_config_raises(manager):
    with pytest.raises(ValueError):
        await manager.update_config("does-not-exist", name="X")


async def test_issue_1629_delete_config(manager):
    config = await manager.create_config(name="Audit Logging")
    assert await manager.delete_config(config.id) is True
    assert await manager.get_config(config.id) is None
    assert await manager.delete_config(config.id) is False


def test_issue_1629_get_configs_by_ids_skips_missing_and_disabled():
    manager = HookConfigManager(Path(tempfile.mkdtemp()))
    enabled_cfg = HookConfig(id="a", name="Enabled", slug="enabled", enabled=True)
    disabled_cfg = HookConfig(id="b", name="Disabled", slug="disabled", enabled=False)
    manager.configs = {"a": enabled_cfg, "b": disabled_cfg}

    result = manager.get_configs_by_ids(["a", "b", "missing"])

    assert result == [enabled_cfg]


# ---------------------------------------------------------------------------
# to_sdk_hooks_payload
# ---------------------------------------------------------------------------


def _cfg(*entries: dict, enabled: bool = True) -> HookConfig:
    return HookConfig(
        id="cfg",
        name="cfg",
        slug="cfg",
        enabled=enabled,
        hooks=[HookEntry.from_dict(e) for e in entries],
    )


def test_issue_1629_to_sdk_hooks_payload_empty_when_no_configs():
    manager = HookConfigManager(Path(tempfile.mkdtemp()))
    assert manager.to_sdk_hooks_payload([]) == {}


def test_issue_1629_to_sdk_hooks_payload_single_event_command():
    manager = HookConfigManager(Path(tempfile.mkdtemp()))
    config = _cfg(_entry(events=["PreToolUse"], matcher="Bash", command="echo hi", timeout=10))

    payload = manager.to_sdk_hooks_payload([config])

    assert payload == {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi", "timeout": 10}]}
            ]
        }
    }


def test_issue_1629_to_sdk_hooks_payload_expands_multi_event_entry():
    manager = HookConfigManager(Path(tempfile.mkdtemp()))
    config = _cfg(_entry(events=["PreToolUse", "PostToolUse"], matcher="Bash", command="echo hi"))

    payload = manager.to_sdk_hooks_payload([config])

    assert set(payload["hooks"].keys()) == {"PreToolUse", "PostToolUse"}
    for event in ("PreToolUse", "PostToolUse"):
        assert payload["hooks"][event] == [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
        ]


def test_issue_1629_to_sdk_hooks_payload_omits_matcher_when_unset():
    manager = HookConfigManager(Path(tempfile.mkdtemp()))
    config = _cfg(_entry(events=["Stop"], matcher=None, command="echo hi"))

    payload = manager.to_sdk_hooks_payload([config])

    assert payload["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "echo hi"}]}]


def test_issue_1629_to_sdk_hooks_payload_excludes_disabled_entry():
    manager = HookConfigManager(Path(tempfile.mkdtemp()))
    config = _cfg(_entry(events=["Stop"], enabled=False, command="echo hi"))

    assert manager.to_sdk_hooks_payload([config]) == {}


def test_issue_1629_to_sdk_hooks_payload_excludes_disabled_config():
    manager = HookConfigManager(Path(tempfile.mkdtemp()))
    config = _cfg(_entry(events=["Stop"], command="echo hi"), enabled=False)

    assert manager.to_sdk_hooks_payload([config]) == {}


def test_issue_1629_to_sdk_hooks_payload_http_type_shape():
    manager = HookConfigManager(Path(tempfile.mkdtemp()))
    config = _cfg(_entry(
        events=["Stop"],
        matcher=None,
        type="http",
        command=None,
        url="https://hooks.example.com/complete",
        headers={"Authorization": "Bearer $AUDIT_TOKEN"},
        allowed_env_vars=["AUDIT_TOKEN"],
        timeout=5,
    ))

    payload = manager.to_sdk_hooks_payload([config])

    assert payload["hooks"]["Stop"] == [{
        "hooks": [{
            "type": "http",
            "url": "https://hooks.example.com/complete",
            "timeout": 5,
            "headers": {"Authorization": "Bearer $AUDIT_TOKEN"},
            "allowedEnvVars": ["AUDIT_TOKEN"],
        }]
    }]


def test_issue_1629_to_sdk_hooks_payload_concatenation_order_follows_config_order():
    manager = HookConfigManager(Path(tempfile.mkdtemp()))
    first = _cfg(_entry(events=["Stop"], matcher=None, command="first"))
    second = _cfg(_entry(events=["Stop"], matcher=None, command="second"))

    payload = manager.to_sdk_hooks_payload([first, second])

    commands = [rule["hooks"][0]["command"] for rule in payload["hooks"]["Stop"]]
    assert commands == ["first", "second"]
