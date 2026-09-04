"""Shared git-restart primitives used by both tiers' /api/system/restart (issue #498).

Frontend and Backend each own a separate repo checkout (same one in the common
embedded case, potentially two different ones in remote-Backend deployments) and
each support "pull latest" or "switch to an explicit branch/commit" before
restarting themselves. This module holds the pieces that are identical between
them: git ref validation and the low-level git-command runner.
"""

import asyncio
import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def validate_git_ref_component(value: str, field_name: str) -> None:
    """Reject values git would interpret as a CLI flag rather than a ref (issue #1760)."""
    if not value or value.startswith("-"):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {value!r}")


async def run_git_command(args: list[str], cwd: str, allow_nonzero: bool = False) -> str | None:
    """Run a git command via asyncio.create_subprocess_exec and return stdout, or None on error."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0 and not allow_nonzero:
            return None
        return stdout.decode().strip()
    except (TimeoutError, FileNotFoundError, OSError) as e:
        logger.debug("Git command failed: %s - %s", args, e)
        return None
