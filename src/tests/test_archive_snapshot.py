"""
Tests for archive_manager helpers — covering issue #1244.

Step 1: scrub_state_for_archive()
Step 2: SnapshotContext + snapshot_artifacts()
Step 5+: reset cleanup helpers (added in later steps)
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.legion.archive_manager import (
    ArchiveManager,
    SnapshotContext,
    scrub_state_for_archive,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_data():
    """Temp directory tree mimicking data/ layout."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "sessions").mkdir()
        (root / "archives" / "minions").mkdir(parents=True)
        (root / "legions").mkdir()
        yield root


@pytest.fixture
def mock_system(tmp_data):
    """Minimal mock LegionSystem."""
    system = Mock()
    system.session_coordinator = Mock()
    system.session_coordinator.session_manager = Mock()
    system.session_coordinator.session_manager.data_dir = tmp_data
    system.session_coordinator.session_manager.sessions_dir = tmp_data / "sessions"
    return system, tmp_data


def _make_session_dir(tmp_data: Path, session_id: str) -> Path:
    d = tmp_data / "sessions" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _full_session(session_dir: Path) -> None:
    """Populate a session dir with every artifact from the matrix."""
    (session_dir / "messages.jsonl").write_text('{"type":"user","content":"hello"}\n')
    (session_dir / "state.json").write_text(
        json.dumps({
            "session_id": session_dir.name,
            "secret_fetch_token": "tok-secret",
            "secret_placeholders": {"FOO": "bar"},
            "is_processing": True,
            "claude_code_session_id": "abc",
        })
    )
    (session_dir / "queue.jsonl").write_text('{"id":"q1","content":"queued"}\n')

    res = session_dir / "resources"
    res.mkdir()
    (res / "resources.jsonl").write_text('{"resource_id":"r1"}\n')
    (res / "r1.bin").write_bytes(b"\x00\x01")

    att = session_dir / "attachments"
    att.mkdir()
    (att / "file.txt").write_text("hello")

    hist = session_dir / "history"
    hist.mkdir()
    (hist / "20240101_120000.md").write_text("# History")

    mem = session_dir / "memory"
    mem.mkdir()
    (mem / "short_term.json").write_text("{}")

    proxy = session_dir / "docker_claude_data" / "proxy"
    proxy.mkdir(parents=True)
    (proxy / "access.log").write_text("GET / 200")
    (proxy / "dns.log").write_text("resolved")
    (proxy / "socks5.log").write_text("connected")
    (proxy / "dropped.log").write_text("blocked")


# ---------------------------------------------------------------------------
# scrub_state_for_archive tests
# ---------------------------------------------------------------------------


class TestScrubStateForArchive:
    def test_drops_secret_fetch_token(self):
        state = {"session_id": "abc", "secret_fetch_token": "tok-live", "name": "Agent"}
        result = scrub_state_for_archive(state)
        assert "secret_fetch_token" not in result
        assert result["session_id"] == "abc"
        assert result["name"] == "Agent"

    def test_keeps_secret_placeholders(self):
        state = {"secret_placeholders": {"API_KEY": "placeholder"}, "secret_fetch_token": "live"}
        result = scrub_state_for_archive(state)
        assert result["secret_placeholders"] == {"API_KEY": "placeholder"}
        assert "secret_fetch_token" not in result

    def test_drops_runtime_keys(self):
        state = {
            "session_id": "s1",
            "is_processing": True,
            "latest_message": "hi",
            "latest_message_type": "user",
            "latest_message_time": 9999,
            "claude_code_session_id": "ccs-123",
        }
        result = scrub_state_for_archive(state)
        assert "is_processing" not in result
        assert "latest_message" not in result
        assert "latest_message_type" not in result
        assert "latest_message_time" not in result
        assert "claude_code_session_id" not in result
        assert result["session_id"] == "s1"

    def test_truncates_error_message_long(self):
        long_err = "x" * 300
        state = {"error_message": long_err}
        result = scrub_state_for_archive(state)
        assert "error_message" not in result
        assert "error_summary" in result
        assert len(result["error_summary"]) == 201  # 200 chars + "…"

    def test_keeps_short_error_message_verbatim(self):
        state = {"error_message": "short error"}
        result = scrub_state_for_archive(state)
        assert result["error_summary"] == "short error"

    def test_no_error_key_when_none(self):
        state = {"session_id": "s1"}
        result = scrub_state_for_archive(state)
        assert "error_message" not in result
        assert "error_summary" not in result

    def test_original_state_unmodified(self):
        state = {"secret_fetch_token": "tok", "session_id": "s1"}
        scrub_state_for_archive(state)
        assert "secret_fetch_token" in state


