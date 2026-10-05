"""Tests for migration_status_report_cli.py (issue #2084 stage 3-D-prep, §6) —
the read-only scan-all tool that's the actual go/no-go signal for 3-D-cutover.

Not under backend/tests/ (pytest's testpaths) — opt-in, invoked explicitly:
    uv run pytest backend/tools/test_migration_status_report_cli.py
"""

import json
from pathlib import Path

from backend.models.messages import CURRENT_MESSAGE_SCHEMA_VERSION
from backend.tools.migration_status_report_cli import _scan_archives, _scan_live, run


def _write_state(path: Path, session_id: str, schema_version: int, status: dict | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "message_schema_version": schema_version,
                "message_migration_status": status,
            }
        ),
        encoding="utf-8",
    )


class TestScanLive:
    def test_buckets_by_status(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        _write_state(
            sessions_dir / "s-canon" / "state.json",
            "s-canon", CURRENT_MESSAGE_SCHEMA_VERSION, {"state": "completed"},
        )
        _write_state(sessions_dir / "s-never" / "state.json", "s-never", 0, None)
        _write_state(
            sessions_dir / "s-prog" / "state.json", "s-prog", 0, {"state": "in_progress"}
        )
        _write_state(
            sessions_dir / "s-quar" / "state.json", "s-quar", 0, {"state": "quarantined"}
        )

        report = _scan_live(sessions_dir)

        assert report.canonical == 1
        assert report.never_attempted == 1
        assert report.in_progress == 1
        assert report.quarantined == 1
        assert report.quarantined_ids == ["s-quar"]
        assert report.total == 4
        assert report.fully_canonical is False

    def test_missing_dir_is_empty_and_fully_canonical(self, tmp_path):
        report = _scan_live(tmp_path / "nonexistent")
        assert report.total == 0
        assert report.fully_canonical is True

    def test_unreadable_state_json_tracked_separately_from_buckets(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        bad = sessions_dir / "s-bad"
        bad.mkdir(parents=True)
        (bad / "state.json").write_text("NOT JSON", encoding="utf-8")

        report = _scan_live(sessions_dir)
        assert report.unreadable == ["s-bad"]
        assert report.total == 0

    def test_session_dir_without_state_json_skipped(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        (sessions_dir / "s-empty").mkdir(parents=True)

        report = _scan_live(sessions_dir)
        assert report.total == 0
        assert report.unreadable == []


class TestScanArchives:
    def test_buckets_nested_minion_timestamp_structure(self, tmp_path):
        archives_dir = tmp_path / "archives" / "minions"
        _write_state(
            archives_dir / "m1" / "20260101_000000" / "state.json",
            "m1", CURRENT_MESSAGE_SCHEMA_VERSION, {"state": "completed"},
        )
        _write_state(
            archives_dir / "m2" / "20260101_000001" / "state.json",
            "m2", 0, {"state": "quarantined"},
        )
        _write_state(
            archives_dir / "m3" / "20260101_000002" / "state.json",
            "m3", 0, None,
        )

        report = _scan_archives(archives_dir)

        assert report.canonical == 1
        assert report.quarantined == 1
        assert report.never_attempted == 1
        assert report.quarantined_ids == ["m2"]

    def test_missing_dir_is_empty_and_fully_canonical(self, tmp_path):
        report = _scan_archives(tmp_path / "nonexistent")
        assert report.total == 0
        assert report.fully_canonical is True


class TestRun:
    def test_exit_zero_when_both_populations_fully_canonical(self, tmp_path, capsys):
        _write_state(
            tmp_path / "sessions" / "s1" / "state.json",
            "s1", CURRENT_MESSAGE_SCHEMA_VERSION, {"state": "completed"},
        )
        _write_state(
            tmp_path / "archives" / "minions" / "m1" / "ts1" / "state.json",
            "m1", CURRENT_MESSAGE_SCHEMA_VERSION, {"state": "completed"},
        )

        exit_code = run(tmp_path)

        assert exit_code == 0
        assert "OK" in capsys.readouterr().out

    def test_exit_nonzero_when_live_population_has_legacy_session(self, tmp_path, capsys):
        _write_state(tmp_path / "sessions" / "s1" / "state.json", "s1", 0, None)

        exit_code = run(tmp_path)

        assert exit_code == 1
        assert "NOT READY" in capsys.readouterr().out

    def test_exit_nonzero_when_archive_population_has_legacy_session(self, tmp_path, capsys):
        _write_state(
            tmp_path / "archives" / "minions" / "m1" / "ts1" / "state.json", "m1", 0, None
        )

        exit_code = run(tmp_path)

        assert exit_code == 1

    def test_quarantined_ids_printed_in_output(self, tmp_path, capsys):
        _write_state(
            tmp_path / "sessions" / "s-quar" / "state.json",
            "s-quar", 0, {"state": "quarantined"},
        )

        run(tmp_path)

        assert "s-quar" in capsys.readouterr().out

    def test_run_never_mutates_state_json(self, tmp_path):
        """Strictly read-only by design — no --apply, no mutation capability at all."""
        state_path = tmp_path / "sessions" / "s1" / "state.json"
        _write_state(state_path, "s1", 0, None)
        before = state_path.read_text(encoding="utf-8")

        run(tmp_path)

        assert state_path.read_text(encoding="utf-8") == before
