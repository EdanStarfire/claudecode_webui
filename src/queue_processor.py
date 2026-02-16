"""
Queue Processor for Claude Code WebUI

Background asyncio task that delivers queued messages to sessions
with timing guards, auto-start, error-halt, and pause support.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .logging_config import get_logger
from .session_manager import SessionState

if TYPE_CHECKING:
    from .session_coordinator import SessionCoordinator

queue_proc_logger = get_logger('queue_processor', category='QUEUE')
logger = logging.getLogger(__name__)

# Default timing configuration
DEFAULT_MIN_WAIT_SECONDS = 10
DEFAULT_MIN_IDLE_SECONDS = 10


class QueueProcessor:
    """
    Delivers queued messages to a session one at a time.

    Processing loop per session:
      1. Peek next pending item
      2. If paused → sleep, continue
      3. If session ERROR → STOP (user must intervene)
      4. If session CREATED/TERMINATED → auto-start
      5. Wait for ACTIVE state
      6. If reset_session → coordinator.reset_session()
      7. Wait min_wait_seconds after session active
      8. Send message via coordinator.send_message()
      9. Poll is_processing (no timeout, supports indefinite waits)
      10. After idle for min_idle_seconds → mark sent, next item
    """

    def __init__(self, coordinator: "SessionCoordinator"):
        self._coordinator = coordinator
        # session_id -> asyncio.Task
        self._tasks: dict[str, asyncio.Task] = {}
        # Callback for broadcasting queue updates via WebSocket
        self._broadcast_callback: Callable[[str, str, dict], Any] | None = None

    def set_broadcast_callback(
        self, callback: Callable[[str, str, dict], Any]
    ) -> None:
        """
        Set callback for broadcasting queue updates.

        Args:
            callback: async fn(session_id, action, item_dict)
        """
        self._broadcast_callback = callback

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

                queue_config = getattr(session_info, 'queue_config', None) or {}
                queue_paused = getattr(session_info, 'queue_paused', False)

                if queue_paused:
                    await asyncio.sleep(1)
                    continue

                # Check session state
                if session_info.state == SessionState.ERROR:
                    queue_proc_logger.warning(
                        f"Session {session_id} in ERROR state — halting queue processor. "
                        "User must fix session manually."
                    )
                    break

                # Auto-start session if needed
                if session_info.state in (SessionState.CREATED, SessionState.TERMINATED):
                    queue_proc_logger.info(f"Auto-starting session {session_id} for queue processing")
                    # Get permission callback from factory if available
                    perm_callback = None
                    if self._coordinator._permission_callback_factory:
                        perm_callback = self._coordinator._permission_callback_factory(session_id)
                    success = await self._coordinator.start_session(session_id, perm_callback)
                    if not success:
                        queue_proc_logger.error(f"Failed to auto-start session {session_id}")
                        session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
                        if session_dir:
                            await self._coordinator.queue_manager.mark_failed(
                                session_id, session_dir, item.queue_id,
                                "Failed to auto-start session"
                            )
                            await self._broadcast("failed", session_id, item)
                        break

                # Wait for session to become active
                if not await self._wait_for_active(session_id):
                    queue_proc_logger.error(f"Session {session_id} did not become active")
                    session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
                    if session_dir:
                        await self._coordinator.queue_manager.mark_failed(
                            session_id, session_dir, item.queue_id,
                            "Session did not become active"
                        )
                        await self._broadcast("failed", session_id, item)
                    break

                # Reset session if requested
                if item.reset_session:
                    queue_proc_logger.info(f"Resetting session {session_id} before queue item {item.queue_id}")
                    perm_callback = None
                    if self._coordinator._permission_callback_factory:
                        perm_callback = self._coordinator._permission_callback_factory(session_id)
                    success = await self._coordinator.reset_session(session_id, perm_callback)
                    if not success:
                        queue_proc_logger.error(f"Failed to reset session {session_id}")
                        session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
                        if session_dir:
                            await self._coordinator.queue_manager.mark_failed(
                                session_id, session_dir, item.queue_id,
                                "Failed to reset session"
                            )
                            await self._broadcast("failed", session_id, item)
                        break

                    # Wait for session active again after reset
                    if not await self._wait_for_active(session_id):
                        queue_proc_logger.error(f"Session {session_id} not active after reset")
                        break

                # Wait min_wait_seconds before sending
                min_wait = queue_config.get("min_wait_seconds", DEFAULT_MIN_WAIT_SECONDS)
                queue_proc_logger.debug(f"Waiting {min_wait}s before sending to session {session_id}")
                await asyncio.sleep(min_wait)

                # Re-check pause before sending
                session_info = await self._coordinator.session_manager.get_session_info(session_id)
                if session_info and getattr(session_info, 'queue_paused', False):
                    continue

                # Send the message
                queue_proc_logger.info(
                    f"Sending queued message {item.queue_id} to session {session_id}: "
                    f"{item.content[:80]}..."
                )
                send_result = await self._coordinator.send_message(session_id, item.content)
                if not send_result:
                    queue_proc_logger.error(f"Failed to send queue item {item.queue_id}")
                    session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
                    if session_dir:
                        await self._coordinator.queue_manager.mark_failed(
                            session_id, session_dir, item.queue_id,
                            "Failed to send message"
                        )
                        await self._broadcast("failed", session_id, item)
                    break

                # Poll for completion with idle timer
                min_idle = queue_config.get("min_idle_seconds", DEFAULT_MIN_IDLE_SECONDS)
                completed = await self._wait_for_idle(session_id, item.queue_id, min_idle)

                if not completed:
                    # Session entered error state during processing
                    break

                # Mark as sent
                session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
                if session_dir:
                    await self._coordinator.queue_manager.mark_sent(
                        session_id, session_dir, item.queue_id
                    )
                    await self._broadcast("sent", session_id, item)

                queue_proc_logger.info(f"Queue item {item.queue_id} sent successfully for session {session_id}")

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

    async def _wait_for_active(self, session_id: str, timeout: float = 120) -> bool:
        """Wait for a session to reach ACTIVE state. Returns False on error/timeout."""
        elapsed = 0.0
        while elapsed < timeout:
            info = await self._coordinator.session_manager.get_session_info(session_id)
            if not info:
                return False
            if info.state == SessionState.ACTIVE:
                return True
            if info.state == SessionState.ERROR:
                return False
            await asyncio.sleep(1)
            elapsed += 1
        return False

    async def _wait_for_idle(
        self, session_id: str, queue_id: str, min_idle_seconds: float
    ) -> bool:
        """
        Wait for the session to be idle for min_idle_seconds after message delivery.

        Returns True if idle reached, False if session errored.
        No timeout — supports indefinite waits (e.g., overnight permission waits).
        """
        idle_start: float | None = None

        while True:
            info = await self._coordinator.session_manager.get_session_info(session_id)
            if not info:
                return False

            # Session entered error state — mark item failed and stop
            if info.state == SessionState.ERROR:
                session_dir = await self._coordinator.session_manager.get_session_directory(session_id)
                if session_dir:
                    await self._coordinator.queue_manager.mark_failed(
                        session_id, session_dir, queue_id,
                        "Session entered error state during processing"
                    )
                    item = self._coordinator.queue_manager._find_item(session_id, queue_id)
                    if item:
                        await self._broadcast("failed", session_id, item)
                return False

            # Check pause
            if getattr(info, 'queue_paused', False):
                idle_start = None
                await asyncio.sleep(1)
                continue

            if info.is_processing:
                # Still processing — reset idle timer
                idle_start = None
            else:
                # Not processing — start/continue idle timer
                if idle_start is None:
                    idle_start = asyncio.get_event_loop().time()

                elapsed_idle = asyncio.get_event_loop().time() - idle_start
                if elapsed_idle >= min_idle_seconds:
                    return True

            await asyncio.sleep(1)

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
