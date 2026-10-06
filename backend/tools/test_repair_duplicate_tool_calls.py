"""Tests for repair_duplicate_tool_calls.py (issue #2093 AC5).

Not under backend/tests/ (pytest's testpaths) — opt-in, invoked explicitly:
    uv run pytest backend/tools/test_repair_duplicate_tool_calls.py
"""

import json
from pathlib import Path

from backend.message_migration import _materialized_record_id
from backend.tools.repair_duplicate_tool_calls import (
    _scan_all_targets,
    find_and_repair_duplicate_tool_calls,
)

SESSION_ID = "sess-1"


def _write_messages(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _tool_call(tool_use_id: str, status: str, message_id: str) -> dict:
    return {
        "type": "tool_call",
        "tool_use_id": tool_use_id,
        "status": status,
        "message_id": message_id,
        "name": "Bash",
        "input": {},
    }


class TestDryRun:
    def test_reports_duplicate_without_modifying_file(self, tmp_path):
        messages_path = tmp_path / "messages.jsonl"
        synthesized_id = _materialized_record_id(SESSION_ID, "tu-1", "interrupted")
        records = [
            _tool_call("tu-1", "interrupted", "genuine-id"),
            _tool_call("tu-1", "interrupted", synthesized_id),
        ]
        _write_messages(messages_path, records)
        original_content = messages_path.read_text(encoding="utf-8")

        result = find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=False)

        assert len(result.repaired) == 1
        assert result.repaired[0].tool_use_id == "tu-1"
        assert result.ambiguous == []
        assert messages_path.read_text(encoding="utf-8") == original_content


class TestMismatchedStatusDuplicate:
    """Issue #2093 (found in review): finalize() always forces the
    SYNTHESIZED record's status to "interrupted", regardless of what status
    the genuine terminal record already had — so a real-world duplicate pair
    can have DIFFERENT statuses (e.g. genuine "completed" + synthesized
    "interrupted"). Grouping by (tool_use_id, status) alone would miss this
    entirely; the tool must group by tool_use_id among TERMINAL records."""

    def test_genuine_completed_plus_synthesized_interrupted_is_detected(self, tmp_path):
        messages_path = tmp_path / "messages.jsonl"
        synthesized_id = _materialized_record_id(SESSION_ID, "tu-1", "interrupted")
        records = [
            _tool_call("tu-1", "completed", "genuine-id"),
            _tool_call("tu-1", "interrupted", synthesized_id),
        ]
        _write_messages(messages_path, records)

        result = find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=True)

        assert len(result.repaired) == 1
        assert result.repaired[0].tool_use_id == "tu-1"
        remaining = [
            json.loads(line)
            for line in messages_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(remaining) == 1
        assert remaining[0]["message_id"] == "genuine-id"
        assert remaining[0]["status"] == "completed"

    def test_genuine_failed_plus_synthesized_interrupted_is_detected(self, tmp_path):
        messages_path = tmp_path / "messages.jsonl"
        synthesized_id = _materialized_record_id(SESSION_ID, "tu-1", "interrupted")
        records = [
            _tool_call("tu-1", "failed", "genuine-id"),
            _tool_call("tu-1", "interrupted", synthesized_id),
        ]
        _write_messages(messages_path, records)

        result = find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=False)

        assert len(result.repaired) == 1
        assert result.repaired[0].tool_use_id == "tu-1"

    def test_non_terminal_plus_terminal_is_not_a_duplicate(self, tmp_path):
        """A tool's normal single lifecycle (e.g. pending -> completed) is two
        real records for the same tool_use_id but must never be flagged —
        only 2+ TERMINAL-state records for one tool_use_id is the signal."""
        messages_path = tmp_path / "messages.jsonl"
        records = [
            _tool_call("tu-1", "pending", "id-1"),
            _tool_call("tu-1", "running", "id-2"),
            _tool_call("tu-1", "completed", "id-3"),
        ]
        _write_messages(messages_path, records)

        result = find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=True)

        assert result.clean


class TestApply:
    def test_removes_exactly_the_synthesized_copy(self, tmp_path):
        messages_path = tmp_path / "messages.jsonl"
        synthesized_id = _materialized_record_id(SESSION_ID, "tu-1", "interrupted")
        records = [
            _tool_call("tu-1", "interrupted", "genuine-id"),
            _tool_call("tu-1", "interrupted", synthesized_id),
        ]
        _write_messages(messages_path, records)

        result = find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=True)

        assert len(result.repaired) == 1
        remaining = [
            json.loads(line)
            for line in messages_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(remaining) == 1
        assert remaining[0]["message_id"] == "genuine-id"

    def test_second_apply_is_idempotent_noop(self, tmp_path):
        messages_path = tmp_path / "messages.jsonl"
        synthesized_id = _materialized_record_id(SESSION_ID, "tu-1", "interrupted")
        records = [
            _tool_call("tu-1", "interrupted", "genuine-id"),
            _tool_call("tu-1", "interrupted", synthesized_id),
        ]
        _write_messages(messages_path, records)

        find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=True)
        second = find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=True)

        assert second.clean
        remaining = [
            json.loads(line)
            for line in messages_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(remaining) == 1


class TestCleanFileUntouched:
    def test_no_duplicates_left_alone_in_dry_run(self, tmp_path):
        messages_path = tmp_path / "messages.jsonl"
        records = [
            _tool_call("tu-1", "pending", "id-1"),
            _tool_call("tu-1", "completed", "id-2"),
        ]
        _write_messages(messages_path, records)
        original_content = messages_path.read_text(encoding="utf-8")

        result = find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=False)

        assert result.clean
        assert messages_path.read_text(encoding="utf-8") == original_content

    def test_no_duplicates_left_alone_in_apply(self, tmp_path):
        messages_path = tmp_path / "messages.jsonl"
        records = [
            _tool_call("tu-1", "pending", "id-1"),
            _tool_call("tu-1", "completed", "id-2"),
        ]
        _write_messages(messages_path, records)
        original_content = messages_path.read_text(encoding="utf-8")

        result = find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=True)

        assert result.clean
        assert messages_path.read_text(encoding="utf-8") == original_content


