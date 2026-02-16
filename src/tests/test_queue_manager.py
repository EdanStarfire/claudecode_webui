"""Tests for QueueManager (issue #500)."""

import json
import tempfile
from pathlib import Path

import pytest

from ..queue_manager import QueueItem, QueueManager


@pytest.fixture
def queue_manager():
    return QueueManager()


@pytest.fixture
def temp_session():
    """Provide a temp directory simulating a session directory, plus a session_id."""
    with tempfile.TemporaryDirectory() as tmp:
        yield "test-session-001", Path(tmp)


class TestQueueItem:
    """QueueItem dataclass basics."""

    def test_defaults(self):
        item = QueueItem(queue_id="q1", session_id="s1", content="hello")
        assert item.status == "pending"
        assert item.reset_session is True
        assert item.metadata is None
        assert item.sent_at is None
        assert item.error is None

    def test_to_dict_roundtrip(self):
        item = QueueItem(
            queue_id="q1", session_id="s1", content="hello",
            metadata={"key": "val"}, position=3,
        )
        d = item.to_dict()
        assert d["queue_id"] == "q1"
        assert d["metadata"] == {"key": "val"}
        restored = QueueItem.from_dict(d)
        assert restored.queue_id == item.queue_id
        assert restored.content == item.content

    def test_from_dict_ignores_unknown_keys(self):
        d = {"queue_id": "q1", "session_id": "s1", "content": "x", "extra_field": True}
        item = QueueItem.from_dict(d)
        assert item.queue_id == "q1"
        assert not hasattr(item, "extra_field")


