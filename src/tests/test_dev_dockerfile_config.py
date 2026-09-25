"""Static regression test for docker/Dockerfile.dev's venv isolation (issue #2003).

A dev-container `uv sync` run without UV_PROJECT_ENVIRONMENT set targets the
bind-mounted workspace's own $WORKSPACE/.venv — the host's real .venv when the
container mounts a host checkout. uv treats that venv's pyvenv.cfg (pointing
at a host-only interpreter) as invalid and silently deletes + recreates it,
breaking the host's already-running Backend out from under it. This parses
the Dockerfile as text (no Docker build/daemon required) so the regression
is caught on every `pytest` run, not only when someone remembers to rebuild
and manually test the image.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE_DEV = REPO_ROOT / "docker" / "Dockerfile.dev"

# Path patterns the $WORKSPACE bind mount (-v "$WORKSPACE:$WORKSPACE") could
# plausibly resolve under — a UV_PROJECT_ENVIRONMENT value must avoid these.
_WORKSPACE_RELATIVE_PATTERNS = ("$WORKSPACE", "${WORKSPACE}", "/workspace")


def _dockerfile_text() -> str:
    return DOCKERFILE_DEV.read_text()


def test_uv_project_environment_is_set():
    text = _dockerfile_text()
    match = re.search(r"^ENV UV_PROJECT_ENVIRONMENT=(\S+)\s*$", text, re.MULTILINE)
    assert match, "docker/Dockerfile.dev must set ENV UV_PROJECT_ENVIRONMENT=<path>"


def test_uv_project_environment_is_absolute_and_container_local():
    text = _dockerfile_text()
    match = re.search(r"^ENV UV_PROJECT_ENVIRONMENT=(\S+)\s*$", text, re.MULTILINE)
    assert match
    value = match.group(1)

    assert value, "UV_PROJECT_ENVIRONMENT value must be non-empty"
    assert value.startswith("/"), f"UV_PROJECT_ENVIRONMENT must be an absolute path, got {value!r}"

    for pattern in _WORKSPACE_RELATIVE_PATTERNS:
        assert pattern.lower() not in value.lower(), (
            f"UV_PROJECT_ENVIRONMENT={value!r} resolves under the workspace bind mount "
            f"(matched {pattern!r}); it must be a container-local path the "
            f'-v "$WORKSPACE:$WORKSPACE" mount never touches'
        )


def test_uv_project_environment_set_before_prewarm_sync():
    text = _dockerfile_text()
    env_match = re.search(r"^ENV UV_PROJECT_ENVIRONMENT=\S+\s*$", text, re.MULTILINE)
    prewarm_match = re.search(r"^RUN cd /tmp/prewarm.*$", text, re.MULTILINE)

    assert env_match, "ENV UV_PROJECT_ENVIRONMENT line not found"
    assert prewarm_match, "pre-warm `RUN cd /tmp/prewarm ...` line not found"
    assert env_match.start() < prewarm_match.start(), (
        "ENV UV_PROJECT_ENVIRONMENT must appear before the pre-warm `uv sync` RUN "
        "so the baked venv lands at the container-local path, not $WORKSPACE/.venv"
    )


def test_prewarm_installs_python_314_not_313():
    text = _dockerfile_text()
    prewarm_block_match = re.search(
        r"^RUN cd /tmp/prewarm.*?(?=^# ----------|\Z)", text, re.MULTILINE | re.DOTALL
    )
    assert prewarm_block_match, "pre-warm RUN block not found"
    prewarm_block = prewarm_block_match.group(0)

    assert "uv python install 3.14" in prewarm_block, (
        "pre-warm layer must install Python 3.14 for parity with the host"
    )
    assert "uv python install 3.13" not in prewarm_block, (
        "pre-warm layer still installs Python 3.13 — bump to 3.14"
    )
