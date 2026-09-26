"""backend_supervisor.py: spawn/monitor/restart/shutdown the local Backend
subprocess (issue #498, Phase 3).

Used by main.py's lifespan when no --remote-backend-url is given: auto-starts
Backend bound to 127.0.0.1 with an OS-assigned free port and a freshly
generated backend-scoped token — the single-user self-hosted case needs zero
manual configuration for this to work. Passes --embedded so backend/main.py
also binds Docker's default bridge gateway (if reachable on this host) for
Docker sidecar reachability on native Docker Engine hosts (issue #1850) —
see backend/docker_utils.py's build_embedded_sockets() for how that bind set
is constructed.
"""

import asyncio
import logging
import secrets
import signal
import sys
import time
from pathlib import Path

from shared.net_utils import allocate_free_port
from shared.raw_log_rotator import RotatingRawLogWriter

logger = logging.getLogger(__name__)

_MAX_RESTART_ATTEMPTS = 5
_RESTART_WINDOW_SECONDS = 300  # rolling window for counting restart attempts
_READINESS_POLL_INTERVAL = 0.5
_READINESS_TIMEOUT = 30.0
_SHUTDOWN_TIMEOUT = 10.0


def _allocate_free_port() -> int:
    """Delegates to shared.net_utils.allocate_free_port() — kept as a local
    name so existing tests that patch src.backend_supervisor._allocate_free_port
    keep working."""
    return allocate_free_port()


