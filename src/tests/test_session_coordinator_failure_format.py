"""Regression tests for issue #1264 — failure-pill regex covers both proxy host names."""
import pytest

from src.session_coordinator import SessionCoordinator


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "connect to cc-webui.internal:8000 failed",
            "Session failed: Proxy startup failed: cc-webui.internal not reachable",
        ),
        (
            "getaddrinfo: host.docker.internal: Name or service not known",
            "Session failed: Proxy startup failed: cc-webui.internal not reachable",
        ),
    ],
)
def test_proxy_failure_pattern_matches_both_names(raw, expected):
    coord = SessionCoordinator.__new__(SessionCoordinator)
    assert coord._format_failure_content("", raw) == expected
