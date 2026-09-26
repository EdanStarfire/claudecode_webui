"""raw_log_rotator.py: size-capped, backup-bounded rotation for raw byte streams.

The RotatingFileHandler equivalent for content that isn't Python LogRecords —
piped subprocess output (backend.log) or redirected stdout/stderr (frontend.log).
Issue #2027.
"""

import threading
from pathlib import Path

from shared.logging_config import LOG_BACKUP_COUNT, MAX_LOG_FILE_SIZE


class RotatingRawLogWriter:
    """Appends raw bytes to `path`, rolling over via rename when oversized.

    Rollover is a rename chain (path -> path.1 -> ... -> path.N, oldest
    dropped) — O(1) regardless of file size, and only ever performed between
    writes, never mid-write, so no output is lost across a rotation boundary.

    Thread-safe: frontend_log_tee.py feeds one instance from two separate
    pump threads (stdout and stderr), so writes and rollovers are serialized
    with an internal lock.
    """

    def __init__(
        self,
        path: Path,
        max_bytes: int = MAX_LOG_FILE_SIZE,
        backup_count: int = LOG_BACKUP_COUNT,
    ):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def rotate_if_oversized(self) -> None:
        """Roll over a pre-existing file that's already past the cap.

        Safe to call on a multi-GB file at startup (AC7) — the rollover is a
        rename, not a copy, so it doesn't stall startup.
        """
        with self._lock:
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                self._rollover()

    def write(self, data: bytes) -> None:
        with self._lock:
            with open(self.path, "ab") as f:
                f.write(data)
                size = f.tell()
            if size >= self.max_bytes:
                self._rollover()

    def _rollover(self) -> None:
        if self.backup_count <= 0:
            self.path.unlink(missing_ok=True)
            return

        for i in range(self.backup_count - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i}")
            dst = self.path.with_name(f"{self.path.name}.{i + 1}")
            if src.exists():
                dst.unlink(missing_ok=True)
                src.rename(dst)

        dst = self.path.with_name(f"{self.path.name}.1")
        dst.unlink(missing_ok=True)
        self.path.rename(dst)
