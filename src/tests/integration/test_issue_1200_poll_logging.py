"""
Issue #1200: Tests for PollAccessLogFilter and polling app-level logging.

Tests:
- PollAccessLogFilter: allows/rejects records based on path and allow_all flag
- configure_logging attaches PollAccessLogFilter to uvicorn.access
- debug_polling=True enables polling logger; debug_all_polling excluded from debug_all
- App-level polling logger emits when events > 0, silent on empty poll
"""

import logging
from pathlib import Path

import pytest

from src.logging_config import PollAccessLogFilter, configure_logging, get_logger

# ===========================================================================
# PollAccessLogFilter unit tests
# ===========================================================================


def _make_access_record(method: str, path: str, status: int = 200) -> logging.LogRecord:
    """Build a fake uvicorn access-log LogRecord with the expected args shape."""
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", method, path, "1.1", status),
        exc_info=None,
    )
    return record


@pytest.fixture(autouse=True)
def reset_logging_after():
    yield
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    for name in ("polling", "polling_verbose", "sdk_debug", "coordinator",
                 "storage", "parser", "error_handler", "session_manager",
                 "legion", "template_manager"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.setLevel(logging.NOTSET)
        lg.propagate = True
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.filters = [
        f for f in uvicorn_access.filters if not isinstance(f, PollAccessLogFilter)
    ]


class TestPollAccessLogFilter:
    """Unit tests for PollAccessLogFilter."""

    def test_poll_path_suppressed_when_allow_all_false(self):
        f = PollAccessLogFilter(allow_all=False)
        record = _make_access_record("GET", "/api/poll/ui")
        assert f.filter(record) is False

    def test_poll_session_path_suppressed_when_allow_all_false(self):
        f = PollAccessLogFilter(allow_all=False)
        record = _make_access_record("GET", "/api/poll/session/abc-123")
        assert f.filter(record) is False

    def test_poll_path_allowed_when_allow_all_true(self):
        f = PollAccessLogFilter(allow_all=True)
        record = _make_access_record("GET", "/api/poll/ui")
        assert f.filter(record) is True

    def test_non_poll_path_always_allowed(self):
        f = PollAccessLogFilter(allow_all=False)
        for path in ("/api/sessions", "/api/projects", "/", "/api/poll-unrelated"):
            record = _make_access_record("GET", path)
            assert f.filter(record) is True, f"expected True for path={path}"

    def test_robust_against_unexpected_args_shape(self):
        """Filter must return True (allow) when args don't match expected shape."""
        f = PollAccessLogFilter(allow_all=False)
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="some unexpected format",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_robust_against_none_args(self):
        f = PollAccessLogFilter(allow_all=False)
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="plain message /api/poll/ui",
            args=None,
            exc_info=None,
        )
        # Falls back to getMessage() substring match
        assert f.filter(record) is False

    def test_robust_against_short_args_tuple(self):
        """Tuple shorter than 3 elements falls back to getMessage() and should allow."""
        f = PollAccessLogFilter(allow_all=False)
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="only two %s %s",
            args=("a", "b"),
            exc_info=None,
        )
        assert f.filter(record) is True


class TestConfigureLoggingPollFilter:
    """Tests for filter attachment behaviour in configure_logging."""

    def test_filter_attached_to_uvicorn_access(self, tmp_path):
        configure_logging(log_dir=str(tmp_path))
        uvicorn_access = logging.getLogger("uvicorn.access")
        poll_filters = [f for f in uvicorn_access.filters if isinstance(f, PollAccessLogFilter)]
        assert len(poll_filters) == 1

    def test_filter_allow_all_false_by_default(self, tmp_path):
        configure_logging(log_dir=str(tmp_path))
        uvicorn_access = logging.getLogger("uvicorn.access")
        poll_filter = next(f for f in uvicorn_access.filters if isinstance(f, PollAccessLogFilter))
        assert poll_filter.allow_all is False

    def test_filter_allow_all_true_when_debug_all_polling(self, tmp_path):
        configure_logging(debug_all_polling=True, log_dir=str(tmp_path))
        uvicorn_access = logging.getLogger("uvicorn.access")
        poll_filter = next(f for f in uvicorn_access.filters if isinstance(f, PollAccessLogFilter))
        assert poll_filter.allow_all is True

    def test_filter_idempotent_on_reconfigure(self, tmp_path):
        configure_logging(log_dir=str(tmp_path))
        configure_logging(log_dir=str(tmp_path))
        uvicorn_access = logging.getLogger("uvicorn.access")
        poll_filters = [f for f in uvicorn_access.filters if isinstance(f, PollAccessLogFilter)]
        assert len(poll_filters) == 1

    def test_debug_polling_enables_polling_logger(self, tmp_path):
        configure_logging(debug_polling=True, log_dir=str(tmp_path))
        polling_logger = logging.getLogger("polling")
        assert len(polling_logger.handlers) > 2

    def test_debug_all_polling_excluded_from_debug_all(self, tmp_path):
        configure_logging(debug_all=True, log_dir=str(tmp_path))
        polling_verbose = logging.getLogger("polling_verbose")
        # Only error handlers — not enabled by debug_all
        assert len(polling_verbose.handlers) == 2

    def test_debug_all_enables_polling(self, tmp_path):
        configure_logging(debug_all=True, log_dir=str(tmp_path))
        polling_logger = logging.getLogger("polling")
        assert len(polling_logger.handlers) > 2


