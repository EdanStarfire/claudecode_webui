"""frontend_log_tee.py: raw combined capture of Frontend's own stdout/stderr.

Issue #2027 — home for Frontend-side bypass content (import-time output,
pre-config tracebacks) and supervisor lifecycle logging, mirroring what
backend_supervisor.py already captures for Backend into backend.log. Must be
installed before anything else writes to stdout/stderr, so it also captures
output from imports that happen afterward.

Deliberately an in-process OS-level fd tee, not a wrapper subprocess — see
issue #498's "exactly one code path to get right" principle: wrapping
Frontend itself would put an undocumented second supervisor in front of the
one process every PID-based kill/signal-cascade doc assumes is top-level.
"""

import atexit
import os
import sys
import threading
from pathlib import Path

from shared.raw_log_rotator import RotatingRawLogWriter

# Set by install_frontend_log_tee(); callers that are about to exec() a new
# process image (which skips atexit entirely) must call
# uninstall_frontend_log_tee() explicitly first — see its docstring.
_active_teardown = None


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def _tee_fd(fd: int, rotator: RotatingRawLogWriter) -> tuple[int, threading.Thread]:
    """Duplicate `fd` aside, dup2() a fresh pipe's write end onto `fd`, and
    start a daemon thread relaying the pipe's read end to both the saved
    original fd and `rotator`. Returns (saved_original_fd, pump_thread)."""
    saved_fd = os.dup(fd)
    read_fd, write_fd = os.pipe()
    os.dup2(write_fd, fd)
    os.close(write_fd)

    def _pump() -> None:
        while True:
            try:
                chunk = os.read(read_fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            _write_all(saved_fd, chunk)
            rotator.write(chunk)
        os.close(read_fd)

    thread = threading.Thread(target=_pump, name=f"frontend-log-tee-fd{fd}", daemon=True)
    thread.start()
    return saved_fd, thread


def install_frontend_log_tee(log_path: Path) -> None:
    """Tee fd 1 and fd 2 into both the real terminal and one combined,
    rotated log file.

    Two independent pipes (one per fd) each get their own pump thread, so a
    caller redirecting stdout and stderr separately on the real terminal
    keeps seeing exactly that separation — only the log file combines both
    into one raw capture, matching backend.log's semantics.

    Registers an atexit hook that restores the original fds and joins both
    pump threads on interpreter shutdown — writing to a pipe only lands in
    the kernel buffer, it doesn't wait for the reader, so without an
    explicit drain the last chunk written right before exit (e.g. an
    uncaught exception's own traceback, AC3's main target) could be dropped
    by a bare daemon thread racing process teardown.

    atexit never fires across an exec()-family syscall, though — a caller
    about to os.execv() (e.g. src/routers/system.py's restart route) must
    call uninstall_frontend_log_tee() explicitly first, or the new process
    image inherits fd 1/2 still pointing at this tee's now-readerless pipe
    and eventually hangs once its kernel buffer fills.
    """
    global _active_teardown

    rotator = RotatingRawLogWriter(log_path)
    saved_stdout_fd, stdout_thread = _tee_fd(1, rotator)
    saved_stderr_fd, stderr_thread = _tee_fd(2, rotator)
    drained = False

    def _drain() -> None:
        nonlocal drained
        if drained:
            return
        drained = True
        sys.stdout.flush()
        sys.stderr.flush()
        # Restoring the original fds closes fd 1/2's last references to
        # their respective pipes' write ends, so both pump threads' read()
        # calls see EOF and can finish draining before the interpreter (or,
        # via uninstall_frontend_log_tee(), an imminent exec()) proceeds.
        os.dup2(saved_stdout_fd, 1)
        os.dup2(saved_stderr_fd, 2)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        os.close(saved_stdout_fd)
        os.close(saved_stderr_fd)

    _active_teardown = _drain
    atexit.register(_drain)


def uninstall_frontend_log_tee() -> None:
    """Restore the original fds and drain both pump threads immediately.

    Call this before any exec()-family syscall replaces the current process
    image (os.execv, os.execve, ...) — atexit hooks registered by
    install_frontend_log_tee() never run in that case, since exec() doesn't
    return into the interpreter's normal shutdown sequence. No-op if the tee
    was never installed, or has already been drained.
    """
    if _active_teardown is not None:
        _active_teardown()
