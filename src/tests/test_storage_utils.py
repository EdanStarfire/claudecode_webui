"""
Tests for src/storage_utils.py — alphabetized JSON write + backup helpers (issue #1230).
"""

import json
from pathlib import Path

from src.storage_utils import (
    _load_safe,
    backup_legacy_dir_once,
    backup_legacy_sessions_once,
    write_alphabetized_json,
)


class TestWriteAlphabetizedJson:
    def test_keys_sorted(self, tmp_path):
        path = tmp_path / "out.json"
        write_alphabetized_json(path, {"z": 1, "a": 2, "m": 3})
        raw = path.read_text()
        keys = [k for k in ["a", "m", "z"] if k in raw]
        positions = [raw.index(f'"{k}"') for k in keys]
        assert positions == sorted(positions), "Keys should be alphabetically ordered"

    def test_nested_keys_sorted(self, tmp_path):
        path = tmp_path / "nested.json"
        write_alphabetized_json(path, {"config": {"z": 1, "a": 2}, "name": "x"})
        data = json.loads(path.read_text())
        assert list(data.keys()) == sorted(data.keys())
        assert list(data["config"].keys()) == sorted(data["config"].keys())

    def test_round_trip(self, tmp_path):
        path = tmp_path / "rt.json"
        original = {"name": "Test", "config": {"model": "sonnet", "docker_enabled": False}}
        write_alphabetized_json(path, original)
        restored = json.loads(path.read_text())
        assert restored == original

    def test_non_ascii_preserved(self, tmp_path):
        path = tmp_path / "unicode.json"
        write_alphabetized_json(path, {"message": "héllo wörld"})
        assert "héllo wörld" in path.read_text(encoding="utf-8")


class TestLoadSafe:
    def test_valid_json(self, tmp_path):
        path = tmp_path / "valid.json"
        path.write_text('{"key": "value"}', encoding="utf-8")
        assert _load_safe(path) == {"key": "value"}

    def test_missing_file_returns_empty(self, tmp_path):
        assert _load_safe(tmp_path / "nonexistent.json") == {}

    def test_invalid_json_returns_empty(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("not valid json", encoding="utf-8")
        assert _load_safe(path) == {}

    def test_empty_file_returns_empty(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        assert _load_safe(path) == {}


class TestBackupLegacyDirOnce:
    def _write_legacy_template(self, path: Path, data: dict):
        """Write a template JSON without 'config' key (pre-1230 format)."""
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_creates_backup_for_legacy_files(self, tmp_path):
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        self._write_legacy_template(
            templates_dir / "agent.json",
            {"name": "Agent", "permission_mode": "default"},
        )

        created = backup_legacy_dir_once(templates_dir)
        assert created is True
        marker = templates_dir / ".legacy_pre_1230"
        assert marker.exists()
        timestamped = list(marker.iterdir())
        assert len(timestamped) == 1
        backed_up = timestamped[0] / "agent.json"
        assert backed_up.exists()

    def test_idempotent_second_call_is_noop(self, tmp_path):
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        self._write_legacy_template(
            templates_dir / "agent.json",
            {"name": "Agent", "permission_mode": "default"},
        )

        backup_legacy_dir_once(templates_dir)
        created_again = backup_legacy_dir_once(templates_dir)
        assert created_again is False

    def test_no_backup_when_all_migrated(self, tmp_path):
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        # Already migrated — has 'config' key
        (templates_dir / "agent.json").write_text(
            json.dumps({"name": "Agent", "config": {"permission_mode": "default"}}),
            encoding="utf-8",
        )

        created = backup_legacy_dir_once(templates_dir)
        assert created is False
        assert not (templates_dir / ".legacy_pre_1230").exists()

    def test_no_backup_when_dir_empty(self, tmp_path):
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        created = backup_legacy_dir_once(templates_dir)
        assert created is False


class TestBackupLegacySessionsOnce:
    def _write_legacy_state(self, session_dir: Path, data: dict):
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "state.json").write_text(json.dumps(data), encoding="utf-8")

    def test_creates_backup_for_legacy_sessions(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        self._write_legacy_state(
            sessions_dir / "sess-001",
            {"session_id": "sess-001", "permission_mode": "default"},
        )

        created = backup_legacy_sessions_once(sessions_dir)
        assert created is True
        marker = sessions_dir / ".legacy_pre_1230"
        assert marker.exists()
        timestamps = list(marker.iterdir())
        assert len(timestamps) == 1
        backed_up = timestamps[0] / "sess-001" / "state.json"
        assert backed_up.exists()

    def test_idempotent_second_call_is_noop(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        self._write_legacy_state(
            sessions_dir / "sess-001",
            {"session_id": "sess-001", "permission_mode": "default"},
        )

        backup_legacy_sessions_once(sessions_dir)
        created_again = backup_legacy_sessions_once(sessions_dir)
        assert created_again is False

    def test_no_backup_when_all_migrated(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        self._write_legacy_state(
            sessions_dir / "sess-001",
            {"session_id": "sess-001", "config": {"permission_mode": "default"}},
        )

        created = backup_legacy_sessions_once(sessions_dir)
        assert created is False