class TestAmbiguousCases:
    def test_both_candidates_matching_deterministic_id_is_ambiguous(self, tmp_path):
        messages_path = tmp_path / "messages.jsonl"
        synthesized_id = _materialized_record_id(SESSION_ID, "tu-1", "interrupted")
        records = [
            _tool_call("tu-1", "interrupted", synthesized_id),
            _tool_call("tu-1", "interrupted", synthesized_id),
        ]
        _write_messages(messages_path, records)
        original_content = messages_path.read_text(encoding="utf-8")

        result = find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=True)

        assert result.repaired == []
        assert len(result.ambiguous) == 1
        # Ambiguous cases must never be touched, even in apply mode.
        assert messages_path.read_text(encoding="utf-8") == original_content

    def test_neither_candidate_matching_deterministic_id_is_ambiguous(self, tmp_path):
        messages_path = tmp_path / "messages.jsonl"
        records = [
            _tool_call("tu-1", "interrupted", "id-a"),
            _tool_call("tu-1", "interrupted", "id-b"),
        ]
        _write_messages(messages_path, records)
        original_content = messages_path.read_text(encoding="utf-8")

        result = find_and_repair_duplicate_tool_calls(messages_path, SESSION_ID, apply=True)

        assert result.repaired == []
        assert len(result.ambiguous) == 1
        assert messages_path.read_text(encoding="utf-8") == original_content


class TestScanAllTargets:
    def test_finds_only_canonical_live_sessions_and_archives(self, tmp_path):
        from backend.models.messages import CURRENT_MESSAGE_SCHEMA_VERSION

        def _write_state(path, session_id, schema_version):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"session_id": session_id, "message_schema_version": schema_version}),
                encoding="utf-8",
            )

        sessions_dir = tmp_path / "sessions"
        _write_state(sessions_dir / "s-canon" / "state.json", "s-canon", CURRENT_MESSAGE_SCHEMA_VERSION)
        (sessions_dir / "s-canon" / "messages.jsonl").write_text("", encoding="utf-8")
        _write_state(sessions_dir / "s-legacy" / "state.json", "s-legacy", 0)
        (sessions_dir / "s-legacy" / "messages.jsonl").write_text("", encoding="utf-8")

        archives_dir = tmp_path / "archives" / "minions"
        _write_state(
            archives_dir / "m1" / "ts1" / "state.json", "m1-session", CURRENT_MESSAGE_SCHEMA_VERSION
        )
        (archives_dir / "m1" / "ts1" / "messages.jsonl").write_text("", encoding="utf-8")

        targets = _scan_all_targets(tmp_path)
        target_ids = {session_id for session_id, _ in targets}

        assert target_ids == {"s-canon", "m1-session"}

    def test_skips_migration_backups_and_legacy_verify_dirs(self, tmp_path):
        """Issue #2094 (AC3, found in review): --scan-all must apply the same
        _is_ignored_population_dir() filter as migration_status_report_cli.py
        and backfill_archives_cli.py, not reimplement an unfiltered walk."""
        from backend.models.messages import CURRENT_MESSAGE_SCHEMA_VERSION

        def _write_state(path, session_id, schema_version):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"session_id": session_id, "message_schema_version": schema_version}),
                encoding="utf-8",
            )

        sessions_dir = tmp_path / "sessions"
        _write_state(
            sessions_dir / "s-real" / "state.json", "s-real", CURRENT_MESSAGE_SCHEMA_VERSION
        )
        (sessions_dir / "s-real" / "messages.jsonl").write_text("", encoding="utf-8")
        _write_state(
            sessions_dir / "migration-backups" / "s-real" / "state.json",
            "s-real", CURRENT_MESSAGE_SCHEMA_VERSION,
        )
        (sessions_dir / "migration-backups" / "s-real" / "messages.jsonl").write_text(
            "", encoding="utf-8"
        )

        archives_dir = tmp_path / "archives" / "minions"
        _write_state(
            archives_dir / "m1" / "ts1" / "state.json", "m1", CURRENT_MESSAGE_SCHEMA_VERSION
        )
        (archives_dir / "m1" / "ts1" / "messages.jsonl").write_text("", encoding="utf-8")
        _write_state(
            archives_dir / "m1" / "ts1-migration-verify-20260101_000000_000000" / "state.json",
            "m1", 0,
        )
        (
            archives_dir / "m1" / "ts1-migration-verify-20260101_000000_000000" / "messages.jsonl"
        ).write_text("", encoding="utf-8")

        targets = _scan_all_targets(tmp_path)
        target_ids = {session_id for session_id, _ in targets}

        assert target_ids == {"s-real", "m1"}
        assert len(targets) == 2