# ---------------------------------------------------------------------------
# snapshot_artifacts — disposal path
# ---------------------------------------------------------------------------


class TestSnapshotArtifactsDisposal:
    @pytest.mark.asyncio
    async def test_disposal_full_archives_all_artifacts(self, mock_system, tmp_data):
        system, _ = mock_system
        session_id = "sess-disposal-full"
        session_dir = _make_session_dir(tmp_data, session_id)
        _full_session(session_dir)

        archive_dir = tmp_data / "archives" / "minions" / session_id / "20240101_120000"
        archive_dir.mkdir(parents=True)

        ctx = SnapshotContext(
            session_id=session_id,
            legion_id=None,
            auto_memory_directory=None,
            docker_enabled=True,
            proxy_enabled=True,
            is_reset=False,
            will_be_deleted=True,
        )
        manager = ArchiveManager(system)
        archived = await manager.snapshot_artifacts(session_dir, archive_dir, ctx)

        assert "messages.jsonl" in archived
        assert "state.json" in archived
        assert "queue.jsonl" in archived
        assert "resources/" in archived
        assert "attachments/" in archived
        assert "history/" in archived
        assert "memory/" in archived
        assert "proxy/" in archived

        # Verify scrubbed state
        state = json.loads((archive_dir / "state.json").read_text())
        assert "secret_fetch_token" not in state
        assert "secret_placeholders" in state

        # Verify proxy logs captured
        for name in ("access.log", "dns.log", "socks5.log", "dropped.log"):
            assert (archive_dir / "proxy" / name).exists()

    @pytest.mark.asyncio
    async def test_disposal_archives_resources_path_a_fix(self, mock_system, tmp_data):
        """Regression: resources/ must be included in disposal archives."""
        system, _ = mock_system
        session_id = "sess-res-fix"
        session_dir = _make_session_dir(tmp_data, session_id)
        res = session_dir / "resources"
        res.mkdir()
        (res / "resources.jsonl").write_text('{"resource_id":"r1"}\n')
        (res / "r1.bin").write_bytes(b"\xff\xfe")

        archive_dir = tmp_data / "archives" / "minions" / session_id / "ts1"
        archive_dir.mkdir(parents=True)
        ctx = SnapshotContext(
            session_id=session_id, legion_id=None, auto_memory_directory=None,
            docker_enabled=False, proxy_enabled=False, is_reset=False, will_be_deleted=True,
        )
        manager = ArchiveManager(system)
        await manager.snapshot_artifacts(session_dir, archive_dir, ctx)

        assert (archive_dir / "resources" / "resources.jsonl").exists()
        assert (archive_dir / "resources" / "r1.bin").exists()

    @pytest.mark.asyncio
    async def test_disposal_archives_schedules(self, mock_system, tmp_data):
        """Disposal with legion_id captures schedules from data/legions/{lid}/."""
        system, _ = mock_system
        session_id = "sess-sched"
        legion_id = "legion-sched-001"
        session_dir = _make_session_dir(tmp_data, session_id)

        legion_dir = tmp_data / "legions" / legion_id
        legion_dir.mkdir(parents=True)
        (legion_dir / "schedules.json").write_text(json.dumps([{"cron": "0 * * * *"}]))
        (legion_dir / "schedule_history.jsonl").write_text('{"exec":"ok"}\n')

        archive_dir = tmp_data / "archives" / "minions" / session_id / "ts2"
        archive_dir.mkdir(parents=True)
        ctx = SnapshotContext(
            session_id=session_id, legion_id=legion_id, auto_memory_directory=None,
            docker_enabled=False, proxy_enabled=False, is_reset=False, will_be_deleted=True,
        )
        manager = ArchiveManager(system)
        archived = await manager.snapshot_artifacts(session_dir, archive_dir, ctx)

        assert "schedules.json" in archived
        assert "schedule_history.jsonl" in archived
        assert (archive_dir / "schedules.json").exists()
        assert (archive_dir / "schedule_history.jsonl").exists()

    @pytest.mark.asyncio
    async def test_archive_includes_metrics_file(self, mock_system, tmp_data):
        """Disposal archive includes schedule_metrics.json when present (issue #1372)."""
        system, _ = mock_system
        session_id = "sess-metrics"
        legion_id = "legion-metrics-001"
        session_dir = _make_session_dir(tmp_data, session_id)

        legion_dir = tmp_data / "legions" / legion_id
        legion_dir.mkdir(parents=True)
        (legion_dir / "schedules.json").write_text(json.dumps([]))
        (legion_dir / "schedule_history.jsonl").write_text("")
        (legion_dir / "schedule_metrics.json").write_text(
            json.dumps({"sched-1": {"schedule_id": "sched-1", "total_runs": 42}})
        )

        archive_dir = tmp_data / "archives" / "minions" / session_id / "ts-metrics"
        archive_dir.mkdir(parents=True)
        ctx = SnapshotContext(
            session_id=session_id, legion_id=legion_id, auto_memory_directory=None,
            docker_enabled=False, proxy_enabled=False, is_reset=False, will_be_deleted=True,
        )
        manager = ArchiveManager(system)
        archived = await manager.snapshot_artifacts(session_dir, archive_dir, ctx)

        assert "schedule_metrics.json" in archived
        assert (archive_dir / "schedule_metrics.json").exists()
        saved = json.loads((archive_dir / "schedule_metrics.json").read_text())
        assert saved["sched-1"]["total_runs"] == 42

    @pytest.mark.asyncio
    async def test_disposal_external_memory_not_deleted(self, mock_system, tmp_data):
        """Out-of-tree auto_memory_directory is copied but source preserved."""
        system, _ = mock_system
        session_id = "sess-extmem"
        session_dir = _make_session_dir(tmp_data, session_id)

        ext_mem = tmp_data / "shared_memory"
        ext_mem.mkdir()
        (ext_mem / "mem.json").write_text('{"key":"value"}')

        archive_dir = tmp_data / "archives" / "minions" / session_id / "ts3"
        archive_dir.mkdir(parents=True)
        ctx = SnapshotContext(
            session_id=session_id, legion_id=None,
            auto_memory_directory=ext_mem,
            docker_enabled=False, proxy_enabled=False, is_reset=False, will_be_deleted=True,
        )
        manager = ArchiveManager(system)
        await manager.snapshot_artifacts(session_dir, archive_dir, ctx)

        assert (archive_dir / "memory_external" / "mem.json").exists()
        assert (ext_mem / "mem.json").exists()  # source not deleted

    @pytest.mark.asyncio
    async def test_disposal_archives_proxy_logs(self, mock_system, tmp_data):
        """All four proxy log files are captured on disposal."""
        system, _ = mock_system
        session_id = "sess-proxy"
        session_dir = _make_session_dir(tmp_data, session_id)
        _full_session(session_dir)

        archive_dir = tmp_data / "archives" / "minions" / session_id / "ts4"
        archive_dir.mkdir(parents=True)
        ctx = SnapshotContext(
            session_id=session_id, legion_id=None, auto_memory_directory=None,
            docker_enabled=True, proxy_enabled=True, is_reset=False, will_be_deleted=True,
        )
        manager = ArchiveManager(system)
        await manager.snapshot_artifacts(session_dir, archive_dir, ctx)

        for name in ("access.log", "dns.log", "socks5.log", "dropped.log"):
            assert (archive_dir / "proxy" / name).exists(), f"Missing {name}"


