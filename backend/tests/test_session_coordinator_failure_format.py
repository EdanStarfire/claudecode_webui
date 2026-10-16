"""Regression tests for issue #1264 — failure-pill regex covers both proxy host names."""
import pytest

from backend.message_pipeline import MessagePipeline


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
    # Issue #2189 (coordinator-split PR 2): _format_failure_content moved to
    # MessagePipeline — it's self-independent (pure regex matching), so this
    # test constructs the bare class via __new__ the same way it did for
    # SessionCoordinator pre-move.
    pipeline = MessagePipeline.__new__(MessagePipeline)
    assert pipeline._format_failure_content("", raw) == expected
