"""Tests for src/frontend_log_tee.py (issue #2027, AC3/T4).

install_frontend_log_tee() dup2()s fd 1/2 onto an internal pipe — that's an
OS-level fd operation that capsys/caplog can't exercise (they intercept above
the fd layer) and that would corrupt the test runner's own stdout if done
in-process. A real subprocess is the only reliable way to test it.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_CHILD_SCRIPT = """
import sys
from pathlib import Path

from src.frontend_log_tee import install_frontend_log_tee

install_frontend_log_tee(Path(sys.argv[1]))

print("stdout line from child")
print("stderr line from child", file=sys.stderr)
sys.stdout.flush()
sys.stderr.flush()

raise RuntimeError("boom")
"""


def _run_child(log_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, str(log_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_tee_captures_stdout_stderr_and_crash_traceback_in_both_places(tmp_path):
    log_path = tmp_path / "frontend.log"

    result = _run_child(log_path)

    # The subprocess's own captured output must be unaffected by the tee —
    # the interactive terminal (here, the parent's capture) keeps seeing
    # everything exactly as it would without the tee installed.
    assert "stdout line from child" in result.stdout
    assert "stderr line from child" in result.stderr
    assert "RuntimeError: boom" in result.stderr
    assert result.returncode != 0

    # And the same content must have landed in the rotated log file (AC3:
    # bypass output, including a crash traceback, still lands in a file).
    log_content = log_path.read_text()
    assert "stdout line from child" in log_content
    assert "stderr line from child" in log_content
    assert "RuntimeError: boom" in log_content


def test_tee_preserves_interleaved_stdout_and_stderr_ordering(tmp_path):
    log_path = tmp_path / "frontend.log"
    script = """
import sys
from pathlib import Path

from src.frontend_log_tee import install_frontend_log_tee

install_frontend_log_tee(Path(sys.argv[1]))

for i in range(5):
    print(f"out-{i}")
    print(f"err-{i}", file=sys.stderr)
sys.stdout.flush()
sys.stderr.flush()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(log_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0
    log_content = log_path.read_text()
    for i in range(5):
        assert f"out-{i}" in log_content
        assert f"err-{i}" in log_content


_EXECV_RESTART_SCRIPT = """
import os
import sys
from pathlib import Path

from src.frontend_log_tee import install_frontend_log_tee, uninstall_frontend_log_tee

log_path = Path(sys.argv[1])
phase = sys.argv[2]

install_frontend_log_tee(log_path)
print(f"phase {phase} stdout")
sys.stdout.flush()

if phase == "1":
    # Must call uninstall_frontend_log_tee() before os.execv() — atexit
    # hooks never fire across exec(), so without this the new process image
    # would inherit fd 1/2 still pointing at this (phase 1) tee's pipe,
    # which nothing drains once phase 1's process image is gone.
    uninstall_frontend_log_tee()
    os.execv(sys.executable, [sys.executable, __file__, str(log_path), "2"])
"""


def test_tee_survives_execv_restart_when_explicitly_uninstalled(tmp_path):
    """Regression test: install_frontend_log_tee()'s cleanup is atexit-based,
    which never runs across os.execv (src/routers/system.py's restart route
    uses exactly this call). Without an explicit uninstall_frontend_log_tee()
    call beforehand, the post-restart process's stdout would be silently
    dropped from the real terminal/capture (though still landing in the log
    file, since rotator.write() doesn't depend on which fd is being tee'd)."""
    log_path = tmp_path / "frontend.log"
    script_path = tmp_path / "execv_restart_script.py"
    script_path.write_text(_EXECV_RESTART_SCRIPT)

    result = subprocess.run(
        [sys.executable, str(script_path), str(log_path), "1"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0
    # Both phases' output must reach the real external capture — proving fd
    # 1 was correctly restored to its true target before phase 2's fresh tee
    # was installed, not left pointing at phase 1's now-readerless pipe.
    assert "phase 1 stdout" in result.stdout
    assert "phase 2 stdout" in result.stdout

    log_content = log_path.read_text()
    assert "phase 1 stdout" in log_content
    assert "phase 2 stdout" in log_content