# ---------------------------------------------------------------------------
# snapshot_artifacts — reset path
# ---------------------------------------------------------------------------


class TestSnapshotArtifactsReset:
    @pytest.mark.asyncio
    async def test_reset_subset_no_history_no_memory_no_schedules(self, mock_system, tmp_data):
        """Reset snapshot excludes history/, memory/, and schedule files."""
        system, _ = mock_system
        session_id = "sess-reset-sub"
        session_dir = _make_session_dir(tmp_data, session_id)
        _full_session(session_dir)

        archive_dir = tmp_data / "archives" / "minions" / session_id / "ts-reset"
        archive_dir.mkdir(parents=True)
        ctx = SnapshotContext(
            session_id=session_id, legion_id="leg1", auto_memory_directory=None,
            docker_enabled=True, proxy_enabled=True, is_reset=True, will_be_deleted=False,
        )
        manager = ArchiveManager(system)
        archived = await manager.snapshot_artifacts(session_dir, archive_dir, ctx)

        assert "messages.jsonl" in archived
        assert "queue.jsonl" in archived
        assert "resources/" in archived
        assert "attachments/" in archived
        assert "proxy/" in archived

        assert "history/" not in archived
        assert "memory/" not in archived
        assert "schedules.json" not in archived
        assert "schedule_history.jsonl" not in archived

    @pytest.mark.asyncio
    async def test_reset_preserves_history_and_memory_on_disk(self, mock_system, tmp_data):
        """snapshot_artifacts with is_reset=True does not touch live history/ or memory/."""
        system, _ = mock_system
        session_id = "sess-reset-pres"
        session_dir = _make_session_dir(tmp_data, session_id)
        _full_session(session_dir)

        archive_dir = tmp_data / "archives" / "minions" / session_id / "ts-pres"
        archive_dir.mkdir(parents=True)
        ctx = SnapshotContext(
            session_id=session_id, legion_id=None, auto_memory_directory=None,
            docker_enabled=False, proxy_enabled=False, is_reset=True, will_be_deleted=False,
        )
        manager = ArchiveManager(system)
        await manager.snapshot_artifacts(session_dir, archive_dir, ctx)

        # Live dirs must be untouched
        assert (session_dir / "history").exists()
        assert (session_dir / "memory").exists()


