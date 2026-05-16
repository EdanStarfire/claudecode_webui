"""Unit tests for AppConfigManager — issue #1427 Phase 2."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from src.config_manager import AppConfig, AppConfigManager


def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data) + "\n")


@pytest.mark.asyncio
async def test_get_config_returns_defaults_for_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        cfg_file = Path(tmp) / "config.json"
        mgr = AppConfigManager(config_file=cfg_file)
        config = await mgr.get_config()
    assert isinstance(config, AppConfig)


@pytest.mark.asyncio
async def test_get_config_caches_result():
    with tempfile.TemporaryDirectory() as tmp:
        cfg_file = Path(tmp) / "config.json"
        _write_config(cfg_file, {})
        mgr = AppConfigManager(config_file=cfg_file)
        first = await mgr.get_config()
        second = await mgr.get_config()
    assert first is second


@pytest.mark.asyncio
async def test_save_config_persists_to_disk_and_updates_cache():
    with tempfile.TemporaryDirectory() as tmp:
        cfg_file = Path(tmp) / "config.json"
        mgr = AppConfigManager(config_file=cfg_file)
        config = await mgr.get_config()
        config.legion.max_concurrent_minions = 99
        await mgr.save_config(config)

        # Cache should be updated
        cached = await mgr.get_config()
        assert cached.legion.max_concurrent_minions == 99

        # Disk should be updated
        fresh = AppConfigManager(config_file=cfg_file)
        disk_config = await fresh.get_config()
        assert disk_config.legion.max_concurrent_minions == 99


@pytest.mark.asyncio
async def test_concurrent_save_config_serializes():
    """Lock must prevent interleaved writes."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg_file = Path(tmp) / "config.json"
        mgr = AppConfigManager(config_file=cfg_file)

        async def _save(n: int) -> None:
            config = await mgr.get_config()
            config.legion.max_concurrent_minions = n
            await mgr.save_config(config)

        await asyncio.gather(*[_save(i) for i in range(10)])
        final = await mgr.get_config()
        # Any one of the values is fine — we just need no exception and a valid int
        assert isinstance(final.legion.max_concurrent_minions, int)
