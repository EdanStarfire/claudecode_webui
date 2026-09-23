"""Tests for fixture_export.py (issue #1998, T2 — missing-marker export failure)."""

import json
import tempfile
import uuid
from pathlib import Path

import pytest

from backend.fixture_export import (
    REQUIRED_MARKERS,
    FixtureExportError,
    _check_markers,
    export_fixture,
)
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator
from backend.session_recorder import SessionRecorder


@pytest.fixture
async def coordinator():
    with tempfile.TemporaryDirectory() as temp_dir:
        c = SessionCoordinator(Path(temp_dir))
        await c.initialize()
        yield c
        await c.cleanup()


@pytest.fixture
async def session_with_dir(coordinator):
    project = await coordinator.project_manager.create_project(
        name="Test Project", working_directory="/tmp"
    )
    session_id = str(uuid.uuid4())
    await coordinator.create_session(
        session_id=session_id,
        project_id=project.project_id,
        config=SessionConfig(recording_enabled=True),
    )
    session_dir = await coordinator.session_manager.get_session_directory(session_id)
    return coordinator, session_id, session_dir


def _write_full_coverage_raw_log(recorder: SessionRecorder) -> None:
    """Writes one raw_log record per required marker, using the exact record
    shapes _check_markers() looks for."""
    recorder._write("sdk_message", lambda: {"_type": "StreamEvent", "data": {}})
    recorder.record_permission_invocation("Read", {"file_path": "/tmp/x"})
    recorder.record_permission_response("Bash", "deny", "blocked")
    recorder.record_permission_invocation("AskUserQuestion", {"question": "which?"})
    recorder._write("sdk_message", lambda: {"_type": "TaskProgressMessage", "data": {}})
    recorder.record_interrupt()
    recorder.record_lifecycle("restart")
    recorder._write(
        "sdk_message",
        lambda: {"_type": "SystemMessage", "data": {"subtype": "compact_boundary"}},
    )
    # Inter-minion comm delivery arrives as an ordinary "user" message wrapped in
    # BackendApp's poll-queue envelope ({"type": "message", "data": {"type": "user",
    # "metadata": {"comm": {...}}}}) — confirmed against a real captured raw_log
    # (2026-09-23). Not a queue event literally typed "comm", and not metadata
    # directly on the envelope (that level is never populated).
    recorder.record_queue_event({
        "type": "message",
        "data": {"type": "user", "metadata": {"comm": {"from_minion_id": "m1"}}},
    })