# ---------------------------------------------------------------------------
# Reset cleanup helpers (DataStorageManager + session_coordinator)
# ---------------------------------------------------------------------------


class TestResetCleanupHelpers:
    @pytest.mark.asyncio
    async def test_clear_queue_truncates_file(self, tmp_data):
        """clear_queue() truncates queue.jsonl to empty."""
        from src.data_storage import DataStorageManager

        session_id = "sess-clr-q"
        session_dir = tmp_data / "sessions" / session_id
        session_dir.mkdir(parents=True)
        queue_file = session_dir / "queue.jsonl"
        queue_file.write_text('{"id":"q1"}\n')

        mgr = DataStorageManager(session_dir)
        result = await mgr.clear_queue()

        assert result is True
        assert queue_file.exists()
        assert queue_file.read_bytes() == b""

    @pytest.mark.asyncio
    async def test_clear_queue_no_file_is_noop(self, tmp_data):
        """clear_queue() succeeds even when queue.jsonl does not exist."""
        from src.data_storage import DataStorageManager

        session_dir = tmp_data / "sessions" / "sess-no-q"
        session_dir.mkdir(parents=True)
        mgr = DataStorageManager(session_dir)
        result = await mgr.clear_queue()
        assert result is True

    @pytest.mark.asyncio
    async def test_clear_attachments_removes_dir(self, tmp_data):
        """clear_attachments() removes the attachments/ directory."""
        from src.data_storage import DataStorageManager

        session_id = "sess-clr-att"
        session_dir = tmp_data / "sessions" / session_id
        session_dir.mkdir(parents=True)
        att = session_dir / "attachments"
        att.mkdir()
        (att / "file.txt").write_text("hello")

        mgr = DataStorageManager(session_dir)
        result = await mgr.clear_attachments()

        assert result is True
        assert not att.exists()

    @pytest.mark.asyncio
    async def test_clear_attachments_no_dir_is_noop(self, tmp_data):
        """clear_attachments() succeeds when attachments/ does not exist."""
        from src.data_storage import DataStorageManager

        session_dir = tmp_data / "sessions" / "sess-no-att"
        session_dir.mkdir(parents=True)
        mgr = DataStorageManager(session_dir)
        result = await mgr.clear_attachments()
        assert result is True

    @pytest.mark.asyncio
    async def test_rotate_proxy_logs_truncates(self, tmp_data):
        """_rotate_proxy_logs() truncates each proxy log to empty bytes."""
        from unittest.mock import Mock

        from src.session_coordinator import SessionCoordinator

        sessions_dir = tmp_data / "sessions"
        session_id = "sess-plog"
        proxy_dir = sessions_dir / session_id / "docker_claude_data" / "proxy"
        proxy_dir.mkdir(parents=True)
        for name in ("access.log", "dns.log", "socks5.log", "dropped.log"):
            (proxy_dir / name).write_text("data")

        coord = Mock(spec=SessionCoordinator)
        coord.session_manager = Mock()
        coord.session_manager.sessions_dir = sessions_dir
        coord._rotate_proxy_logs = SessionCoordinator._rotate_proxy_logs.__get__(coord)

        await coord._rotate_proxy_logs(session_id)

        for name in ("access.log", "dns.log", "socks5.log", "dropped.log"):
            assert (proxy_dir / name).read_bytes() == b""

    @pytest.mark.asyncio
    async def test_clear_docker_claude_data_except_proxy(self, tmp_data):
        """_clear_docker_claude_data keeps proxy/ and removes others."""
        from unittest.mock import Mock

        from src.session_coordinator import SessionCoordinator

        sessions_dir = tmp_data / "sessions"
        session_id = "sess-docker"
        docker_dir = sessions_dir / session_id / "docker_claude_data"
        for sub in ("proxy", "projects", "todos", "tasks"):
            (docker_dir / sub).mkdir(parents=True)
            (docker_dir / sub / "f.txt").write_text("data")

        coord = Mock(spec=SessionCoordinator)
        coord.session_manager = Mock()
        coord.session_manager.sessions_dir = sessions_dir
        coord._clear_docker_claude_data = SessionCoordinator._clear_docker_claude_data.__get__(coord)

        await coord._clear_docker_claude_data(session_id, keep_subdirs={"proxy"})

        assert (docker_dir / "proxy").exists()
        assert not (docker_dir / "projects").exists()
        assert not (docker_dir / "todos").exists()
        assert not (docker_dir / "tasks").exists()

    @pytest.mark.asyncio
    async def test_clear_session_tmp(self, tmp_data):
        """_clear_session_tmp removes the tmp/ directory."""
        from unittest.mock import Mock

        from src.session_coordinator import SessionCoordinator

        sessions_dir = tmp_data / "sessions"
        session_id = "sess-tmp"
        tmp_dir = sessions_dir / session_id / "tmp"
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "a.txt").write_text("temp")

        coord = Mock(spec=SessionCoordinator)
        coord.session_manager = Mock()
        coord.session_manager.sessions_dir = sessions_dir
        coord._clear_session_tmp = SessionCoordinator._clear_session_tmp.__get__(coord)

        await coord._clear_session_tmp(session_id)
        assert not tmp_dir.exists()
