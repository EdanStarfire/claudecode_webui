"""System endpoints: /api/system/*"""

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..exception_handlers import handle_exceptions

logger = logging.getLogger(__name__)


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/system/secrets-backend-status")
    @handle_exceptions("get secrets backend status")
    async def get_secrets_backend_status():
        """Return active keyring backend name and any warning message (issue #827)."""
        from src.secrets_keyring import get_backend_status
        return get_backend_status()

    @router.get("/api/system/docker-status")
    @handle_exceptions("check docker status")
    async def get_docker_status():
        """Check Docker availability and image status (issue #496)."""
        from src.docker_utils import check_docker_available
        status = await check_docker_available()
        return status

    @router.get("/api/system/git-status")
    @handle_exceptions("get git status")
    async def get_git_status():
        """Return current git branch, last commit, remote commit info, and dirty state."""
        project_root = str(Path(__file__).parent.parent.parent)

        branch = await webui._run_git_command(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], project_root
        )
        commit_hash = await webui._run_git_command(
            ["git", "log", "-1", "--format=%H"], project_root
        )
        commit_message = await webui._run_git_command(
            ["git", "log", "-1", "--format=%s"], project_root
        )
        status = await webui._run_git_command(
            ["git", "status", "--porcelain"], project_root
        )

        # Remote commit info
        remote_commit_hash = ""
        remote_commit_message = ""
        commits_behind = 0
        remote_fetch_failed = False

        # Detect remote tracking branch
        remote_branch = None
        if branch and branch != "HEAD":
            # Try the tracking branch for the current local branch
            candidate = f"origin/{branch}"
            ref_exists = await webui._run_git_command(
                ["git", "rev-parse", "--verify", candidate], project_root
            )
            if ref_exists:
                remote_branch = candidate

        if not remote_branch:
            # Detached HEAD, no remote tracking, or unknown — try origin/HEAD
            origin_head = await webui._run_git_command(
                ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"], project_root
            )
            if origin_head:
                remote_branch = origin_head
            else:
                # Fall back to origin/main, then origin/master
                for fallback in ["origin/main", "origin/master"]:
                    ref_check = await webui._run_git_command(
                        ["git", "rev-parse", "--verify", fallback], project_root
                    )
                    if ref_check:
                        remote_branch = fallback
                        break

        # Fetch from origin (15s timeout)
        if remote_branch:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "fetch", "origin",
                    cwd=project_root,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=15)
                if proc.returncode != 0:
                    remote_fetch_failed = True
            except (TimeoutError, OSError):
                remote_fetch_failed = True

            # Read remote commit info (works even if fetch failed, using stale refs)
            r_hash = await webui._run_git_command(
                ["git", "log", "-1", "--format=%H", remote_branch], project_root
            )
            r_msg = await webui._run_git_command(
                ["git", "log", "-1", "--format=%s", remote_branch], project_root
            )
            if r_hash:
                remote_commit_hash = r_hash
                remote_commit_message = r_msg or ""
                behind = await webui._run_git_command(
                    ["git", "rev-list", "--count",
                     f"HEAD..{remote_branch}"], project_root
                )
                commits_behind = int(behind) if behind else 0
            else:
                remote_fetch_failed = True
        else:
            remote_fetch_failed = True

        return {
            "branch": branch or "unknown",
            "last_commit_hash": commit_hash or "",
            "last_commit_message": commit_message or "",
            "has_uncommitted_changes": bool(status),
            "remote_commit_hash": remote_commit_hash,
            "remote_commit_message": remote_commit_message,
            "commits_behind": commits_behind,
            "remote_fetch_failed": remote_fetch_failed,
        }

    @router.post("/api/system/restart", status_code=202)
    @handle_exceptions("restart server")
    async def restart_server():
        """Pull latest code and restart the backend server via os.execv."""
        # Rate limiting: 1 restart per 30 seconds
        now = time.time()
        if now - webui._last_restart_time < 30:
            remaining = int(30 - (now - webui._last_restart_time))
            raise HTTPException(
                status_code=429,
                detail=f"Rate limited. Try again in {remaining} seconds."
            )
        webui._last_restart_time = now

        project_root = Path(__file__).parent.parent.parent

        # Git pull current branch
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

        # Sync Python dependencies (after git pull, before restart)
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

        # Append restart notice to UI poll queue
        webui._broadcast_server_restarting(pull_output, sync_output)

        # Schedule the actual restart after response is sent
        async def _do_restart():
            await asyncio.sleep(0.5)
            logger.info("Executing os.execv restart...")
            try:
                await webui.coordinator.cleanup()
            except Exception as e:
                logger.warning(f"Cleanup error during restart: {e}")
            os.execv(sys.executable, [sys.executable] + sys.argv)

        asyncio.get_event_loop().create_task(_do_restart())

        return {
            "status": "restarting",
            "message": "Server is pulling latest code and restarting...",
            "pull_output": pull_output,
            "sync_output": sync_output,
        }

    return router
