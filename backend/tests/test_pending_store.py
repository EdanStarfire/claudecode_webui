"""Tests for PendingStore (issue #2193, AC10)."""

import json
import tempfile
import uuid
from pathlib import Path

import pytest

from backend.models.legion_models import Comm, CommType, DeliveryPolicy, SenderType
from backend.pending_store import PendingItem, PendingStore


def make_comm(session_id: str, *, comm_id: str | None = None, content: str = "hello", **overrides) -> Comm:
    """Build a valid Comm addressed to `session_id` (from the user, unless overridden)."""
    kwargs = {
        "comm_id": comm_id or str(uuid.uuid4()),
        "from_user": True,
        "to_minion_id": session_id,
        "content": content,
    }
    kwargs.update(overrides)
    return Comm(**kwargs)


def make_schedule_comm(session_id: str, *, content: str = "hello") -> Comm:
    """Build a Comm whose sender_type is SCHEDULE (from_user stays True, matching
    how the real scheduler/enqueue_message() builds its Comm — only sender_type
    diverges from the default inference)."""
    return make_comm(session_id, content=content, sender_type=SenderType.SCHEDULE)


@pytest.fixture
def pending_store():
    return PendingStore()


@pytest.fixture
def temp_session():
    """Provide a temp directory simulating a session directory, plus a session_id."""
    with tempfile.TemporaryDirectory() as tmp:
        yield "test-session-001", Path(tmp)


class TestPendingItem:
    """PendingItem dataclass basics."""

    def test_defaults(self):
        comm = make_comm("s1")
        item = PendingItem(comm=comm, session_id="s1")
        assert item.status == "pending"
        assert item.position == 0
        assert item.created_at == 0.0
        assert item.delivered_at is None
        assert item.error is None
        # Issue #2193 PR6: None ("no explicit preference, use the heuristic"),
        # not an always-True default — nothing read this field before PR6.
        assert item.inject_timestamp is None
        assert item.dispatch_content is None
        assert item.dispatch_metadata is None

    def test_to_dict_from_dict_roundtrip_fully_populated_comm(self):
        comm = make_comm(
            "s1",
            from_user=False,
            from_minion_id="minion-x",
            from_minion_name="Minion X",
            to_minion_name="Target",
            summary="a summary",
            content="full content",
            comm_type=CommType.TASK,
            delivery=DeliveryPolicy.WHEN_IDLE,
            attachments=[
                {
                    "name": "file.txt",
                    "size": 123,
                    "mime_type": "text/plain",
                    "resource_id": "res-1",
                    "session_id": "s1",
                }
            ],
            in_reply_to="comm-parent",
            related_task_id="task-1",
            metadata={"key": "value"},
            visible_to_user=False,
            timestamp=1700000000.0,
            auto_start=True,
            reset_first=True,
            fire_at=1700000500.0,
            sender_type=SenderType.MINION,
            sender_id="minion-x",
        )
        item = PendingItem(
            comm=comm,
            session_id="s1",
            status="pending",
            position=2,
            created_at=1700000001.0,
            delivered_at=None,
            error=None,
            inject_timestamp=False,
            dispatch_content="formatted dispatch text",
            dispatch_metadata={"comm": {"from_name": "sender"}},
        )

        d = item.to_dict()
        assert d["comm"] == comm.to_dict()
        assert d["session_id"] == "s1"
        assert d["position"] == 2
        assert d["inject_timestamp"] is False
        assert d["dispatch_content"] == "formatted dispatch text"
        assert d["dispatch_metadata"] == {"comm": {"from_name": "sender"}}

        restored = PendingItem.from_dict(d)
        assert isinstance(restored.comm, Comm)
        assert restored.comm == comm
        assert restored.comm.attachments == comm.attachments
        assert restored.comm.sender_type == SenderType.MINION
        assert restored.comm.delivery == DeliveryPolicy.WHEN_IDLE
        assert restored.session_id == item.session_id
        assert restored.position == item.position
        assert restored.created_at == item.created_at
        assert restored.inject_timestamp == item.inject_timestamp
        assert restored.dispatch_content == "formatted dispatch text"
        assert restored.dispatch_metadata == {"comm": {"from_name": "sender"}}

    def test_from_dict_ignores_unknown_keys(self):
        comm = make_comm("s1")
        d = {"comm": comm.to_dict(), "session_id": "s1", "extra_field": True}
        item = PendingItem.from_dict(d)
        assert item.session_id == "s1"
        assert not hasattr(item, "extra_field")

    def test_from_dict_missing_dispatch_fields_defaults_to_none(self):
        """Pre-PR5 ledger entries have neither key — from_dict() must not raise
        and must default both to None (same setdefault pattern as every prior
        optional-field addition to this dataclass)."""
        comm = make_comm("s1")
        d = {"comm": comm.to_dict(), "session_id": "s1"}
        assert "dispatch_content" not in d
        assert "dispatch_metadata" not in d

        item = PendingItem.from_dict(d)
        assert item.dispatch_content is None
        assert item.dispatch_metadata is None


