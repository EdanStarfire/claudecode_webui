"""Regression tests for issue #1998 (AC7): off by default, no overhead when off,
excluded from archives/distillation.

Both halves of AC7 are satisfied by existing explicit-whitelist/explicit-path code
with zero changes needed (see plan's Technical Approach) — these tests lock that in
rather than relying on it staying true by accident.
"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock

from backend.claude_sdk import ClaudeSDK
from backend.history_distiller import distill_session_history
from backend.legion.archive_manager import ArchiveManager, SnapshotContext
from backend.session_config import SessionConfig
from backend.session_coordinator import SessionCoordinator


class TestArchiveExcludesRawLog:
    @pytest.mark.asyncio
    async def test_issue_1998_snapshot_artifacts_never_copies_raw_log(self, tmp_path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "messages.jsonl").write_text('{"type": "system"}\n')
        (session_dir / "raw_log.jsonl").write_text('{"kind": "sdk_message"}\n')
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()

        manager = ArchiveManager(Mock())
        ctx = SnapshotContext(
            session_id="sess-1", legion_id=None, auto_memory_directory=None,
            docker_enabled=False, proxy_enabled=False, is_reset=False, will_be_deleted=True,
        )
        archived = await manager.snapshot_artifacts(session_dir, archive_dir, ctx)

        assert "raw_log.jsonl" not in archived
        assert not (archive_dir / "raw_log.jsonl").exists()
        assert (archive_dir / "messages.jsonl").exists()


class TestHistoryDistillationExcludesRawLog:
    @pytest.mark.asyncio
    async def test_issue_1998_distiller_only_ever_reads_its_given_path(self, tmp_path):
        """distill_session_history() takes an explicit messages_jsonl_path and never
        globs/scans the session directory, so a sibling raw_log.jsonl is structurally
        unreachable — not just unused by convention."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "messages.jsonl").write_text('{"type": "user", "content": "hi"}\n')
        (session_dir / "raw_log.jsonl").write_text('{"kind": "sdk_message", "malformed": ')

        output_path = tmp_path / "distilled.md"
        success = await distill_session_history(
            session_dir / "messages.jsonl", output_path, "sess-1", "2026-01-01T00:00:00Z"
        )

        # If the distiller ever touched raw_log.jsonl (malformed JSON above), this
        # would raise/return False instead of succeeding off messages.jsonl alone.
        assert success is True


class TestOffPathNoRecorderConstructed:
    @pytest.fixture
    async def coordinator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            c = SessionCoordinator(Path(temp_dir), session_recording_enabled=True)
            await c.initialize()
            mock_sdk_instance = Mock()
            mock_sdk_instance.start = Mock(return_value=True)

            async def _start():
                return True
            mock_sdk_instance.start = _start
            mock_sdk_instance.is_running = Mock(return_value=False)
            mock_sdk_instance.auto_approval_callback = None
            factory = Mock(return_value=mock_sdk_instance)
            c.set_sdk_factory(factory)
            c._test_factory = factory
            yield c
            await c.cleanup()

    async def _create_session(self, coordinator, **config_kwargs):
        project = await coordinator.project_manager.create_project(
            name="P", working_directory="/tmp"
        )
        session_id = str(uuid.uuid4())
        await coordinator.create_session(
            session_id=session_id,
            project_id=project.project_id,
            config=SessionConfig(**config_kwargs),
        )
        return session_id

    @pytest.mark.asyncio
    async def test_issue_1998_recording_disabled_never_constructs_recorder(self, coordinator):
        session_id = await self._create_session(coordinator, recording_enabled=False)
        await coordinator.start_session(session_id)

        assert coordinator.get_session_recorder(session_id) is None
        assert coordinator._test_factory.call_args.kwargs["recorder"] is None

    @pytest.mark.asyncio
    async def test_issue_1998_flag_off_goes_inert_even_if_session_opted_in(self):
        """Backend-wide flag off (self.session_recording_enabled=False) is a silent
        no-op, not a config mutation — mirrors docker_enabled's posture if Docker
        becomes unavailable (plan's Risks & Considerations)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            coordinator = SessionCoordinator(Path(temp_dir), session_recording_enabled=False)
            await coordinator.initialize()
            try:
                mock_sdk_instance = Mock()

                async def _start():
                    return True
                mock_sdk_instance.start = _start
                mock_sdk_instance.is_running = Mock(return_value=False)
                mock_sdk_instance.auto_approval_callback = None
                factory = Mock(return_value=mock_sdk_instance)
                coordinator.set_sdk_factory(factory)

                session_id = await self._create_session(coordinator, recording_enabled=True)
                await coordinator.start_session(session_id)

                assert coordinator.get_session_recorder(session_id) is None
                # Config itself is untouched — still recording_enabled=True on disk.
                info = await coordinator.session_manager.get_session_info(session_id)
                assert info.config.get("recording_enabled") is True
            finally:
                await coordinator.cleanup()

    @pytest.mark.asyncio
    async def test_issue_1998_docker_isolated_session_never_constructs_recorder(self, coordinator):
        session_id = await self._create_session(
            coordinator, recording_enabled=True, docker_enabled=True
        )
        await coordinator.start_session(session_id)

        assert coordinator.get_session_recorder(session_id) is None

    @pytest.mark.asyncio
    async def test_issue_1998_recording_enabled_and_flag_on_constructs_recorder(self, coordinator):
        session_id = await self._create_session(
            coordinator, recording_enabled=True, docker_enabled=False
        )
        await coordinator.start_session(session_id)

        recorder = coordinator.get_session_recorder(session_id)
        assert recorder is not None
        assert coordinator._test_factory.call_args.kwargs["recorder"] is recorder


class TestRecordingFailureDoesNotDropRealMessages:
    """Regression: record_sdk_message() previously ran unguarded as the first
    statement inside _process_sdk_message()'s try block — a recorder-only failure
    (e.g. dataclasses.asdict() raising on an exotic field) was caught by
    _process_sdk_message()'s own broad except, which drops the ENTIRE message
    (never reaches storage or message_callback). A dev-only recording bug must
    never turn into user-facing message loss."""

    @pytest.mark.asyncio
    async def test_issue_1998_broken_recorder_still_delivers_the_message(self):
        received = []

        async def message_callback(msg):
            received.append(msg)

        sdk = ClaudeSDK(
            session_id="sess-1",
            working_directory="/tmp",
            message_callback=message_callback,
        )
        broken_recorder = Mock()
        broken_recorder.record_sdk_message.side_effect = RuntimeError("boom")
        sdk.recorder = broken_recorder

        msg = AssistantMessage(content=[TextBlock(text="hi")], model="claude-sonnet-4-5")
        await sdk._process_sdk_message(msg)

        assert broken_recorder.record_sdk_message.called
        assert len(received) == 1
        assert received[0]["type"] == "assistant"

    @pytest.mark.asyncio
    async def test_issue_1998_real_recorder_payload_construction_failure_is_swallowed(self):
        """SessionRecorder.record_sdk_message() itself must catch a failure inside
        its own payload construction (e.g. dataclasses.asdict()), not just the file
        write — exercised directly through the real class, not a mock."""
        from backend.session_recorder import SessionRecorder

        recorder = SessionRecorder("sess-1", Path(tempfile.mkdtemp()))

        class NotADataclassOrException:
            pass

        recorder.record_sdk_message(NotADataclassOrException())  # must not raise
        assert not recorder.log_path.exists() or recorder.log_path.read_text() == ""
