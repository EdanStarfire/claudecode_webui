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
import socket
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_RESTART_ATTEMPTS = 5
_RESTART_WINDOW_SECONDS = 300  # rolling window for counting restart attempts
_READINESS_POLL_INTERVAL = 0.5
_READINESS_TIMEOUT = 30.0
_SHUTDOWN_TIMEOUT = 10.0


def _allocate_free_port() -> int:
    """Bind a throwaway socket on 127.0.0.1:0 to obtain a free OS-assigned port.

    Avoids a fixed offset, which collides when multiple frontend/backend pairs
    run on one host (issue #1825).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
    ):
        self.data_dir = data_dir
        self.experimental = experimental
        self.mock_sdk = mock_sdk
        self.fixtures_dir = fixtures_dir
        self.extra_backend_args = extra_backend_args or []
        self.log_dir = Path(log_dir) if log_dir else (data_dir / "logs" / "backend")

        self.host = "127.0.0.1"
        self.port = _allocate_free_port()
        # Never the browser's own token — a separate, backend-scoped credential
        # generated fresh at Frontend startup (issue #827/#1427 auth-boundary pattern).
        self.token = secrets.token_urlsafe(32)
        self.base_url = f"http://{self.host}:{self.port}"

        self._process: asyncio.subprocess.Process | None = None
        self._log_file = None
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
        if self.mock_sdk:
            cmd.append("--mock-sdk")
            if self.fixtures_dir:
                cmd.extend(["--fixtures-dir", str(self.fixtures_dir)])
        cmd.extend(self.extra_backend_args)
        return cmd

    async def _spawn(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self._build_command(),
            stdout=self._log_file,
            stderr=asyncio.subprocess.STDOUT,
        )
        logger.info("Spawned Backend subprocess (pid=%s) on %s", self._process.pid, self.base_url)

    async def start(self) -> None:
        """Spawn the Backend subprocess and start the crash-monitor loop."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.log_dir / "backend.log", "ab")
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

            logger.warning("Backend subprocess exited unexpectedly (code=%s)", returncode)
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

        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=_SHUTDOWN_TIMEOUT)
            except TimeoutError:
                logger.warning("Backend subprocess did not terminate in time, killing")
                self._process.kill()
                await self._process.wait()

        if self._log_file is not None:
            self._log_file.close()
        logger.info("Backend subprocess stopped")
