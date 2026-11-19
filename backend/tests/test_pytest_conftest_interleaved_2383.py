"""Regression guard for #2383: pytest 9.1.0-9.1.1 lose nested conftest fixtures.

With those versions, file arguments from a nested directory separated by a file from
its parent directory left the last nested file without the nested conftest's fixtures.
Fixed upstream in pytest 9.2.0; the dev dependency floor excludes the broken versions.
These tests run pytest in a subprocess, so they fail if an affected pytest is installed.
"""

import itertools
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_pytest(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")}
    return subprocess.run(
        [sys.executable, "-I", "-m", "pytest", *args, "-p", "no:cacheprovider"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _build_project(root: Path) -> list[str]:
    tests = root / "pkg" / "tests"
    integration = tests / "integration"
    integration.mkdir(parents=True)
    (integration / "conftest.py").write_text(
        textwrap.dedent(
            """
            import pytest

            @pytest.fixture
            def nested_env():
                return "ok"
            """
        )
    )
    for name in ("test_a.py", "test_c.py"):
        (integration / name).write_text("def test_it(nested_env):\n    assert nested_env == 'ok'\n")
    (tests / "test_b.py").write_text("def test_it():\n    pass\n")
    return [
        "pkg/tests/integration/test_a.py",
        "pkg/tests/test_b.py",
        "pkg/tests/integration/test_c.py",
    ]


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_interleaved_file_args_see_nested_conftest_fixtures(tmp_path, order):
    files = _build_project(tmp_path)
    result = _run_pytest(
        ["-o", "addopts=", "-o", "asyncio_mode=strict", *[files[i] for i in order]], tmp_path
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "3 passed" in output, output


@pytest.mark.slow
def test_repo_repro_command():
    result = _run_pytest(
        [
            "backend/tests/integration/test_discovery_tools.py",
            "backend/tests/test_proxy_addon_1245.py",
            "backend/tests/integration/test_comm_tools.py",
            "-o",
            "addopts=",
            "-m",
            "",
        ],
        REPO_ROOT,
    )
    output = result.stdout + result.stderr
    assert "fixture 'legion_test_env' not found" not in output, output
    assert result.returncode == 0, output
