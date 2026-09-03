"""Shared asyncio task utilities."""

import asyncio
import logging

logger = logging.getLogger(__name__)


def task_done_log_exception(task: asyncio.Task) -> None:
    """Done callback: log any unhandled exception from a background task."""
    if not task.cancelled() and (exc := task.exception()):
        logger.error(
            "Unhandled exception in background task %s: %s",
            task.get_name(), exc, exc_info=exc,
        )
