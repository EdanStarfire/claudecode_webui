"""Tests for cleanup_thinking_tokens_cli.py (issue #2136) — the one-off cleanup
tool for already-persisted thinking_tokens bloat.

Not under backend/tests/ (pytest's testpaths) — opt-in, invoked explicitly:
    uv run pytest backend/tools/test_cleanup_thinking_tokens_cli.py
"""

import json
from pathlib import Path

from backend.tools.cleanup_thinking_tokens_cli import (
    _BACKUP_DIR_NAME,
    _process_archive,
    _process_live_session,
    _scan_live,
    run,
)


def _thinking_record(msg_id: str) -> dict:
    return {
        "type": "system",
        "subtype": "thinking_tokens",
        "timestamp": 123.456,
        "session_id": "s1",
        "message_id": msg_id,
        "content": "System message",
        "metadata": {"foo": "bar"},
    }


def _regular_record(msg_id: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": 123.456,
        "session_id": "s1",
        "message_id": msg_id,
        "content": f"hello {msg_id}",
    }


def _jsonl_bytes(records: list[dict]) -> bytes:
    return ("\n".join(json.dumps(r) for r in records) + "\n").encode("utf-8")


def _write_messages(path: Path, records: list[dict]) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _jsonl_bytes(records)
    path.write_bytes(content)
    return content


def _write_state(path: Path, session_id: str, *, state: str = "terminated", is_processing: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"session_id": session_id, "state": state, "is_processing": is_processing}),
        encoding="utf-8",
    )


_MIXED_RECORDS = [
    _thinking_record("tt-1"),
    _regular_record("m-1"),
    _thinking_record("tt-2"),
    _regular_record("m-2"),
]


class TestDryRun:
    def test_reports_counts_and_leaves_files_untouched(self, tmp_path):
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        original = _write_messages(messages_path, _MIXED_RECORDS)
        _write_state(session_dir / "state.json", "s1", state="terminated")

        result = _process_live_session(session_dir, apply=False, force=False)

        assert result.removed_count == 2
        assert result.bytes_reclaimed > 0
        assert result.applied is False
        assert result.error is None
        assert messages_path.read_bytes() == original

    def test_run_exits_zero_and_writes_nothing(self, tmp_path):
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        original = _write_messages(messages_path, _MIXED_RECORDS)
        _write_state(session_dir / "state.json", "s1", state="terminated")

        exit_code = run(tmp_path, apply=False, force=False)

        assert exit_code == 0
        assert messages_path.read_bytes() == original
        assert not (tmp_path / "sessions" / "migration-backups").exists()


class TestApply:
    def test_removes_records_byte_identical_and_backs_up(self, tmp_path):
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        state_path = session_dir / "state.json"
        original = _write_messages(messages_path, _MIXED_RECORDS)
        _write_state(state_path, "s1", state="terminated")
        original_state = state_path.read_bytes()

        result = _process_live_session(session_dir, apply=True, force=False)

        assert result.applied is True
        assert result.removed_count == 2
        assert result.error is None

        new_content = messages_path.read_bytes()
        surviving_lines = [line for line in new_content.split(b"\n") if line]
        assert len(surviving_lines) == 2
        expected_survivors = [
            json.dumps(_regular_record("m-1")).encode("utf-8"),
            json.dumps(_regular_record("m-2")).encode("utf-8"),
        ]
        assert surviving_lines == expected_survivors

        backup_dir = session_dir.parent / _BACKUP_DIR_NAME / "s1"
        assert (backup_dir / "messages.jsonl").read_bytes() == original
        assert (backup_dir / "state.json").read_bytes() == original_state

    def test_idempotent_second_apply_is_noop(self, tmp_path):
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        _write_messages(messages_path, _MIXED_RECORDS)
        _write_state(session_dir / "state.json", "s1", state="terminated")

        first = _process_live_session(session_dir, apply=True, force=False)
        assert first.applied is True
        mtime_before = messages_path.stat().st_mtime_ns
        backup_messages_before = (
            session_dir.parent / _BACKUP_DIR_NAME / "s1" / "messages.jsonl"
        ).read_bytes()

        second = _process_live_session(session_dir, apply=True, force=False)

        assert second.removed_count == 0
        assert second.applied is False
        assert messages_path.stat().st_mtime_ns == mtime_before
        backup_messages_after = (
            session_dir.parent / _BACKUP_DIR_NAME / "s1" / "messages.jsonl"
        ).read_bytes()
        assert backup_messages_after == backup_messages_before

    def test_apply_does_not_collide_with_retired_schema_migration_backup(self, tmp_path):
        """The now-deleted migrate_session_verify_cli.py used the exact path
        <sessions_dir>/migration-backups/<session_id>/ for its own, unrelated
        schema-migration backups. Production sessions migrated via that tool
        still have that directory on disk. This tool must never treat that
        leftover as "already backed up" for its own, unrelated purpose (found in
        review: doing so would silently skip saving the real pre-cleanup
        messages.jsonl before overwriting it)."""
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        original = _write_messages(messages_path, _MIXED_RECORDS)
        _write_state(session_dir / "state.json", "s1", state="terminated")

        stale_backup_dir = tmp_path / "sessions" / "migration-backups" / "s1"
        stale_backup_dir.mkdir(parents=True)
        stale_content = b'{"type": "assistant", "content": "unrelated pre-migration backup"}\n'
        (stale_backup_dir / "messages.jsonl").write_bytes(stale_content)

        result = _process_live_session(session_dir, apply=True, force=False)

        assert result.applied is True
        assert result.backup_skipped is False
        # The old tool's unrelated backup is untouched.
        assert (stale_backup_dir / "messages.jsonl").read_bytes() == stale_content
        # This tool's own, real pre-cleanup backup was written to its own namespace.
        real_backup_dir = tmp_path / "sessions" / _BACKUP_DIR_NAME / "s1"
        assert (real_backup_dir / "messages.jsonl").read_bytes() == original


