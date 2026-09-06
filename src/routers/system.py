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
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.exception_handlers import handle_exceptions
from shared.git_restart import (
    get_git_branches_info,
    get_git_commits_info,
    get_git_status_info,
    run_git_command,
    validate_git_ref_component,
)

logger = logging.getLogger(__name__)

# Per-phase timeout for polling a remote Backend back to healthy, then ready, after
# triggering its restart. Each phase gets its OWN full budget below (not one shared
# deadline) — mirrors BackendSupervisor.wait_ready()'s own two-phase-deadline fix in
# src/backend_supervisor.py: a shared deadline can starve the second phase for a
# Backend slow to pass the first. Matches the 60s ceiling RestartModal.vue already
# uses for its own post-restart health poll, for consistency.
_BACKEND_RESTART_TIMEOUT = 60.0
_BACKEND_RESTART_POLL_INTERVAL = 0.5
# Grace period before the first poll. Backend's own restart re-execs ~0.5s after
# responding (backend/routers/system.py's _do_restart), so polling immediately risks
# observing the still-alive pre-restart process and declaring success without ever
# having seen it go down and come back.
_BACKEND_RESTART_GRACE_SECONDS = 1.0
# Margin above Backend's own worst-case synchronous restart-trigger work (git
# checkout/fetch/reset up to ~75s, then uv sync up to 120s — both run before Backend
# even responds). The client-side timeout for triggering the restart must have
# headroom above that budget, not equal or below it — same discipline as
# BackendClient.get_json()'s own docstring (issue #498 review finding).
_BACKEND_RESTART_TRIGGER_TIMEOUT = 210.0


class RestartRequest(BaseModel):
    branch: str | None = None       # Frontend's own target (unchanged meaning)
    commit: str | None = None       # Frontend's own target (unchanged meaning)
    backend_branch: str | None = None   # remote mode only — Backend's target
    backend_commit: str | None = None   # remote mode only — Backend's target


@dataclass
class BackendRestartOutcome:
    status: str  # "restarted" | "failed" | "unreachable"
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "restarted"