class TestQueueManagerCRUD:
    """Core CRUD operations."""

    @pytest.mark.asyncio
    async def test_enqueue_returns_item(self, queue_manager, temp_session):
        sid, sdir = temp_session
        item = await queue_manager.enqueue(sid, sdir, "msg 1")
        assert item.content == "msg 1"
        assert item.status == "pending"
        assert item.position == 0

    @pytest.mark.asyncio
    async def test_enqueue_sequential_positions(self, queue_manager, temp_session):
        sid, sdir = temp_session
        i1 = await queue_manager.enqueue(sid, sdir, "first")
        i2 = await queue_manager.enqueue(sid, sdir, "second")
        assert i2.position > i1.position

    @pytest.mark.asyncio
    async def test_enqueue_respects_max_queue_size(self, queue_manager, temp_session):
        sid, sdir = temp_session
        await queue_manager.enqueue(sid, sdir, "a", max_queue_size=2)
        await queue_manager.enqueue(sid, sdir, "b", max_queue_size=2)
        with pytest.raises(ValueError, match="Queue full"):
            await queue_manager.enqueue(sid, sdir, "c", max_queue_size=2)

    @pytest.mark.asyncio
    async def test_cancel_pending_item(self, queue_manager, temp_session):
        sid, sdir = temp_session
        item = await queue_manager.enqueue(sid, sdir, "cancel me")
        result = await queue_manager.cancel(sid, sdir, item.queue_id)
        assert result is not None
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_non_pending_returns_none(self, queue_manager, temp_session):
        sid, sdir = temp_session
        item = await queue_manager.enqueue(sid, sdir, "x")
        await queue_manager.mark_sent(sid, sdir, item.queue_id)
        result = await queue_manager.cancel(sid, sdir, item.queue_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_unknown_returns_none(self, queue_manager, temp_session):
        sid, sdir = temp_session
        result = await queue_manager.cancel(sid, sdir, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_mark_sent(self, queue_manager, temp_session):
        sid, sdir = temp_session
        item = await queue_manager.enqueue(sid, sdir, "x")
        result = await queue_manager.mark_sent(sid, sdir, item.queue_id)
        assert result.status == "sent"
        assert result.sent_at is not None

    @pytest.mark.asyncio
    async def test_mark_failed(self, queue_manager, temp_session):
        sid, sdir = temp_session
        item = await queue_manager.enqueue(sid, sdir, "x")
        result = await queue_manager.mark_failed(sid, sdir, item.queue_id, "boom")
        assert result.status == "failed"
        assert result.error == "boom"

    @pytest.mark.asyncio
    async def test_requeue_sent_item(self, queue_manager, temp_session):
        sid, sdir = temp_session
        item = await queue_manager.enqueue(sid, sdir, "original")
        await queue_manager.mark_sent(sid, sdir, item.queue_id)

        new_item = await queue_manager.requeue(sid, sdir, item.queue_id)
        assert new_item is not None
        assert new_item.queue_id != item.queue_id  # New ID
        assert new_item.content == "original"
        assert new_item.status == "pending"
        # Original unchanged
        assert item.status == "sent"

    @pytest.mark.asyncio
    async def test_requeue_failed_item(self, queue_manager, temp_session):
        sid, sdir = temp_session
        item = await queue_manager.enqueue(sid, sdir, "fail me")
        await queue_manager.mark_failed(sid, sdir, item.queue_id, "err")

        new_item = await queue_manager.requeue(sid, sdir, item.queue_id)
        assert new_item is not None
        assert new_item.status == "pending"

    @pytest.mark.asyncio
    async def test_requeue_pending_returns_none(self, queue_manager, temp_session):
        sid, sdir = temp_session
        item = await queue_manager.enqueue(sid, sdir, "x")
        result = await queue_manager.requeue(sid, sdir, item.queue_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_requeue_at_front(self, queue_manager, temp_session):
        sid, sdir = temp_session
        i1 = await queue_manager.enqueue(sid, sdir, "first")
        i2 = await queue_manager.enqueue(sid, sdir, "second")
        await queue_manager.mark_sent(sid, sdir, i1.queue_id)

        requeued = await queue_manager.requeue(sid, sdir, i1.queue_id)
        pending = queue_manager.get_pending(sid)
        assert pending[0].queue_id == requeued.queue_id  # Re-queued at front
        assert pending[1].queue_id == i2.queue_id

    @pytest.mark.asyncio
    async def test_clear_pending(self, queue_manager, temp_session):
        sid, sdir = temp_session
        await queue_manager.enqueue(sid, sdir, "a")
        i2 = await queue_manager.enqueue(sid, sdir, "b")
        await queue_manager.mark_sent(sid, sdir, i2.queue_id)
        await queue_manager.enqueue(sid, sdir, "c")

        count = await queue_manager.clear_pending(sid, sdir)
        assert count == 2  # a and c, not b (already sent)
        assert queue_manager.get_pending_count(sid) == 0


class TestQueueManagerQuery:
    """Query / accessor methods."""

    @pytest.mark.asyncio
    async def test_get_queue_sorted(self, queue_manager, temp_session):
        sid, sdir = temp_session
        await queue_manager.enqueue(sid, sdir, "a")
        await queue_manager.enqueue(sid, sdir, "b")
        items = queue_manager.get_queue(sid)
        assert len(items) == 2
        assert items[0].position <= items[1].position

    @pytest.mark.asyncio
    async def test_get_pending_filters(self, queue_manager, temp_session):
        sid, sdir = temp_session
        i1 = await queue_manager.enqueue(sid, sdir, "a")
        await queue_manager.enqueue(sid, sdir, "b")
        await queue_manager.cancel(sid, sdir, i1.queue_id)
        assert queue_manager.get_pending_count(sid) == 1

    @pytest.mark.asyncio
    async def test_peek_next(self, queue_manager, temp_session):
        sid, sdir = temp_session
        i1 = await queue_manager.enqueue(sid, sdir, "first")
        await queue_manager.enqueue(sid, sdir, "second")
        peeked = queue_manager.peek_next(sid)
        assert peeked.queue_id == i1.queue_id

    def test_peek_next_empty(self, queue_manager):
        assert queue_manager.peek_next("no-such-session") is None

    @pytest.mark.asyncio
    async def test_remove_session(self, queue_manager, temp_session):
        sid, sdir = temp_session
        await queue_manager.enqueue(sid, sdir, "x")
        queue_manager.remove_session(sid)
        assert queue_manager.get_queue(sid) == []


class TestQueueManagerJSONLReplay:
    """JSONL persistence and replay-from-log."""

    @pytest.mark.asyncio
    async def test_enqueue_writes_jsonl(self, queue_manager, temp_session):
        sid, sdir = temp_session
        await queue_manager.enqueue(sid, sdir, "hello")

        queue_file = sdir / "queue.jsonl"
        assert queue_file.exists()
        lines = queue_file.read_text().strip().split('\n')
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["type"] == "enqueue"
        assert entry["content"] == "hello"

    @pytest.mark.asyncio
    async def test_status_writes_jsonl(self, queue_manager, temp_session):
        sid, sdir = temp_session
        item = await queue_manager.enqueue(sid, sdir, "x")
        await queue_manager.mark_sent(sid, sdir, item.queue_id)

        lines = (sdir / "queue.jsonl").read_text().strip().split('\n')
        assert len(lines) == 2
        status_entry = json.loads(lines[1])
        assert status_entry["type"] == "status"
        assert status_entry["status"] == "sent"

    @pytest.mark.asyncio
    async def test_replay_rebuilds_state(self, temp_session):
        sid, sdir = temp_session

        # Phase 1: populate with one manager
        mgr1 = QueueManager()
        i1 = await mgr1.enqueue(sid, sdir, "first")
        i2 = await mgr1.enqueue(sid, sdir, "second")
        await mgr1.mark_sent(sid, sdir, i1.queue_id)
        await mgr1.cancel(sid, sdir, i2.queue_id)
        i3 = await mgr1.enqueue(sid, sdir, "third")

        # Phase 2: fresh manager replays the log
        mgr2 = QueueManager()
        items = await mgr2.load_queue(sid, sdir)
        assert len(items) == 3
        by_id = {i.queue_id: i for i in items}
        assert by_id[i1.queue_id].status == "sent"
        assert by_id[i2.queue_id].status == "cancelled"
        assert by_id[i3.queue_id].status == "pending"
        assert mgr2.get_pending_count(sid) == 1

    @pytest.mark.asyncio
    async def test_replay_empty_file(self, temp_session):
        sid, sdir = temp_session
        mgr = QueueManager()
        items = await mgr.load_queue(sid, sdir)
        assert items == []

    @pytest.mark.asyncio
    async def test_replay_malformed_line_skipped(self, temp_session):
        sid, sdir = temp_session
        queue_file = sdir / "queue.jsonl"
        queue_file.write_text('not json\n{"type":"enqueue","queue_id":"q1","content":"ok","position":0,"created_at":0}\n')

        mgr = QueueManager()
        items = await mgr.load_queue(sid, sdir)
        assert len(items) == 1
        assert items[0].queue_id == "q1"

    @pytest.mark.asyncio
    async def test_replay_preserves_failed_error(self, temp_session):
        sid, sdir = temp_session
        mgr1 = QueueManager()
        item = await mgr1.enqueue(sid, sdir, "x")
        await mgr1.mark_failed(sid, sdir, item.queue_id, "test error")

        mgr2 = QueueManager()
        items = await mgr2.load_queue(sid, sdir)
        assert items[0].status == "failed"
        assert items[0].error == "test error"