class TestArchivePath:
    def test_archive_filtered_backed_up_and_idempotent(self, tmp_path):
        archive_dir = tmp_path / "archives" / "minions" / "m1" / "ts1"
        messages_path = archive_dir / "messages.jsonl"
        state_path = archive_dir / "state.json"
        original = _write_messages(messages_path, _MIXED_RECORDS)
        _write_state(state_path, "m1", state="terminated")
        original_state = state_path.read_bytes()

        result = _process_archive(archive_dir, "m1", True)

        assert result.applied is True
        assert result.removed_count == 2

        backup_dir = tmp_path / "archives" / _BACKUP_DIR_NAME / "m1" / "ts1"
        assert (backup_dir / "messages.jsonl").read_bytes() == original
        assert (backup_dir / "state.json").read_bytes() == original_state

        new_content = messages_path.read_bytes()
        surviving_lines = [line for line in new_content.split(b"\n") if line]
        assert surviving_lines == [
            json.dumps(_regular_record("m-1")).encode("utf-8"),
            json.dumps(_regular_record("m-2")).encode("utf-8"),
        ]

        mtime_before = messages_path.stat().st_mtime_ns
        second = _process_archive(archive_dir, "m1", True)
        assert second.removed_count == 0
        assert second.applied is False
        assert messages_path.stat().st_mtime_ns == mtime_before


class TestActiveSessionGuard:
    def test_apply_without_force_skips_active_session(self, tmp_path):
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        original = _write_messages(messages_path, _MIXED_RECORDS)
        _write_state(session_dir / "state.json", "s1", state="active")

        result = _process_live_session(session_dir, apply=True, force=False)

        assert result.skipped_active is True
        assert result.applied is False
        assert messages_path.read_bytes() == original
        # Dry-run-equivalent reporting still happens even though skipped.
        assert result.removed_count == 2

    def test_apply_with_force_proceeds_on_active_session(self, tmp_path):
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        _write_messages(messages_path, _MIXED_RECORDS)
        _write_state(session_dir / "state.json", "s1", state="active")

        result = _process_live_session(session_dir, apply=True, force=True)

        assert result.skipped_active is True
        assert result.applied is True
        assert result.removed_count == 2

    def test_is_processing_true_also_triggers_guard(self, tmp_path):
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        original = _write_messages(messages_path, _MIXED_RECORDS)
        _write_state(session_dir / "state.json", "s1", state="paused", is_processing=True)

        result = _process_live_session(session_dir, apply=True, force=False)

        assert result.skipped_active is True
        assert result.applied is False
        assert messages_path.read_bytes() == original

    def test_non_dict_state_json_is_treated_as_active_not_a_crash(self, tmp_path):
        """A state.json that parses as valid JSON but isn't an object (e.g. a
        crash mid-write, or external corruption) must not crash the scan, and
        must not be silently treated as "confirmed inactive" either — the safe
        default is to assume active and let --force override."""
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        original = _write_messages(messages_path, _MIXED_RECORDS)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "state.json").write_text("[]", encoding="utf-8")

        dry_run_result = _process_live_session(session_dir, apply=False, force=False)
        assert dry_run_result.error is None
        assert dry_run_result.skipped_active is True
        assert dry_run_result.removed_count == 2

        apply_result = _process_live_session(session_dir, apply=True, force=False)
        assert apply_result.error is None
        assert apply_result.applied is False
        assert messages_path.read_bytes() == original

        forced_result = _process_live_session(session_dir, apply=True, force=True)
        assert forced_result.error is None
        assert forced_result.applied is True

    def test_unreadable_state_json_is_treated_as_active_not_a_crash(self, tmp_path):
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        _write_messages(messages_path, _MIXED_RECORDS)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "state.json").write_text("NOT JSON", encoding="utf-8")

        result = _process_live_session(session_dir, apply=False, force=False)

        assert result.error is None
        assert result.skipped_active is True
        assert result.removed_count == 2


