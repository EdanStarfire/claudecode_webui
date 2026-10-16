"""
Queue Processor for Claude Code WebUI

Background asyncio task that delivers queued messages to sessions
with timing guards, auto-start, error-halt, and pause support.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from shared.logging_config import get_logger

from .models.legion_models import Comm, CommType, InterruptPriority
from .session_lifecycle import is_errored

if TYPE_CHECKING:
    from .session_coordinator import SessionCoordinator

queue_proc_logger = get_logger('queue_processor', category='QUEUE')
logger = logging.getLogger(__name__)


class QueueProcessor:
    """
    Delivers queued messages to a session one at a time.

    Processing loop per session:
      1. Peek next pending item
      2. If paused → sleep, continue
      3. If session ERROR → STOP (user must intervene)
      4. Wrap the item into a Comm and hand it to
         coordinator.delivery_gate.deliver(wait_for_idle=True) — auto-start,
         reset, the min_wait_seconds delay, the send itself, and the
         min_idle_seconds post-send wait all live there now (issue #2189
         coordinator-split PR 10; see delivery_gate.py)
      5. mark_sent/mark_failed + broadcast based on the DeliveryResult
    """

    def __init__(self, coordinator: "SessionCoordinator"):
        self._coordinator = coordinator
        # session_id -> asyncio.Task
        self._tasks: dict[str, asyncio.Task] = {}
        # Callback for broadcasting queue updates via WebSocket
        self._broadcast_callback: Callable[[str, str, dict], Any] | None = None
        # Callback for broadcasting queue_update (enqueued) events — kept separate
        # from _broadcast_callback above (see issue #2189 PR 8 staging notes).
        self._enqueue_broadcast_callback: Callable[[str, str, dict], Any] | None = None

    def set_broadcast_callback(
        self, callback: Callable[[str, str, dict], Any]
    ) -> None:
        """
        Set callback for broadcasting queue updates.

        Args:
            callback: async fn(session_id, action, item_dict)
        """
        self._broadcast_callback = callback

    def set_enqueue_broadcast_callback(
        self, callback: Callable[[str, str, dict], Any]
    ) -> None:
        """Set the callback for broadcasting queue_update (enqueued) events."""
        self._enqueue_broadcast_callback = callback

    # =========================================================================
    # Message Queue Operations (Issue #500)
    # =========================================================================

    async def _initialize_queues(self) -> None:
        """Load message queues for all existing sessions on startup."""
        try:
            sessions = await self._coordinator.session_manager.list_sessions()
            for session in sessions:
                session_id = session.session_id
                session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
                if session_dir:
                    await self._coordinator.queue_manager.load_queue(session_id, session_dir)
                    # Auto-start processor if there are pending items
                    if self._coordinator.queue_manager.get_pending_count(session_id) > 0:
                        queue_paused = getattr(session, 'queue_paused', False)
                        if not queue_paused:
                            self.ensure_running(session_id)
        except Exception:
            logger.exception("Failed to initialize queues")

    async def enqueue_message(
        self,
        session_id: str,
        content: str,
        reset_session: bool | None = None,
        metadata: dict | None = None,
        inject_timestamp: bool = True,
    ) -> dict:
        """Enqueue a message for a session. Returns the queue item dict."""
        session_info = await self._coordinator.session_manager.get_session_info(session_id)
        if not session_info:
            raise ValueError(f"Session {session_id} not found")

        session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
        if not session_dir:
            raise ValueError(f"Session directory not found for {session_id}")

        queue_config = getattr(session_info, 'queue_config', None) or {}
        max_queue_size = queue_config.get("max_queue_size", 100)

        # Use session default if not specified
        if reset_session is None:
            reset_session = queue_config.get("default_reset_session", True)

        item = await self._coordinator.queue_manager.enqueue(
            session_id=session_id,
            session_dir=session_dir,
            content=content,
            reset_session=reset_session,
            metadata=metadata,
            max_queue_size=max_queue_size,
            inject_timestamp=inject_timestamp,
        )

        # Start processor if not paused
        queue_paused = getattr(session_info, 'queue_paused', False)
        if not queue_paused:
            self.ensure_running(session_id)

        item_dict = item.to_dict()
        if self._enqueue_broadcast_callback:
            try:
                if asyncio.iscoroutinefunction(self._enqueue_broadcast_callback):
                    await self._enqueue_broadcast_callback(session_id, "enqueued", item_dict)
                else:
                    self._enqueue_broadcast_callback(session_id, "enqueued", item_dict)
            except Exception:
                logger.exception(f"Failed to broadcast queue enqueued for {session_id}")
        return item_dict

    async def cancel_queue_item(self, session_id: str, queue_id: str) -> dict | None:
        """Cancel a pending queue item. Returns the item dict or None."""
        session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
        if not session_dir:
            return None
        item = await self._coordinator.queue_manager.cancel(session_id, session_dir, queue_id)
        return item.to_dict() if item else None

    async def requeue_item(self, session_id: str, queue_id: str) -> dict | None:
        """Re-queue a sent/failed item at the front. Returns the new item dict."""
        session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
        if not session_dir:
            return None
        item = await self._coordinator.queue_manager.requeue(session_id, session_dir, queue_id)
        if item:
            session_info = await self._coordinator.session_manager.get_session_info(session_id)
            queue_paused = getattr(session_info, 'queue_paused', False) if session_info else False
            if not queue_paused:
                self.ensure_running(session_id)
        return item.to_dict() if item else None

    async def clear_queue(self, session_id: str) -> int:
        """Clear all pending queue items. Returns count of cancelled items."""
        session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
        if not session_dir:
            return 0
        return await self._coordinator.queue_manager.clear_pending(session_id, session_dir)

    async def clear_queue_history(self, session_id: str) -> int:
        """Remove terminal (sent/failed/cancelled) queue items. Returns count removed."""
        session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
        if not session_dir:
            return 0
        return await self._coordinator.queue_manager.clear_history(session_id, session_dir)

    async def pause_queue(self, session_id: str, paused: bool) -> bool:
        """Pause or resume the queue for a session."""
        success = await self._coordinator.session_manager.update_session(
            session_id, queue_paused=paused
        )
        if success and not paused:
            # Resume: start processor if there are pending items
            if self._coordinator.queue_manager.get_pending_count(session_id) > 0:
                self.ensure_running(session_id)
        return success

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def ensure_running(self, session_id: str) -> None:
        """Start the processor for a session if it's not already running."""
        task = self._tasks.get(session_id)
        if task and not task.done():
            return  # Already running

        queue_proc_logger.info(f"Starting queue processor for session {session_id}")
        self._tasks[session_id] = asyncio.create_task(
            self._process_loop(session_id),
            name=f"queue-processor-{session_id}",
        )

    def stop(self, session_id: str) -> None:
        """Stop the processor for a session."""
        task = self._tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            queue_proc_logger.info(f"Stopped queue processor for session {session_id}")

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    # =========================================================================
    # Main processing loop
    # =========================================================================

    async def _process_loop(self, session_id: str) -> None:
        """Main processing loop for a session's queue."""
        queue_proc_logger.info(f"Queue processor started for session {session_id}")

        try:
            while True:
                item = self._coordinator.queue_manager.peek_next(session_id)
                if not item:
                    queue_proc_logger.debug(f"No pending items for session {session_id}, exiting processor")
                    break

                # Check pause state
                session_info = await self._coordinator.session_manager.get_session_info(session_id)
                if not session_info:
                    queue_proc_logger.warning(f"Session {session_id} not found, stopping processor")
                    break

                queue_paused = getattr(session_info, 'queue_paused', False)

                if queue_paused:
                    await asyncio.sleep(1)
                    continue

                # Check session state
                if is_errored(session_info.state):
                    queue_proc_logger.warning(
                        f"Session {session_id} in ERROR state — halting queue processor. "
                        "User must fix session manually."
                    )
                    break

                # Issue #2189 (coordinator-split PR 10): auto-start/wait/send/busy-wait
                # dance now lives in DeliveryGate — see delivery_gate.py for the
                # verbatim-ported AUTO_START_STATES/_wait_for_active/_wait_for_idle
                # mechanics (issue #2159's PAUSED-unbounded-wait nuance included).
                comm = Comm(
                    comm_id=item.queue_id,
                    from_user=True,
                    to_minion_id=session_id,
                    content=item.content,
                    comm_type=CommType.SYSTEM,
                    interrupt_priority=InterruptPriority.NONE,
                    metadata=item.metadata or {},
                )
                result = await self._coordinator.delivery_gate.deliver(
                    comm,
                    wait_for_idle=True,
                    reset_first=item.reset_session,
                    inject_timestamp=item.inject_timestamp,
                )

                session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
                if not result.success:
                    # error=None is DeliveryGate's signal for the one pre-PR
                    # asymmetry it preserves verbatim (post-reset wait timeout):
                    # stop for now without permanently failing the item.
                    if result.error:
                        queue_proc_logger.error(
                            f"Queue item {item.queue_id} failed for session {session_id}: {result.error}"
                        )
                        if session_dir:
                            await self._coordinator.queue_manager.mark_failed(
                                session_id, session_dir, item.queue_id, result.error
                            )
                            await self._broadcast("failed", session_id, item)
                    break

                if session_dir:
                    await self._coordinator.queue_manager.mark_sent(
                        session_id, session_dir, item.queue_id
                    )
                    await self._broadcast("sent", session_id, item)

                queue_proc_logger.info(f"Queue item {item.queue_id} sent to session {session_id}")

        except asyncio.CancelledError:
            queue_proc_logger.info(f"Queue processor cancelled for session {session_id}")
        except Exception as e:
            logger.error(f"Queue processor error for session {session_id}: {e}", exc_info=True)
        finally:
            self._tasks.pop(session_id, None)
            queue_proc_logger.info(f"Queue processor exited for session {session_id}")

    # =========================================================================
    # Helpers
    # =========================================================================

    async def _broadcast(self, action: str, session_id: str, item: Any) -> None:
        """Broadcast a queue update if callback is set."""
        if self._broadcast_callback:
            try:
                item_dict = item.to_dict() if hasattr(item, 'to_dict') else {}
                if asyncio.iscoroutinefunction(self._broadcast_callback):
                    await self._broadcast_callback(session_id, action, item_dict)
                else:
                    self._broadcast_callback(session_id, action, item_dict)
            except Exception as e:
                logger.error(f"Failed to broadcast queue update: {e}")
