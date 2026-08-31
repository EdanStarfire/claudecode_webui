"""System endpoints: /api/system/*"""

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..exception_handlers import handle_exceptions

logger = logging.getLogger(__name__)


class RestartRequest(BaseModel):
    branch: str | None = None
    commit: str | None = None


def _validate_git_ref_component(value: str, field_name: str) -> None:
    """Reject values git would interpret as a CLI flag rather than a ref (issue #1760)."""
    if not value or value.startswith("-"):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {value!r}")


async def _restart_to_target(webui, project_root: Path, payload: "RestartRequest") -> str:
    """Switch the repo to a specific branch/commit before restart (issue #1760).

    Only called when the client explicitly requested a non-default branch/commit;
    the no-body request path never reaches this function.
    """
    for value, field_name in ((payload.branch, "branch"), (payload.commit, "commit")):
        if value is not None:
            _validate_git_ref_component(value, field_name)

    project_root_str = str(project_root)

    # Defense in depth: the frontend also blocks this, but the server must not trust the client.
    # _run_git_command returns None on any command failure (not just "clean") — treat that as
    # unsafe-to-proceed rather than silently equating it with a clean tree.
    status = await webui._run_git_command(["git", "status", "--porcelain"], project_root_str)
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

    current_branch = await webui._run_git_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], project_root_str
    )
    branch = payload.branch or current_branch
    if not branch:
        raise HTTPException(status_code=500, detail="Could not resolve current branch")

    local_ref_check = await webui._run_git_command(
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
    pre_reset_status = await webui._run_git_command(
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

    short_hash = await webui._run_git_command(
        ["git", "rev-parse", "--short", "HEAD"], project_root_str
    )
    return f"Switched to {branch} @ {short_hash or reset_target}"


def build_router(webui) -> APIRouter:
    router = APIRouter()

    @router.get("/api/system/secrets-backend-status")
    @handle_exceptions("get secrets backend status")
    async def get_secrets_backend_status():
        """Return active keyring backend name and any warning message (issue #827)."""
        from src.secrets_keyring import get_backend_status
        return get_backend_status()

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

    @router.get("/api/system/git-branches")
    @handle_exceptions("get git branches")
    async def get_git_branches():
        """Return local + origin branches for the WebUI's own repo (issue #1760)."""
        project_root = str(Path(__file__).parent.parent.parent)

        remote_fetch_failed = False
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

        current_branch = await webui._run_git_command(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], project_root
        )

        local_output = await webui._run_git_command(
            ["git", "for-each-ref", "refs/heads", "--format=%(refname:short)"], project_root
        )
        local_branches = [line for line in (local_output or "").splitlines() if line]

        remote_output = await webui._run_git_command(
            ["git", "for-each-ref", "refs/remotes/origin", "--format=%(refname:short)"],
            project_root,
        )
        remote_branches = [
            line for line in (remote_output or "").splitlines()
            if line and line != "origin/HEAD"
        ]

        branches = {}
        for name in local_branches:
            branches[name] = {
                "name": name,
                "is_current": name == current_branch,
                "is_local": True,
                "is_remote_only": False,
            }
        for full_name in remote_branches:
            name = full_name.split("/", 1)[1] if "/" in full_name else full_name
            if name in branches:
                branches[name]["is_remote_only"] = False
            else:
                branches[name] = {
                    "name": name,
                    "is_current": name == current_branch,
                    "is_local": False,
                    "is_remote_only": True,
                }

        return {
            "branches": sorted(branches.values(), key=lambda b: b["name"]),
            "remote_fetch_failed": remote_fetch_failed,
        }

    @router.get("/api/system/git-commits")
    @handle_exceptions("get git commits")
    async def get_git_commits(branch: str):
        """Return up to 50 one-line commit summaries for a branch (issue #1760)."""
        if not branch:
            raise HTTPException(status_code=400, detail="branch is required")
        _validate_git_ref_component(branch, "branch")

        project_root = str(Path(__file__).parent.parent.parent)

        ref = None
        local_ref_check = await webui._run_git_command(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], project_root
        )
        if local_ref_check:
            ref = f"refs/heads/{branch}"
        else:
            remote_ref_check = await webui._run_git_command(
                ["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
                project_root,
            )
            if remote_ref_check:
                ref = f"refs/remotes/origin/{branch}"

        if not ref:
            raise HTTPException(status_code=404, detail=f"Branch not found: {branch}")

        # Fetch one extra row so we can tell "exactly 50 commits" apart from "more than 50 exist".
        log_output = await webui._run_git_command(
            ["git", "log", ref, "-n", "51", "--format=%H%x1f%h%x1f%s%x1f%an%x1f%ad",
             "--date=iso-strict"],
            project_root,
        )
        commits = []
        for line in (log_output or "").splitlines():
            if not line:
                continue
            parts = line.split("\x1f")
            if len(parts) != 5:
                continue
            commit_hash, short_hash, subject, author, date = parts
            commits.append({
                "hash": commit_hash,
                "short_hash": short_hash,
                "subject": subject,
                "author": author,
                "date": date,
            })

        truncated = len(commits) > 50
        commits = commits[:50]

        return {
            "branch": branch,
            "commits": commits,
            "truncated": truncated,
        }

    @router.post("/api/system/restart", status_code=202)
    @handle_exceptions("restart server")
    async def restart_server(payload: RestartRequest | None = None):
        """Pull latest code (default) or switch to a specific branch/commit, then restart via os.execv."""
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
        has_custom_target = payload is not None and (payload.branch or payload.commit)

        if not has_custom_target:
            # Default path (issue #1760): unchanged from pre-existing behavior — git pull current branch
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
            pull_output = await _restart_to_target(webui, project_root, payload)

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
