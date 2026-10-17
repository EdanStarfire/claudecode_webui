"""
Shared history-distillation trigger shell.

Extracted (issue #2201) from the duplicated "check enabled -> launch -> log"
shell shared by SessionLauncher._archive_session_for_reset (reset-time
distillation) and ArchiveCoordinator._handle_compact_boundary
(compaction-boundary distillation). The worker bodies each call site launches
(distill_session_history / _distill_compaction) are genuinely different and
stay where they are — only this shell is unified.

Pre-existing divergences between the two call sites are preserved via the
`launch_check` parameter and by NOT owning the None-`session_info` guard or
any exception handling here — callers keep their own, exactly as today.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from .config_resolution import resolve_effective_config
from .task_utils import task_done_log_exception

if TYPE_CHECKING:
    from .profile_manager import ProfileManager
    from .session_manager import SessionInfo
    from .template_manager import TemplateManager

coord_logger = get_logger('coordinator', category='COORDINATOR')


async def schedule_history_distillation(
    session_info: SessionInfo | None,
    template_manager: TemplateManager,
    profile_manager: ProfileManager | None,
    *,
    task_coro_factory: Callable[[], Coroutine],
    debug_log_message: str,
    launch_check: Callable[[], bool] | None = None,
) -> asyncio.Task | None:
    """Resolve effective config, and if distillation is enabled (and *launch_check*
    passes, when given), fire-and-forget the task built by *task_coro_factory*.

    No None-guard on *session_info* and no try/except here — both stay the
    caller's responsibility, matching each call site's current behavior.
    """
    eff = await resolve_effective_config(session_info, template_manager, profile_manager)
    if not eff.history_distillation_enabled:
        return None
    if launch_check is not None and not launch_check():
        return None
    task = asyncio.create_task(task_coro_factory())
    task.add_done_callback(task_done_log_exception)
    coord_logger.debug(debug_log_message)
    return task
