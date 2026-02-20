"""Root test configuration — applies to all tests."""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _unset_claudecode_env():
    """
    Unset CLAUDECODE env var to prevent SDK nested-session detection.

    The Claude Agent SDK refuses to start inside another Claude Code session.
    Since tests run inside Claude Code during development, this env var must
    be removed so integration tests can start real SDK sessions.
    """
    old = os.environ.pop("CLAUDECODE", None)
    yield
    if old is not None:
        os.environ["CLAUDECODE"] = old