class TestCheckMarkers:
    """Unit-level marker detection, isolated from the coordinator/session plumbing."""

    def test_empty_log_misses_everything(self):
        found = _check_markers([])
        assert all(not present for present in found.values())

    def test_full_coverage_log_finds_everything(self, tmp_path):
        recorder = SessionRecorder("sess-1", tmp_path)
        _write_full_coverage_raw_log(recorder)
        records = [
            json.loads(line)
            for line in recorder.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        found = _check_markers(records)
        assert all(found.values()), found

    def test_ask_user_question_detected_via_assistant_tool_use(self):
        records = [{
            "kind": "sdk_message",
            "_type": "AssistantMessage",
            "data": {"content": [{"id": "tu1", "name": "AskUserQuestion", "input": {}}]},
        }]
        found = _check_markers(records)
        assert found["AskUserQuestion"] is True

    def test_issue_1998_microcompact_boundary_is_not_a_compaction_match(self):
        """Regression: a loose substring match on 'compact' previously false-positived
        on microcompact_boundary, which the codebase explicitly treats as NOT a real
        compaction (SessionCoordinator._handle_compact_boundary)."""
        records = [{
            "kind": "sdk_message", "_type": "SystemMessage",
            "data": {"subtype": "microcompact_boundary"},
        }]
        found = _check_markers(records)
        assert found["compaction"] is False

    def test_issue_1998_compact_boundary_exact_match_is_detected(self):
        records = [{
            "kind": "sdk_message", "_type": "SystemMessage",
            "data": {"subtype": "compact_boundary"},
        }]
        found = _check_markers(records)
        assert found["compaction"] is True

    def test_issue_1998_comm_queue_event_type_alone_is_not_a_comm_match(self):
        """Regression: inter-minion comm delivery arrives as an ordinary 'user' queue
        event carrying metadata.comm (comm_router.py's comm_metadata), not a queue
        event literally typed 'comm' — a substring match on event.type never fires
        for a real comm delivery."""
        records = [{"kind": "queue_event", "event": {"type": "comm_received"}}]
        found = _check_markers(records)
        assert found["inter-minion comm"] is False

    def test_issue_1998_comm_metadata_at_wrong_nesting_level_is_not_a_match(self):
        """Regression: the raw log's queue_event.event is BackendApp's poll-queue
        envelope ({"type": "message", "data": {...}}) — metadata lives at
        event.data.metadata, never directly on event.metadata. An earlier fix
        checked the wrong level and passed only because the test fixture that
        exercised it made the same wrong assumption; caught against a real
        captured raw_log on 2026-09-23 where every genuine comm delivery was
        still reported missing despite two real deliveries being present."""
        records = [{
            "kind": "queue_event",
            "event": {"type": "user", "metadata": {"comm": {"from_minion_id": "m1"}}},
        }]
        found = _check_markers(records)
        assert found["inter-minion comm"] is False

    def test_issue_1998_comm_metadata_is_detected(self):
        records = [{
            "kind": "queue_event",
            "event": {
                "type": "message",
                "data": {"type": "user", "metadata": {"comm": {"from_minion_id": "m1"}}},
            },
        }]
        found = _check_markers(records)
        assert found["inter-minion comm"] is True


class TestExportFixture:
    @pytest.mark.asyncio
    async def test_missing_markers_raises_naming_specifics(self, session_with_dir, tmp_path):
        coordinator, session_id, session_dir = session_with_dir
        recorder = SessionRecorder(session_id, session_dir)
        # Only cover 2 of the 9 markers.
        recorder._write("sdk_message", lambda: {"_type": "StreamEvent", "data": {}})
        recorder.record_interrupt()

        with pytest.raises(FixtureExportError) as exc_info:
            await export_fixture(coordinator, session_id, "test-fixture", fixtures_root=tmp_path)

        missing = set(exc_info.value.missing_markers)
        assert "streaming deltas" not in missing
        assert "interrupt mid-tool" not in missing
        assert missing == set(REQUIRED_MARKERS) - {"streaming deltas", "interrupt mid-tool"}
        # A failed export must never leave a partial fixture directory behind.
        assert not (tmp_path / "test-fixture").exists()

    @pytest.mark.asyncio
    async def test_full_coverage_exports_successfully(self, session_with_dir, tmp_path):
        coordinator, session_id, session_dir = session_with_dir
        recorder = SessionRecorder(session_id, session_dir)
        _write_full_coverage_raw_log(recorder)

        result = await export_fixture(coordinator, session_id, "test-fixture", fixtures_root=tmp_path)

        assert result.success is True
        assert all(result.markers.values())
        fixture_dir = tmp_path / "test-fixture"
        assert (fixture_dir / "raw_log.jsonl").exists()
        assert (fixture_dir / "provenance.json").exists()
        assert (fixture_dir / "rest_history.json").exists()

        provenance = json.loads((fixture_dir / "provenance.json").read_text())
        assert "sdk_version" in provenance
        assert "capture_date" in provenance

    @pytest.mark.asyncio
    async def test_issue_1998_exported_state_scrubs_secret_fetch_token(self, session_with_dir, tmp_path):
        """Security regression: state.json's secret_fetch_token is a live credential
        granting vault-secret resolution for this session (session_coordinator.py's
        start_session()). An earlier version of this export copied state.json
        verbatim via shutil.copy2() — found leaking a real token into a committed
        fixture during live owner testing (2026-09-23). Must reuse the same
        scrub_state_for_archive() archive_manager.py already applies to state.json
        for the disposal-archive path."""
        coordinator, session_id, session_dir = session_with_dir
        await coordinator.session_manager.update_session(session_id, secret_fetch_token="fake-token-xyz")
        recorder = SessionRecorder(session_id, session_dir)
        _write_full_coverage_raw_log(recorder)

        await export_fixture(coordinator, session_id, "test-fixture", fixtures_root=tmp_path)

        exported_state = json.loads((tmp_path / "test-fixture" / "state.json").read_text())
        assert "secret_fetch_token" not in exported_state
        assert "fake-token-xyz" not in json.dumps(exported_state)

    @pytest.mark.asyncio
    async def test_no_raw_log_at_all_reports_all_markers_missing(self, session_with_dir, tmp_path):
        coordinator, session_id, _session_dir = session_with_dir
        with pytest.raises(FixtureExportError) as exc_info:
            await export_fixture(coordinator, session_id, "test-fixture", fixtures_root=tmp_path)
        assert set(exc_info.value.missing_markers) == set(REQUIRED_MARKERS)

    @pytest.mark.asyncio
    async def test_issue_1998_nonexistent_session_raises_value_error(self, coordinator, tmp_path):
        """Regression: get_session_directory() returns None for an unknown session_id
        (e.g. the CLI wrapper called directly with a bad id, or a REST race where the
        session was deleted between the existence check and this call) — must raise a
        clean, catchable error rather than crash on `None / "raw_log.jsonl"`."""
        with pytest.raises(ValueError, match="Session not found"):
            await export_fixture(coordinator, "does-not-exist", "test-fixture", fixtures_root=tmp_path)