class TestIgnoredDirs:
    def test_migration_backups_dir_not_treated_as_a_session(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        _write_messages(sessions_dir / "s-real" / "messages.jsonl", _MIXED_RECORDS)
        _write_state(sessions_dir / "s-real" / "state.json", "s-real", state="terminated")
        # A prior run's own backup directory sitting alongside real sessions.
        _write_messages(
            sessions_dir / "migration-backups" / "s-real" / "messages.jsonl", _MIXED_RECORDS
        )
        _write_state(
            sessions_dir / "migration-backups" / "s-real" / "state.json",
            "s-real", state="terminated",
        )

        report = _scan_live(sessions_dir, apply=False, force=False)

        assert set(report.per_target.keys()) == {"s-real"}

    def test_own_backup_dir_not_treated_as_a_session_on_rescan(self, tmp_path):
        """After a real --apply run creates this tool's own backup directory
        (_BACKUP_DIR_NAME), a subsequent scan must not walk into it as if it
        were a session to clean."""
        sessions_dir = tmp_path / "sessions"
        _write_messages(sessions_dir / "s-real" / "messages.jsonl", _MIXED_RECORDS)
        _write_state(sessions_dir / "s-real" / "state.json", "s-real", state="terminated")

        first = _scan_live(sessions_dir, apply=True, force=False)
        assert first.per_target["s-real"].applied is True
        assert (sessions_dir / _BACKUP_DIR_NAME / "s-real").is_dir()

        second = _scan_live(sessions_dir, apply=False, force=False)

        assert set(second.per_target.keys()) == {"s-real"}

    def test_legacy_migration_verify_suffix_dir_not_counted(self, tmp_path):
        """Issue #2094 AC3's legacy dry-run leftover pattern
        (<id>-migration-verify-<timestamp>) must be excluded exactly like
        migration_status_report_cli.py does."""
        sessions_dir = tmp_path / "sessions"
        _write_messages(sessions_dir / "s-real" / "messages.jsonl", _MIXED_RECORDS)
        _write_state(sessions_dir / "s-real" / "state.json", "s-real", state="terminated")
        _write_messages(
            sessions_dir / "s-real-migration-verify-20260101_000000_000000" / "messages.jsonl",
            _MIXED_RECORDS,
        )

        report = _scan_live(sessions_dir, apply=False, force=False)

        assert set(report.per_target.keys()) == {"s-real"}


class TestMalformedLineResilience:
    def test_non_json_and_unexpected_shape_lines_kept_untouched(self, tmp_path):
        session_dir = tmp_path / "sessions" / "s1"
        messages_path = session_dir / "messages.jsonl"
        _write_state(session_dir / "state.json", "s1", state="terminated")
        session_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            json.dumps(_thinking_record("tt-1")),
            "NOT JSON AT ALL",
            json.dumps([1, 2, 3]),  # valid JSON, unexpected shape (not a dict)
            json.dumps(_regular_record("m-1")),
        ]
        original = ("\n".join(lines) + "\n").encode("utf-8")
        messages_path.write_bytes(original)

        result = _process_live_session(session_dir, apply=True, force=False)

        assert result.removed_count == 1
        assert result.error is None

        new_content = messages_path.read_bytes()
        surviving_lines = [line for line in new_content.split(b"\n") if line]
        assert surviving_lines == [
            b"NOT JSON AT ALL",
            json.dumps([1, 2, 3]).encode("utf-8"),
            json.dumps(_regular_record("m-1")).encode("utf-8"),
        ]