async def _restart_remote_backend(webui, payload: "RestartRequest") -> BackendRestartOutcome:
    """Orchestrate a remote Backend's own restart before Frontend restarts itself.

    Only called in remote mode (webui.backend_supervisor is None). Backend's own
    POST /api/system/restart (backend/routers/system.py) already fully implements
    this exact {branch, commit} target contract, already validates/fetches/
    checks-out/resets against its own repo, and already waits, re-execs, and comes
    back up on its own — this is orchestration glue, not new restart machinery.

    Note: Backend's own git checkout/reset runs synchronously, before it even
    responds to the trigger request below — so once Backend accepts the request at
    all, its repo has already changed regardless of what happens next. A "failed"/
    "unreachable" outcome from this function only guarantees Frontend hasn't touched
    its own state; it does not mean Backend's is unchanged too.
    """
    if not await webui.backend_client.health():
        return BackendRestartOutcome(
            status="unreachable",
            detail="Backend was already unreachable before this restart was attempted.",
        )

    backend_payload = {"branch": payload.backend_branch, "commit": payload.backend_commit}
    try:
        await webui.backend_client.request_json(
            "POST", "/api/system/restart", json=backend_payload,
            timeout=_BACKEND_RESTART_TRIGGER_TIMEOUT,
        )
    except httpx.HTTPStatusError as e:
        detail = e.response.text
        try:
            detail = e.response.json().get("detail", detail)
        except (ValueError, AttributeError, TypeError):
            pass
        return BackendRestartOutcome(status="failed", detail=detail)
    except httpx.RequestError as e:
        return BackendRestartOutcome(
            status="unreachable",
            detail=f"Backend became unreachable while triggering its restart: {e}",
        )

    # Backend returned 202 and is now restarting asynchronously (fire-and-forget,
    # same pattern as Frontend's own _finish_restart). Give it a moment to actually
    # start tearing down before polling (see _BACKEND_RESTART_GRACE_SECONDS), then
    # poll it back to healthy, then ready. health()/ready() route connection failures
    # through BackendReachabilityTracker automatically, so repeated failures during
    # the expected outage window collapse into the existing suppressed-warning
    # behavior (#1844) instead of spamming error.log.
    await asyncio.sleep(_BACKEND_RESTART_GRACE_SECONDS)

    async def _poll_until(check) -> bool:
        deadline = time.monotonic() + _BACKEND_RESTART_TIMEOUT
        while time.monotonic() < deadline:
            if await check():
                return True
            await asyncio.sleep(_BACKEND_RESTART_POLL_INTERVAL)
        return False

    if not await _poll_until(webui.backend_client.health):
        return BackendRestartOutcome(
            status="failed",
            detail=f"Backend did not become healthy within {int(_BACKEND_RESTART_TIMEOUT)}s of restarting.",
        )

    if not await _poll_until(webui.backend_client.ready):
        return BackendRestartOutcome(
            status="failed",
            detail=(
                f"Backend became healthy but did not report ready within the "
                f"{int(_BACKEND_RESTART_TIMEOUT)}s restart window."
            ),
        )

    return BackendRestartOutcome(status="restarted", detail="Backend restarted and is ready.")


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

    @router.get("/api/system/frontend-git-status")
    @handle_exceptions("get frontend git status")
    async def get_frontend_git_status():
        """Return Frontend's own repo status (issue #1847 — remote-mode drift display).

        Distinct from the relayed /api/system/git-status (always Backend's repo,
        which in embedded mode happens to be the same checkout). This route describes
        Frontend's own checkout, which only differs from Backend's in remote mode.
        """
        project_root = str(Path(__file__).parent.parent.parent)
        return await get_git_status_info(project_root)

    @router.get("/api/system/frontend-git-branches")
    @handle_exceptions("get frontend git branches")
    async def get_frontend_git_branches():
        """Return Frontend's own local + origin branches (issue #1847)."""
        project_root = str(Path(__file__).parent.parent.parent)
        return await get_git_branches_info(project_root)

    @router.get("/api/system/frontend-git-commits")
    @handle_exceptions("get frontend git commits")
    async def get_frontend_git_commits(branch: str):
        """Return up to 50 one-line commit summaries for a branch in Frontend's own repo (issue #1847)."""
        project_root = str(Path(__file__).parent.parent.parent)
        return await get_git_commits_info(project_root, branch)

    @router.get("/api/system/frontend-mode")
    @handle_exceptions("get frontend mode")
    async def get_frontend_mode():
        """Return whether Frontend is running in remote-Backend mode (issue #1847).

        Authoritative signal for RestartModal.vue's dual-tier UI — reflects the actual live
        wiring (webui.backend_supervisor is None means Backend was pointed to via
        --remote-backend-url/--remote-backend-token rather than auto-started), not
        config.json's persisted backend_connection.remote_backend_url alone, which misses
        deployments where those were passed as CLI-only flags and never written to disk
        (review finding: main.py's `args.remote_backend_url or frontend_config...` falls
        through to the CLI value without ever persisting it back).
        """
        return {"remote_mode": webui.backend_supervisor is None}

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

        Remote mode (webui.backend_supervisor is None): Backend is restarted
        first via _restart_remote_backend() — defaulting to "pull latest" on
        Backend too when backend_branch/backend_commit are both absent, or an
        explicit independent target otherwise. Frontend's own restart only
        proceeds once Backend reports healthy+ready again; a Backend-side
        failure reported by that step (already unreachable, rejected target,
        never came back healthy+ready) aborts before Frontend touches its own
        git state or restarts. Note this guarantees Frontend is untouched on
        such a failure, not that Backend is too: Backend's own git checkout/
        reset runs synchronously before it even responds, so a request Backend
        *accepts* has already changed Backend's repo regardless of what
        happens afterward — full two-tier atomicity (e.g. if Frontend's own
        subsequent git operations then fail) is out of scope for this stage.
        """
        now = time.time()
        if now - webui._last_restart_time < 30:
            remaining = int(30 - (now - webui._last_restart_time))
            raise HTTPException(
                status_code=429,
                detail=f"Rate limited. Try again in {remaining} seconds."
            )
        webui._last_restart_time = now

        payload = payload or RestartRequest()
        project_root = Path(__file__).parent.parent.parent

        # Validate Frontend's own target up front, before Backend is touched at all —
        # a malformed/malicious ref here must not trigger Backend's restart only to
        # then have Frontend itself reject the request (also re-checked inside
        # _restart_to_target(), which remains the authoritative check for its own
        # direct callers/tests).
        for value, field_name in ((payload.branch, "branch"), (payload.commit, "commit")):
            if value is not None:
                validate_git_ref_component(value, field_name)

        has_custom_target = bool(payload.branch or payload.commit)

        backend_outcome = None
        if webui.backend_supervisor is None:
            outcome = await _restart_remote_backend(webui, payload)
            backend_outcome = {"status": outcome.status, "detail": outcome.detail}
            logger.info(
                "Restart requested (frontend: branch=%s commit=%s | backend: branch=%s "
                "commit=%s) — backend outcome: %s",
                payload.branch, payload.commit, payload.backend_branch, payload.backend_commit,
                backend_outcome,
            )
            if not outcome.ok:
                raise HTTPException(
                    status_code=502,
                    detail=f"Backend restart failed ({outcome.status}): {outcome.detail}",
                )
        else:
            logger.info(
                "Restart requested (frontend: branch=%s commit=%s)",
                payload.branch, payload.commit,
            )

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
            "backend": backend_outcome,
        }

    return router
