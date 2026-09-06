"""Unit tests for src/backend_reachability.py (issue #1844).

No network — exercises BackendReachabilityTracker's log-suppression state
machine directly, and to_http_exception()'s response-contract parity with
shared/exception_handlers.py's handle_exceptions decorator.
"""

import logging
import time
from unittest.mock import patch

import httpx
import pytest

import shared.logging_config as logging_config
from src.backend_reachability import BackendReachabilityTracker, to_http_exception


def test_issue_1844_first_failure_logs_once(caplog):
    tracker = BackendReachabilityTracker()

    with caplog.at_level(logging.WARNING, logger="src.backend_reachability"):
        tracker.record_failure(httpx.ConnectError("refused"))

    assert tracker.is_unreachable is True
    assert caplog.text.count("Backend unreachable") == 1
    assert "ConnectError" in caplog.text
    assert "refused" in caplog.text


def test_issue_1844_repeated_failures_within_cooldown_log_only_once(caplog):
    tracker = BackendReachabilityTracker(cooldown_seconds=60)

    with caplog.at_level(logging.WARNING, logger="src.backend_reachability"):
        for _ in range(5):
            tracker.record_failure(httpx.ConnectError("refused"))

    assert caplog.text.count("Backend unreachable") == 1


def test_issue_1844_failure_logs_again_after_cooldown_elapses(caplog):
    tracker = BackendReachabilityTracker(cooldown_seconds=0.05)

    with caplog.at_level(logging.WARNING, logger="src.backend_reachability"):
        tracker.record_failure(httpx.ConnectError("refused"))
        time.sleep(0.1)
        tracker.record_failure(httpx.ConnectError("refused"))

    assert caplog.text.count("Backend unreachable") == 2


def test_issue_1844_record_success_after_failure_logs_recovery_and_resets(caplog):
    tracker = BackendReachabilityTracker()
    tracker.record_failure(httpx.ConnectError("refused"))

    with caplog.at_level(logging.WARNING, logger="src.backend_reachability"):
        tracker.record_success()

    assert tracker.is_unreachable is False
    assert "Backend reachable again" in caplog.text
    assert caplog.text.count("Backend reachable again") == 1


def test_issue_1844_record_success_with_no_prior_failure_logs_nothing(caplog):
    tracker = BackendReachabilityTracker()

    with caplog.at_level(logging.WARNING, logger="src.backend_reachability"):
        tracker.record_success()

    assert tracker.is_unreachable is False
    assert caplog.text == ""


def test_issue_1844_recovery_then_new_failure_logs_again(caplog):
    """A second outage after a full recovery must log again immediately —
    the cooldown only applies within a single ongoing outage."""
    tracker = BackendReachabilityTracker(cooldown_seconds=60)

    with caplog.at_level(logging.WARNING, logger="src.backend_reachability"):
        tracker.record_failure(httpx.ConnectError("refused"))
        tracker.record_success()
        tracker.record_failure(httpx.ConnectError("refused again"))

    assert caplog.text.count("Backend unreachable") == 2
    assert caplog.text.count("Backend reachable again") == 1


@pytest.mark.asyncio
async def test_issue_1844_to_http_exception_sanitized_when_debug_off():
    with patch.object(logging_config, '_log_config', {'debug_error_handler': False}):
        exc = to_http_exception(httpx.ConnectError("internal/path/secret"))

    assert exc.status_code == 500
    assert exc.detail == "An internal error occurred"


@pytest.mark.asyncio
async def test_issue_1844_to_http_exception_full_detail_when_debug_on():
    with patch.object(logging_config, '_log_config', {'debug_error_handler': True}):
        exc = to_http_exception(httpx.ConnectError("refused"))

    assert exc.status_code == 500
    assert "refused" in exc.detail