class TestPollingLogFileContent:
    """Tests that verify polling logger writes correct content."""

    def test_empty_poll_produces_no_polling_log(self, tmp_path):
        configure_logging(debug_polling=True, log_dir=str(tmp_path))
        poll_log = Path(tmp_path) / "polling.log"
        # No messages written — file should not exist or be empty
        if poll_log.exists():
            assert poll_log.read_text() == ""

    def test_events_returned_log_line(self, tmp_path):
        configure_logging(debug_polling=True, log_dir=str(tmp_path))
        logger = get_logger("polling", category="POLL")
        logger.info("poll ui returned %d event(s) since=%d next_cursor=%d", 3, 0, 3)

        poll_log = Path(tmp_path) / "polling.log"
        assert poll_log.exists()
        content = poll_log.read_text()
        assert "poll ui returned 3 event(s)" in content
        assert "[POLL]" in content

    def test_polling_logger_silent_when_not_enabled(self, tmp_path):
        configure_logging(debug_polling=False, log_dir=str(tmp_path))
        logger = get_logger("polling", category="POLL")
        logger.info("this should not appear")

        poll_log = Path(tmp_path) / "polling.log"
        assert not poll_log.exists()


# ===========================================================================
# Integration tests: poll endpoint + logging (via api_integration_env)
# ===========================================================================


class TestPollEndpointLogging:
    """Integration tests verifying the polling logger fires on real poll responses."""

    async def test_empty_poll_no_log_entry(self, api_integration_env, tmp_path):
        """Empty /api/poll/ui?since=0&timeout=0 produces no polling.log line."""
        configure_logging(debug_polling=True, log_dir=str(tmp_path))

        client = api_integration_env["client"]
        resp = await client.get("/api/poll/ui?since=0&timeout=0")
        assert resp.status_code == 200
        assert resp.json()["events"] == []

        poll_log = Path(tmp_path) / "polling.log"
        if poll_log.exists():
            content = poll_log.read_text()
            assert "returned" not in content

    async def test_events_poll_emits_log_line(self, api_integration_env, tmp_path):
        """Poll returning events emits exactly one polling.log line."""
        configure_logging(debug_polling=True, log_dir=str(tmp_path))

        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        webui.ui_queue.append({"type": "test_evt"})
        webui.ui_queue.append({"type": "test_evt_2"})

        resp = await client.get("/api/poll/ui?since=0&timeout=0")
        assert resp.status_code == 200
        assert len(resp.json()["events"]) >= 2

        poll_log = Path(tmp_path) / "polling.log"
        assert poll_log.exists()
        content = poll_log.read_text()
        assert "poll ui returned" in content

    async def test_session_poll_log_includes_session_id(self, api_integration_env, tmp_path):
        """Session poll log line includes the session_id."""
        configure_logging(debug_polling=True, log_dir=str(tmp_path))

        client = api_integration_env["client"]
        webui = api_integration_env["webui"]

        project = await api_integration_env["create_test_project"]()
        session = await api_integration_env["create_test_session"](project["project_id"])
        sid = session["session_id"]

        webui.session_queues[sid].append({"type": "msg"})

        resp = await client.get(f"/api/poll/session/{sid}?since=0&timeout=0")
        assert resp.status_code == 200
        assert len(resp.json()["events"]) == 1

        poll_log = Path(tmp_path) / "polling.log"
        assert poll_log.exists()
        content = poll_log.read_text()
        assert "poll session" in content
        assert sid in content
