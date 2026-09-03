"""Cross-process advisory locking for the shared ~/.config/cc_webui/config.json
(issue #498 review finding).

Frontend and Backend each own disjoint top-level sections of this one file but
both do read-merge-write on save. Without a lock, two saves racing across the
two processes can interleave: A reads, B reads (same snapshot), A writes its
merged copy, B writes its own merged copy — B's write silently drops A's update
because B merged from a snapshot taken before A's write landed. This can happen
any time a Frontend config PUT lands close to a Backend pricing/config update.
"""

import contextlib
import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None


@contextlib.contextmanager
def locked_config_write(config_file: Path):
    """Hold an exclusive advisory lock for the duration of a read-modify-write.

    Best-effort on platforms without fcntl (e.g. Windows): the write still
    happens, just without cross-process mutual exclusion.
    """
    config_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = config_file.with_suffix(config_file.suffix + ".lock")
    with open(lock_path, "w") as lock_fd:
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str) -> None:
    """Write text to path atomically via temp-file + os.replace.

    Prevents a concurrent reader from ever observing a partially-written file.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)