class BackendSupervisor:
    """Owns the lifecycle of an auto-started local Backend subprocess."""

    def __init__(
        self,
        data_dir: Path,
        experimental: bool = False,
        mock_sdk: bool = False,
        fixtures_dir: Path | None = None,
        extra_backend_args: list[str] | None = None,
        log_dir: Path | None = None,
        session_recording_enabled: bool = False,
    ):
        self.data_dir = data_dir
        self.experimental = experimental
        self.mock_sdk = mock_sdk
        self.fixtures_dir = fixtures_dir
        # Issue #1998: forwarded verbatim into the auto-started Backend's argv.
        self.session_recording_enabled = session_recording_enabled
        self.extra_backend_args = extra_backend_args or []
        self.log_dir = Path(log_dir) if log_dir else (data_dir / "logs" / "backend")

        self.host = "127.0.0.1"
        self.port = _allocate_free_port()
        # Never the browser's own token — a separate, backend-scoped credential
        # generated fresh at Frontend startup (issue #827/#1427 auth-boundary pattern).
        self.token = secrets.token_urlsafe(32)
        self.base_url = f"http://{self.host}:{self.port}"

        self._process: asyncio.subprocess.Process | None = None
        self._rotator: RotatingRawLogWriter | None = None
        self._pump_tasks: list[asyncio.Task] = []
        self._restart_timestamps: list[float] = []
        self._degraded = False
        self._monitor_task: asyncio.Task | None = None
        self._stopping = False

    @property
    def degraded(self) -> bool:
        return self._degraded

    def _build_command(self) -> list[str]:
        # Spawn via sys.executable directly, not "uv run python ..." — uv run's
        # wrapper process doesn't propagate SIGTERM to the actual grandchild,
        # which leaves an orphaned Backend process behind on shutdown (found
        # live while testing Phase 2's two-process CLI entrypoint smoke test).
        cmd = [
            sys.executable, "-m", "backend.main",
            "--host", self.host,
            "--port", str(self.port),
            "--token", self.token,
            "--data-dir", str(self.data_dir),
            # Tells backend/main.py it was auto-started by this supervisor, not run
            # manually/remotely — see backend/main.py's --embedded help text (#1850).
            "--embedded",
        ]
        if self.experimental:
            cmd.append("--experimental")
        if self.session_recording_enabled:
            cmd.append("--enable-session-recording")
        if self.mock_sdk:
            cmd.append("--mock-sdk")
            if self.fixtures_dir:
                cmd.extend(["--fixtures-dir", str(self.fixtures_dir)])
        cmd.extend(self.extra_backend_args)
        return cmd

    async def _spawn(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self._build_command(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        logger.info("Spawned Backend subprocess (pid=%s) on %s", self._process.pid, self.base_url)
        self._pump_tasks.append(
            asyncio.create_task(self._pump_log(self._process), name="backend_supervisor_log_pump")
        )

    async def _pump_log(self, process: asyncio.subprocess.Process) -> None:
        """Read the subprocess's piped stdout/stderr and feed it to the rotator.

        Rotation is purely a parent-side file-handle swap (AC2) — the pipe read
        loop doesn't care whether backend.log just rolled over. The write is
        offloaded to a thread since it's blocking file I/O and this coroutine
        runs on Frontend's single shared event loop. If a write ever fails
        (e.g. disk full), this keeps draining the pipe without persisting
        further — the alternative (giving up on the loop entirely) would let
        the pipe's kernel buffer fill and block Backend's own stdout writes.
        """
        assert process.stdout is not None
        assert self._rotator is not None
        write_failed = False
        while True:
            chunk = await process.stdout.read(65536)
            if not chunk:
                break
            if write_failed:
                continue
            try:
                await asyncio.to_thread(self._rotator.write, chunk)
            except OSError:
                write_failed = True
                logger.exception(
                    "backend.log write failed; continuing to drain Backend's "
                    "output without persisting it"
                )

    async def start(self) -> None:
        """Spawn the Backend subprocess and start the crash-monitor loop."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._rotator = RotatingRawLogWriter(self.log_dir / "backend.log")
        # AC7: a pre-existing oversized backend.log gets rolled over via an O(1)
        # rename before the first spawn, so a multi-GB leftover file never stalls
        # startup.
        self._rotator.rotate_if_oversized()
        await self._spawn()
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="backend_supervisor_monitor"
        )

    async def wait_ready(self, backend_client) -> bool:
        """Poll /health then /ready with backoff. Returns False on timeout.

        Liveness and readiness each get their own full _READINESS_TIMEOUT budget
        (worst case ~2x _READINESS_TIMEOUT total) rather than sharing one deadline
        — a shared deadline meant a Backend slow to pass /health (e.g. disk-bound
        startup work) left little or no time for the /ready phase, causing a
        spurious failure on a Backend that would have become ready given the
        budget its own log message and timeout name imply (issue #498 review finding).
        """
        liveness_deadline = time.monotonic() + _READINESS_TIMEOUT
        while time.monotonic() < liveness_deadline:
            if await backend_client.health():
                break
            await asyncio.sleep(_READINESS_POLL_INTERVAL)
        else:
            logger.error("Backend never became live (liveness) within %ss", _READINESS_TIMEOUT)
            return False

        readiness_deadline = time.monotonic() + _READINESS_TIMEOUT
        while time.monotonic() < readiness_deadline:
            if await backend_client.ready():
                logger.info("Backend reported ready")
                return True
            await asyncio.sleep(_READINESS_POLL_INTERVAL)

        logger.error("Backend never became ready within %ss", _READINESS_TIMEOUT)
        return False

    async def _monitor_loop(self) -> None:
        """Watch the subprocess; restart with exponential backoff on unexpected exit."""
        while not self._stopping:
            assert self._process is not None
            returncode = await self._process.wait()
            if self._stopping:
                return

            # AC5: negative returncode means killed by signal -returncode
            # (asyncio subprocess convention). AC6: this is genuinely error-level —
            # bumped from warning so it reaches error.log.
            signal_suffix = ""
            if returncode < 0:
                try:
                    signal_suffix = f" signal={signal.Signals(-returncode).name}"
                except ValueError:
                    # Signal number not in Python's Signals enum (e.g. an
                    # unmapped realtime signal) — still log the raw code.
                    pass
            logger.error(
                "Backend subprocess exited unexpectedly (code=%s%s)", returncode, signal_suffix
            )
            now = time.monotonic()
            self._restart_timestamps = [
                t for t in self._restart_timestamps if now - t < _RESTART_WINDOW_SECONDS
            ]
            self._restart_timestamps.append(now)

            if len(self._restart_timestamps) > _MAX_RESTART_ATTEMPTS:
                self._degraded = True
                logger.error(
                    "Backend crashed %d times within %ds — giving up, marking degraded",
                    len(self._restart_timestamps), _RESTART_WINDOW_SECONDS,
                )
                return

            backoff = min(2 ** (len(self._restart_timestamps) - 1), 30)
            logger.info(
                "Restarting Backend in %ds (attempt %d/%d)",
                backoff, len(self._restart_timestamps), _MAX_RESTART_ATTEMPTS,
            )
            await asyncio.sleep(backoff)
            if self._stopping:
                return
            await self._spawn()

    async def stop(self) -> None:
        """SIGTERM, wait with timeout, then SIGKILL. Cancels the monitor task first
        so a crash mid-shutdown doesn't race with the restart logic."""
        self._stopping = True
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            except Exception:
                # A crash inside the monitor loop (e.g. it observed an exit it
                # couldn't fully process) must not abort the rest of shutdown —
                # the Backend subprocess still needs to be terminated below.
                logger.exception("Monitor task raised during shutdown")

        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=_SHUTDOWN_TIMEOUT)
            except TimeoutError:
                logger.warning("Backend subprocess did not terminate in time, killing")
                self._process.kill()
                await self._process.wait()

        # The process exiting closes its end of the stdout pipe, so each pump
        # task's read() loop should already be at (or very near) EOF here.
        for task in self._pump_tasks:
            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=_SHUTDOWN_TIMEOUT)
                except (TimeoutError, asyncio.CancelledError):
                    logger.warning(
                        "Log pump task did not drain in time; any buffered "
                        "output not yet written may be lost"
                    )
                    task.cancel()

        logger.info("Backend subprocess stopped")
