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


async def get_git_status_info(project_root: str) -> dict:
    """Return current git branch, last commit, remote commit info, and dirty state.

    Parameterized by project_root (issue #1847) so both tiers' own repo checkouts can
    be inspected with one implementation — mirrors backend/routers/system.py's
    get_git_status() body exactly (that copy is left in place unchanged; deduplicating
    it into a call to this function is optional follow-up, not required for #1847-ui).
    """
    branch = await run_git_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], project_root)
    commit_hash = await run_git_command(["git", "log", "-1", "--format=%H"], project_root)
    commit_message = await run_git_command(["git", "log", "-1", "--format=%s"], project_root)
    status = await run_git_command(["git", "status", "--porcelain"], project_root)

    remote_commit_hash = ""
    remote_commit_message = ""
    commits_behind = 0
    remote_fetch_failed = False

    remote_branch = None
    if branch and branch != "HEAD":
        candidate = f"origin/{branch}"
        ref_exists = await run_git_command(["git", "rev-parse", "--verify", candidate], project_root)
        if ref_exists:
            remote_branch = candidate

    if not remote_branch:
        origin_head = await run_git_command(
            ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"], project_root
        )
        if origin_head:
            remote_branch = origin_head
        else:
            for fallback in ["origin/main", "origin/master"]:
                ref_check = await run_git_command(["git", "rev-parse", "--verify", fallback], project_root)
                if ref_check:
                    remote_branch = fallback
                    break

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

        r_hash = await run_git_command(["git", "log", "-1", "--format=%H", remote_branch], project_root)
        r_msg = await run_git_command(["git", "log", "-1", "--format=%s", remote_branch], project_root)
        if r_hash:
            remote_commit_hash = r_hash
            remote_commit_message = r_msg or ""
            behind = await run_git_command(
                ["git", "rev-list", "--count", f"HEAD..{remote_branch}"], project_root
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


async def get_git_branches_info(project_root: str) -> dict:
    """Return local + origin branches for the given repo (issue #1760, parameterized #1847)."""
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

    current_branch = await run_git_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], project_root)

    local_output = await run_git_command(
        ["git", "for-each-ref", "refs/heads", "--format=%(refname:short)"], project_root
    )
    local_branches = [line for line in (local_output or "").splitlines() if line]

    remote_output = await run_git_command(
        ["git", "for-each-ref", "refs/remotes/origin", "--format=%(refname:short)"], project_root
    )
    remote_branches = [
        line for line in (remote_output or "").splitlines() if line and line != "origin/HEAD"
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


async def get_git_commits_info(project_root: str, branch: str) -> dict:
    """Return up to 50 one-line commit summaries for a branch (issue #1760, parameterized #1847)."""
    if not branch:
        raise HTTPException(status_code=400, detail="branch is required")
    validate_git_ref_component(branch, "branch")

    ref = None
    local_ref_check = await run_git_command(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], project_root
    )
    if local_ref_check:
        ref = f"refs/heads/{branch}"
    else:
        remote_ref_check = await run_git_command(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], project_root
        )
        if remote_ref_check:
            ref = f"refs/remotes/origin/{branch}"

    if not ref:
        raise HTTPException(status_code=404, detail=f"Branch not found: {branch}")

    log_output = await run_git_command(
        ["git", "log", ref, "-n", "51", "--format=%H%x1f%h%x1f%s%x1f%an%x1f%ad", "--date=iso-strict"],
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
