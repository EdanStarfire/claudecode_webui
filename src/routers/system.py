"""System restart endpoint: /api/system/restart (issue #498).

Intercepts the one /api/system/* route that needs Frontend-side handling —
everything else (git-status, git-branches, git-commits, docker-status, etc.)
is still accurate to relay straight through to Backend post-split, since in
the embedded (auto-started Backend) case they're literally the same repo
checkout.

Restart itself can't be a relay: os.execv only replaces the process that
receives the call. Blindly forwarding this to Backend (the pre-#498
behavior, inherited by the mechanical move of routers/system.py into
backend/) restarted Backend only — Frontend kept running whatever process
image it already had in memory, so pulled changes to src/, main.py, or
shared/ were silently never applied.
"""

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.exception_handlers import handle_exceptions
from shared.git_restart import run_git_command, validate_git_ref_component

logger = logging.getLogger(__name__)


class RestartRequest(BaseModel):
    branch: str | None = None
    commit: str | None = None


async def _restart_to_target(project_root: Path, payload: "RestartRequest") -> str:
    """Switch Frontend's repo to a specific branch/commit before restart.

    Mirrors backend/routers/system.py's _restart_to_target exactly (issue
    #1760's safety checks — uncommitted-changes guard, TOCTOU re-check before
    the destructive reset). Kept as parallel, independent code rather than a
    shared function: Frontend and Backend each restart a different process
    against a different (in the general/remote case) repo, so only the
    validation/subprocess primitives are truly identical — those are already
    deduplicated into shared/git_restart.py.
    """
    for value, field_name in ((payload.branch, "branch"), (payload.commit, "commit")):
        if value is not None:
            validate_git_ref_component(value, field_name)

    project_root_str = str(project_root)

    status = await run_git_command(["git", "status", "--porcelain"], project_root_str)
    if status is None:
        raise HTTPException(
            status_code=500, detail="Could not determine git status; aborting for safety."
        )
    if status:
        raise HTTPException(
            status_code=409,
            detail="Uncommitted changes present. Commit, stash, or discard them before "
                   "switching branch or commit.",
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "fetch", "origin",
            cwd=project_root_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=15)
    except (TimeoutError, OSError):
        pass  # tolerate fetch failure, matching git-status's existing behavior

    current_branch = await run_git_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], project_root_str
    )
    branch = payload.branch or current_branch
    if not branch:
        raise HTTPException(status_code=500, detail="Could not resolve current branch")

    local_ref_check = await run_git_command(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], project_root_str
    )
    if local_ref_check:
        checkout_result = subprocess.run(
            ["git", "checkout", branch],
            cwd=project_root, capture_output=True, text=True, timeout=30,
        )
    else:
        checkout_result = subprocess.run(
            ["git", "checkout", "-b", branch, "--track", f"origin/{branch}"],
            cwd=project_root, capture_output=True, text=True, timeout=30,
        )
    if checkout_result.returncode != 0:
        raise HTTPException(
            status_code=500, detail=f"git checkout failed: {checkout_result.stderr.strip()}"
        )

    # Re-check right before the destructive reset to shrink the TOCTOU window opened by
    # the fetch/checkout above.
    pre_reset_status = await run_git_command(
        ["git", "status", "--porcelain"], project_root_str
    )
    if pre_reset_status is None:
        raise HTTPException(
            status_code=500, detail="Could not determine git status; aborting for safety."
        )
    if pre_reset_status:
        raise HTTPException(
            status_code=409,
            detail="Uncommitted changes appeared before the reset could complete. Aborting.",
        )

    reset_target = payload.commit or f"origin/{branch}"
    reset_result = subprocess.run(
        ["git", "reset", "--hard", reset_target],
        cwd=project_root, capture_output=True, text=True, timeout=30,
    )
    if reset_result.returncode != 0:
        raise HTTPException(
            status_code=500, detail=f"git reset failed: {reset_result.stderr.strip()}"
        )

    short_hash = await run_git_command(
        ["git", "rev-parse", "--short", "HEAD"], project_root_str
    )
    return f"Switched to {branch} @ {short_hash or reset_target}"