class TestPendingStoreCRUD:
    """Core CRUD operations."""

    @pytest.mark.asyncio
    async def test_enqueue_returns_item(self, pending_store, temp_session):
        sid, sdir = temp_session
        comm = make_comm(sid, content="msg 1")
        item = await pending_store.enqueue(sid, sdir, comm)
        assert item.comm.content == "msg 1"
        assert item.status == "pending"
        assert item.position == 0

    @pytest.mark.asyncio
    async def test_enqueue_sequential_positions(self, pending_store, temp_session):
        sid, sdir = temp_session
        i1 = await pending_store.enqueue(sid, sdir, make_comm(sid, content="first"))
        i2 = await pending_store.enqueue(sid, sdir, make_comm(sid, content="second"))
        assert i2.position > i1.position

    @pytest.mark.asyncio
    async def test_enqueue_respects_max_pending_size(self, pending_store, temp_session):
        """max_pending_size only gates SCHEDULE-sourced enqueues (issue #2233)."""
        sid, sdir = temp_session

        await pending_store.enqueue(sid, sdir, make_schedule_comm(sid, content="a"), max_pending_size=2)
        await pending_store.enqueue(sid, sdir, make_schedule_comm(sid, content="b"), max_pending_size=2)
        with pytest.raises(ValueError, match="Pending store full"):
            await pending_store.enqueue(sid, sdir, make_schedule_comm(sid, content="c"), max_pending_size=2)

    @pytest.mark.asyncio
    async def test_enqueue_non_schedule_bypasses_cap(self, pending_store, temp_session):
        """USER/MINION/SYSTEM/EXTERNAL enqueues always succeed, even past a full
        SCHEDULE cap (issue #2233)."""
        sid, sdir = temp_session

        # Fill the schedule cap.
        await pending_store.enqueue(sid, sdir, make_schedule_comm(sid, content="s1"), max_pending_size=1)
        with pytest.raises(ValueError, match="Pending store full"):
            await pending_store.enqueue(sid, sdir, make_schedule_comm(sid, content="s2"), max_pending_size=1)

        # USER-, MINION-, SYSTEM-, and EXTERNAL-sourced enqueues still succeed,
        # unconditionally, with a full SCHEDULE cap already in place.
        user_item = await pending_store.enqueue(
            sid, sdir, make_comm(sid, content="user msg"), max_pending_size=1
        )
        assert user_item.status == "pending"

        minion_comm = make_comm(
            sid, content="minion msg", from_user=False, from_minion_id="minion-x"
        )
        minion_item = await pending_store.enqueue(sid, sdir, minion_comm, max_pending_size=1)
        assert minion_item.status == "pending"

        system_comm = make_comm(
            sid, content="system msg", from_user=False,
            from_minion_id="system", sender_type=SenderType.SYSTEM,
        )
        system_item = await pending_store.enqueue(sid, sdir, system_comm, max_pending_size=1)
        assert system_item.status == "pending"

        external_comm = make_comm(sid, content="external msg", sender_type=SenderType.EXTERNAL)
        external_item = await pending_store.enqueue(sid, sdir, external_comm, max_pending_size=1)
        assert external_item.status == "pending"

    @pytest.mark.asyncio
    async def test_enqueue_schedule_cap_only_counts_pending_schedule_items(
        self, pending_store, temp_session
    ):
        """Non-schedule pending volume and terminal-status schedule items must
        neither count toward nor protect against the schedule-only cap
        (issue #2233) — only still-pending SCHEDULE items are counted."""
        sid, sdir = temp_session

        # A delivered SCHEDULE item must not count toward the cap.
        delivered = await pending_store.enqueue(
            sid, sdir, make_schedule_comm(sid, content="done"), max_pending_size=1
        )
        await pending_store.mark_delivered(sid, sdir, delivered.comm.comm_id)
        await pending_store.enqueue(sid, sdir, make_schedule_comm(sid, content="still fits"), max_pending_size=1)

        # A pile of non-schedule pending items doesn't protect the cap either.
        for i in range(5):
            await pending_store.enqueue(sid, sdir, make_comm(sid, content=f"user {i}"), max_pending_size=1)
        with pytest.raises(ValueError, match="Pending store full"):
            await pending_store.enqueue(sid, sdir, make_schedule_comm(sid, content="over cap"), max_pending_size=1)

    @pytest.mark.asyncio
    async def test_mark_delivered(self, pending_store, temp_session):
        sid, sdir = temp_session
        item = await pending_store.enqueue(sid, sdir, make_comm(sid))
        result = await pending_store.mark_delivered(sid, sdir, item.comm.comm_id)
        assert result.status == "delivered"
        assert result.delivered_at is not None

    @pytest.mark.asyncio
    async def test_mark_failed(self, pending_store, temp_session):
        sid, sdir = temp_session
        item = await pending_store.enqueue(sid, sdir, make_comm(sid))
        result = await pending_store.mark_failed(sid, sdir, item.comm.comm_id, "boom")
        assert result.status == "failed"
        assert result.error == "boom"

    @pytest.mark.asyncio
    async def test_cancel_pending_item(self, pending_store, temp_session):
        sid, sdir = temp_session
        item = await pending_store.enqueue(sid, sdir, make_comm(sid, content="cancel me"))
        result = await pending_store.cancel(sid, sdir, item.comm.comm_id)
        assert result is not None
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_non_pending_returns_none(self, pending_store, temp_session):
        sid, sdir = temp_session
        item = await pending_store.enqueue(sid, sdir, make_comm(sid))
        await pending_store.mark_delivered(sid, sdir, item.comm.comm_id)
        result = await pending_store.cancel(sid, sdir, item.comm.comm_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_unknown_returns_none(self, pending_store, temp_session):
        sid, sdir = temp_session
        result = await pending_store.cancel(sid, sdir, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_mark_delivered_on_cancelled_item_returns_none_and_leaves_status(
        self, pending_store, temp_session
    ):
        """Issue #2193 PR4 race fix: a concurrent flush_sender() may cancel an entry
        between dispatch picking it up and this call — must not resurrect it."""
        sid, sdir = temp_session
        item = await pending_store.enqueue(sid, sdir, make_comm(sid))
        await pending_store.cancel(sid, sdir, item.comm.comm_id)

        result = await pending_store.mark_delivered(sid, sdir, item.comm.comm_id)

        assert result is None
        assert item.status == "cancelled"
        assert item.delivered_at is None

    @pytest.mark.asyncio
    async def test_mark_failed_on_cancelled_item_returns_none_and_leaves_status(
        self, pending_store, temp_session
    ):
        """Same guard as mark_delivered() — see issue #2193 PR4."""
        sid, sdir = temp_session
        item = await pending_store.enqueue(sid, sdir, make_comm(sid))
        await pending_store.cancel(sid, sdir, item.comm.comm_id)

        result = await pending_store.mark_failed(sid, sdir, item.comm.comm_id, "boom")

        assert result is None
        assert item.status == "cancelled"
        assert item.error is None

    def test_get_item_returns_item_by_comm_id(self, pending_store):
        """get_item() is the public lookup the gate's race-check uses (issue #2193 PR4)."""
        comm = make_comm("s1")
        item = PendingItem(comm=comm, session_id="s1")
        pending_store._pending["s1"] = [item]

        assert pending_store.get_item("s1", comm.comm_id) is item

    def test_get_item_returns_none_for_unknown_comm_id(self, pending_store):
        assert pending_store.get_item("s1", "nonexistent") is None

    @pytest.mark.asyncio
    async def test_clear_pending(self, pending_store, temp_session):
        sid, sdir = temp_session
        await pending_store.enqueue(sid, sdir, make_comm(sid, content="a"))
        i2 = await pending_store.enqueue(sid, sdir, make_comm(sid, content="b"))
        await pending_store.mark_delivered(sid, sdir, i2.comm.comm_id)
        await pending_store.enqueue(sid, sdir, make_comm(sid, content="c"))

        count = await pending_store.clear_pending(sid, sdir)
        assert count == 2  # a and c, not b (already delivered)
        assert len(pending_store.get_pending(sid)) == 0

    @pytest.mark.asyncio
    async def test_clear_history_removes_terminal_items_only(self, pending_store, temp_session):
        sid, sdir = temp_session
        pending_item = await pending_store.enqueue(sid, sdir, make_comm(sid, content="pending"))
        delivered_item = await pending_store.enqueue(sid, sdir, make_comm(sid, content="delivered"))
        failed_item = await pending_store.enqueue(sid, sdir, make_comm(sid, content="failed"))
        await pending_store.mark_delivered(sid, sdir, delivered_item.comm.comm_id)
        await pending_store.mark_failed(sid, sdir, failed_item.comm.comm_id, "err")

        count = await pending_store.clear_history(sid, sdir)

        assert count == 2
        remaining = pending_store.get_all(sid)
        assert len(remaining) == 1
        assert remaining[0].comm.comm_id == pending_item.comm.comm_id

    @pytest.mark.asyncio
    async def test_clear_history_empty_returns_zero(self, pending_store, temp_session):
        sid, sdir = temp_session
        await pending_store.enqueue(sid, sdir, make_comm(sid, content="pending only"))
        count = await pending_store.clear_history(sid, sdir)
        assert count == 0


class TestPendingStoreValidation:
    """enqueue() routing guards."""

    @pytest.mark.asyncio
    async def test_enqueue_rejects_to_user_comm(self, pending_store, temp_session):
        sid, sdir = temp_session
        comm = Comm(comm_id=str(uuid.uuid4()), from_user=True, to_user=True, content="for the user")
        with pytest.raises(ValueError):
            await pending_store.enqueue(sid, sdir, comm)

    @pytest.mark.asyncio
    async def test_enqueue_rejects_session_id_mismatch(self, pending_store, temp_session):
        sid, sdir = temp_session
        comm = make_comm("some-other-session")
        with pytest.raises(ValueError, match="does not match session_id"):
            await pending_store.enqueue(sid, sdir, comm)

    @pytest.mark.asyncio
    async def test_enqueue_propagates_comm_validate_error(self, pending_store, temp_session):
        sid, sdir = temp_session
        # Neither to_user nor to_minion_id set -> comm.validate() raises before our own guard.
        comm = Comm(comm_id=str(uuid.uuid4()), from_user=True, content="nowhere")
        with pytest.raises(ValueError, match="exactly one destination"):
            await pending_store.enqueue(sid, sdir, comm)


class TestPendingStorePersistence:
    """JSONL persistence and replay-from-log, simulating a restart via two instances."""

    @pytest.mark.asyncio
    async def test_enqueue_writes_jsonl(self, pending_store, temp_session):
        sid, sdir = temp_session
        await pending_store.enqueue(sid, sdir, make_comm(sid, content="hello"))

        ledger_file = sdir / "pending.jsonl"
        assert ledger_file.exists()
        lines = ledger_file.read_text().strip().split('\n')
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["type"] == "enqueue"
        assert entry["comm"]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_replay_rebuilds_state_across_two_instances(self, temp_session):
        sid, sdir = temp_session

        # Phase 1: populate with one store instance (simulates pre-restart process)
        store1 = PendingStore()
        i1 = await store1.enqueue(sid, sdir, make_comm(sid, content="first"))
        i2 = await store1.enqueue(sid, sdir, make_comm(sid, content="second"))
        await store1.mark_delivered(sid, sdir, i1.comm.comm_id)
        await store1.cancel(sid, sdir, i2.comm.comm_id)
        i3 = await store1.enqueue(sid, sdir, make_comm(sid, content="third"))

        # Phase 2: a brand-new store instance replays the on-disk log (simulates restart)
        store2 = PendingStore()
        items = await store2.load(sid, sdir)
        assert len(items) == 3
        by_id = {i.comm.comm_id: i for i in items}
        assert by_id[i1.comm.comm_id].status == "delivered"
        assert by_id[i1.comm.comm_id].comm.content == "first"
        assert by_id[i2.comm.comm_id].status == "cancelled"
        assert by_id[i3.comm.comm_id].status == "pending"
        assert len(store2.get_pending(sid)) == 1

    @pytest.mark.asyncio
    async def test_replay_empty_file(self, temp_session):
        sid, sdir = temp_session
        store = PendingStore()
        items = await store.load(sid, sdir)
        assert items == []

    @pytest.mark.asyncio
    async def test_replay_malformed_line_skipped(self, temp_session):
        sid, sdir = temp_session
        comm = make_comm(sid, comm_id="comm-ok", content="ok")
        ledger_file = sdir / "pending.jsonl"
        good_entry = json.dumps({
            "type": "enqueue",
            "comm": comm.to_dict(),
            "session_id": sid,
            "position": 0,
            "created_at": 0.0,
        })
        ledger_file.write_text(f"not json\n{good_entry}\n")

        store = PendingStore()
        items = await store.load(sid, sdir)
        assert len(items) == 1
        assert items[0].comm.comm_id == "comm-ok"

    @pytest.mark.asyncio
    async def test_enqueue_with_dispatch_fields_persists_across_restart(self, temp_session):
        """enqueue()'s dispatch_content/dispatch_metadata kwargs survive a
        simulated restart (write via one PendingStore instance, load() via a
        fresh one) — the mechanism PR5's WHEN_IDLE dispatch depends on."""
        sid, sdir = temp_session

        store1 = PendingStore()
        item = await store1.enqueue(
            sid, sdir, make_comm(sid, content="raw content"),
            dispatch_content="formatted content",
            dispatch_metadata={"comm": {"from_name": "sender"}},
        )

        store2 = PendingStore()
        items = await store2.load(sid, sdir)
        assert len(items) == 1
        restored = items[0]
        assert restored.comm.comm_id == item.comm.comm_id
        assert restored.dispatch_content == "formatted content"
        assert restored.dispatch_metadata == {"comm": {"from_name": "sender"}}
        # The comm's own raw content is untouched by the dispatch override.
        assert restored.comm.content == "raw content"

    @pytest.mark.asyncio
    async def test_replay_preserves_failed_error(self, temp_session):
        sid, sdir = temp_session
        store1 = PendingStore()
        item = await store1.enqueue(sid, sdir, make_comm(sid))
        await store1.mark_failed(sid, sdir, item.comm.comm_id, "test error")

        store2 = PendingStore()
        items = await store2.load(sid, sdir)
        assert items[0].status == "failed"
        assert items[0].error == "test error"

    @pytest.mark.asyncio
    async def test_clear_history_persists_across_reload(self, temp_session):
        sid, sdir = temp_session
        store1 = PendingStore()
        pending_item = await store1.enqueue(sid, sdir, make_comm(sid, content="pending"))
        delivered_item = await store1.enqueue(sid, sdir, make_comm(sid, content="delivered"))
        await store1.mark_delivered(sid, sdir, delivered_item.comm.comm_id)
        await store1.clear_history(sid, sdir)

        store2 = PendingStore()
        items = await store2.load(sid, sdir)
        assert len(items) == 1
        assert items[0].comm.comm_id == pending_item.comm.comm_id

    @pytest.mark.asyncio
    async def test_clear_history_then_new_terminal_items_persist(self, temp_session):
        """Items added after clear must not be dropped on reload (boundary guard)."""
        sid, sdir = temp_session
        store1 = PendingStore()
        old_item = await store1.enqueue(sid, sdir, make_comm(sid, content="old"))
        await store1.mark_delivered(sid, sdir, old_item.comm.comm_id)
        await store1.clear_history(sid, sdir)

        new_item = await store1.enqueue(sid, sdir, make_comm(sid, content="new"))
        await store1.mark_delivered(sid, sdir, new_item.comm.comm_id)

        store2 = PendingStore()
        items = await store2.load(sid, sdir)
        comm_ids = {i.comm.comm_id for i in items}
        assert new_item.comm.comm_id in comm_ids
        assert old_item.comm.comm_id not in comm_ids


class TestPendingStorePagination:
    """get_page() contract parity with QueueManager.get_queue_page()."""

    @pytest.mark.asyncio
    async def test_get_page_paginates_most_recent_first(self, pending_store, temp_session):
        sid, sdir = temp_session
        await pending_store.enqueue(sid, sdir, make_comm(sid, content="a"))
        await pending_store.enqueue(sid, sdir, make_comm(sid, content="b"))
        await pending_store.enqueue(sid, sdir, make_comm(sid, content="c"))

        page = pending_store.get_page(sid, limit=2, offset=0)
        assert page["total"] == 3
        assert page["pending_count"] == 3
        assert page["limit"] == 2
        assert page["offset"] == 0
        assert page["has_more"] is True
        assert [i["comm"]["content"] for i in page["items"]] == ["c", "b"]

        next_page = pending_store.get_page(sid, limit=2, offset=2)
        assert next_page["has_more"] is False
        assert [i["comm"]["content"] for i in next_page["items"]] == ["a"]

    def test_get_page_empty_session(self, pending_store):
        page = pending_store.get_page("no-such-session")
        assert page == {
            "items": [],
            "total": 0,
            "limit": 100,
            "offset": 0,
            "has_more": False,
            "pending_count": 0,
        }

    @pytest.mark.asyncio
    async def test_get_page_pending_count_excludes_terminal(self, pending_store, temp_session):
        sid, sdir = temp_session
        await pending_store.enqueue(sid, sdir, make_comm(sid, content="a"))
        delivered = await pending_store.enqueue(sid, sdir, make_comm(sid, content="b"))
        await pending_store.mark_delivered(sid, sdir, delivered.comm.comm_id)

        page = pending_store.get_page(sid)
        assert page["total"] == 2
        assert page["pending_count"] == 1


class TestToLegacyQueueDict:
    """PendingItem.to_legacy_queue_dict() — the REST-view adapter (issue #2193 AC10),
    confirmed field-by-field against routers/queue.py's consumers (queue.js,
    QueueSection.vue)."""

    def test_maps_every_field(self):
        comm = make_comm("s1", content="hello", reset_first=True, metadata={"k": "v"})
        item = PendingItem(
            comm=comm, session_id="s1", status="pending", position=3,
            created_at=111.0, delivered_at=None, error=None, inject_timestamp=False,
        )
        d = item.to_legacy_queue_dict()
        assert d == {
            "queue_id": comm.comm_id,
            "session_id": "s1",
            "content": "hello",
            "reset_session": True,
            "metadata": {"k": "v"},
            "status": "pending",
            "position": 3,
            "created_at": 111.0,
            "sent_at": None,
            "error": None,
            "inject_timestamp": False,
        }

    def test_delivered_status_renamed_to_sent(self):
        comm = make_comm("s1")
        item = PendingItem(
            comm=comm, session_id="s1", status="delivered", delivered_at=222.0,
        )
        d = item.to_legacy_queue_dict()
        assert d["status"] == "sent"
        assert d["sent_at"] == 222.0

    def test_failed_and_cancelled_status_pass_through_unchanged(self):
        comm = make_comm("s1")
        for status in ("failed", "cancelled"):
            item = PendingItem(comm=comm, session_id="s1", status=status)
            assert item.to_legacy_queue_dict()["status"] == status

    def test_inject_timestamp_none_serializes_as_true(self):
        """None (no explicit preference) must present as True in the legacy
        shape — the historical default for anything that never expressed a
        preference, matching pre-PR6 queue items' always-True field."""
        comm = make_comm("s1")
        item = PendingItem(comm=comm, session_id="s1", inject_timestamp=None)
        assert item.to_legacy_queue_dict()["inject_timestamp"] is True

    def test_inject_timestamp_explicit_false_preserved(self):
        comm = make_comm("s1")
        item = PendingItem(comm=comm, session_id="s1", inject_timestamp=False)
        assert item.to_legacy_queue_dict()["inject_timestamp"] is False


class TestGetPageLegacyShape:
    """get_page(legacy_shape=True) (issue #2193 PR6/AC10)."""

    @pytest.mark.asyncio
    async def test_legacy_shape_items_are_flat(self, pending_store, temp_session):
        sid, sdir = temp_session
        await pending_store.enqueue(sid, sdir, make_comm(sid, content="flat me"))

        page = pending_store.get_page(sid, legacy_shape=True)
        item = page["items"][0]
        assert item["content"] == "flat me"
        assert "comm" not in item

    @pytest.mark.asyncio
    async def test_legacy_shape_pagination_math_unchanged(self, pending_store, temp_session):
        sid, sdir = temp_session
        for c in ("a", "b", "c"):
            await pending_store.enqueue(sid, sdir, make_comm(sid, content=c))

        legacy_page = pending_store.get_page(sid, limit=2, offset=0, legacy_shape=True)
        default_page = pending_store.get_page(sid, limit=2, offset=0)
        for key in ("total", "limit", "offset", "has_more", "pending_count"):
            assert legacy_page[key] == default_page[key]
        assert [i["content"] for i in legacy_page["items"]] == ["c", "b"]

    def test_legacy_shape_defaults_to_false(self, pending_store):
        """legacy_shape is opt-in — default behavior (nested comm shape) unchanged."""
        page = pending_store.get_page("no-such-session")
        assert page["items"] == []


class TestRequeue:
    """PendingStore.requeue() — new in issue #2193 PR6, a gap left by PR2 (no
    analog existed before). Ports QueueManager.requeue()'s exact semantics."""

    @pytest.mark.asyncio
    async def test_requeue_delivered_item_creates_new_pending_entry_at_front(
        self, pending_store, temp_session
    ):
        sid, sdir = temp_session
        original = await pending_store.enqueue(sid, sdir, make_comm(sid, content="redo me"))
        await pending_store.mark_delivered(sid, sdir, original.comm.comm_id)
        other_pending = await pending_store.enqueue(sid, sdir, make_comm(sid, content="unrelated"))

        new_item = await pending_store.requeue(sid, sdir, original.comm.comm_id)

        assert new_item is not None
        assert new_item.comm.comm_id != original.comm.comm_id
        assert new_item.comm.content == "redo me"
        assert new_item.status == "pending"
        assert new_item.position < other_pending.position
        # Original item's own history is untouched.
        assert original.status == "delivered"

    @pytest.mark.asyncio
    async def test_requeue_failed_item_succeeds(self, pending_store, temp_session):
        sid, sdir = temp_session
        original = await pending_store.enqueue(sid, sdir, make_comm(sid, content="retry me"))
        await pending_store.mark_failed(sid, sdir, original.comm.comm_id, "boom")

        new_item = await pending_store.requeue(sid, sdir, original.comm.comm_id)

        assert new_item is not None
        assert new_item.status == "pending"

    @pytest.mark.asyncio
    async def test_requeue_still_pending_item_returns_none(self, pending_store, temp_session):
        """Matches QueueManager.requeue()'s guard — only delivered/failed items
        may be requeued."""
        sid, sdir = temp_session
        item = await pending_store.enqueue(sid, sdir, make_comm(sid, content="still pending"))

        result = await pending_store.requeue(sid, sdir, item.comm.comm_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_requeue_cancelled_item_returns_none(self, pending_store, temp_session):
        sid, sdir = temp_session
        item = await pending_store.enqueue(sid, sdir, make_comm(sid, content="cancelled"))
        await pending_store.cancel(sid, sdir, item.comm.comm_id)

        result = await pending_store.requeue(sid, sdir, item.comm.comm_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_requeue_unknown_comm_id_returns_none(self, pending_store, temp_session):
        sid, sdir = temp_session
        result = await pending_store.requeue(sid, sdir, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_requeue_preserves_dispatch_overrides(self, pending_store, temp_session):
        sid, sdir = temp_session
        original = await pending_store.enqueue(
            sid, sdir, make_comm(sid, content="raw"),
            inject_timestamp=False,
            dispatch_content="formatted",
            dispatch_metadata={"k": "v"},
        )
        await pending_store.mark_failed(sid, sdir, original.comm.comm_id, "boom")

        new_item = await pending_store.requeue(sid, sdir, original.comm.comm_id)

        assert new_item.inject_timestamp is False
        assert new_item.dispatch_content == "formatted"
        assert new_item.dispatch_metadata == {"k": "v"}