async def _finish_restart(webui) -> None:
    """Stop Frontend's own background tasks/clients, optionally stop the
    current Backend child (embedded mode only), then re-exec this process.

    Runs as a fire-and-forget background task after the HTTP response has
    already been sent — extracted to a standalone function so it's directly
    awaitable from tests instead of only reachable via the scheduled task.
    """
    await asyncio.sleep(0.5)
    logger.info("Executing Frontend os.execv restart...")
    if webui._oauth_resync_task is not None:
        webui._oauth_resync_task.cancel()
    try:
        await webui.poll_relay.stop()
    except Exception:
        logger.warning("Error stopping poll_relay during restart")
    try:
        await webui.backend_client.aclose()
    except Exception:
        logger.warning("Error closing backend_client during restart")
    if webui.backend_supervisor is not None:
        try:
            await webui.backend_supervisor.stop()
        except Exception:
            logger.warning("Error stopping Backend during restart")
    os.execv(sys.executable, [sys.executable] + sys.argv)


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.post("/api/system/restart", status_code=202)
    @handle_exceptions("restart server")
    async def restart_server(payload: RestartRequest | None = None):
        """Pull latest code (default) or switch Frontend to a specific
        branch/commit, then restart.

        Embedded mode (webui.backend_supervisor is not None): also stops the
        current Backend child before restarting Frontend. Frontend's own
        re-exec re-runs main.py's normal startup sequence, which auto-starts a
        fresh Backend from the same, already-pulled repo on disk — reusing the
        existing, already-tested startup/readiness-gating flow rather than
        adding new orchestration here. (Asking the current Backend to
        os.execv itself instead would be wrong: execv doesn't change its PID,
        so backend_supervisor's crash-monitor would never see it "exit" — it
        would keep running under a supervisor that's about to stop watching
        it, orphaned, while a second, fresh Backend also starts on re-exec.)

        Remote mode (webui.backend_supervisor is None): only Frontend is
        restarted. A remote Backend is a separate deployment this action does
        not attempt to manage — coordinated split-repo restart/rollback for
        that case is tracked separately.
        """
        now = time.time()
        if now - webui._last_restart_time < 30:
            remaining = int(30 - (now - webui._last_restart_time))
            raise HTTPException(
                status_code=429,
                detail=f"Rate limited. Try again in {remaining} seconds."
            )
        webui._last_restart_time = now

        project_root = Path(__file__).parent.parent.parent
        has_custom_target = payload is not None and (payload.branch or payload.commit)

        if not has_custom_target:
            try:
                result = subprocess.run(
                    ["git", "pull"],
                    cwd=project_root, capture_output=True, text=True, timeout=60
                )
                if result.returncode != 0:
                    raise HTTPException(
                        status_code=500,
                        detail=f"git pull failed: {result.stderr.strip()}"
                    )
                pull_output = result.stdout.strip()
            except subprocess.TimeoutExpired as e:
                raise HTTPException(status_code=504, detail="git pull timed out") from e
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("git pull failed")
                raise HTTPException(status_code=500, detail=str(e)) from e
        else:
            pull_output = await _restart_to_target(project_root, payload)

        try:
            sync_result = subprocess.run(
                ["uv", "sync"],
                cwd=project_root, capture_output=True, text=True, timeout=120
            )
            if sync_result.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"uv sync failed: {sync_result.stderr.strip()}"
                )
            sync_output = sync_result.stdout.strip()
        except subprocess.TimeoutExpired as e:
            raise HTTPException(status_code=504, detail="uv sync timed out") from e
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("uv sync failed")
            raise HTTPException(status_code=500, detail=str(e)) from e

        # Append restart notice to the local UI poll queue — same event shape Backend
        # already used for its own restart broadcast, so no frontend JS change is needed.
        try:
            webui.ui_queue.append({
                "type": "server_restarting",
                "message": "Server is restarting...",
                "pull_output": pull_output,
                "sync_output": sync_output,
            })
        except Exception:
            logger.warning("Failed to append restart notice")

        asyncio.get_event_loop().create_task(_finish_restart(webui))

        return {
            "status": "restarting",
            "message": "Server is pulling latest code and restarting...",
            "pull_output": pull_output,
            "sync_output": sync_output,
        }

    return router
