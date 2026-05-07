"""Regression test for issue #1332.

Verifies that logger.exception() produces traceback output via StandardizedFormatter.
"""

import logging
from io import StringIO

from ..logging_config import StandardizedFormatter, configure_logging


class TestLoggerExceptionTraceback:
    """StandardizedFormatter must preserve exc_info so tracebacks appear in output."""

    def test_formatter_includes_traceback_in_output(self):
        """StandardizedFormatter.format() appends traceback when exc_info is set."""
        formatter = StandardizedFormatter()
        try:
            raise ValueError("something went wrong")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="operation failed",
            args=(),
            exc_info=exc_info,
        )

        formatted = formatter.format(record)

        assert "operation failed" in formatted
        assert "ValueError" in formatted
        assert "something went wrong" in formatted
        assert "Traceback (most recent call last)" in formatted

    def test_logger_exception_writes_traceback_to_error_log(self, tmp_path):
        """logger.exception() call produces traceback lines in error.log."""
        configure_logging(log_dir=str(tmp_path))

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.ERROR)
        handler.setFormatter(StandardizedFormatter())

        test_logger = logging.getLogger("test_1332_exception")
        test_logger.handlers.clear()
        test_logger.propagate = False
        test_logger.setLevel(logging.ERROR)
        test_logger.addHandler(handler)

        try:
            raise RuntimeError("disk full")
        except RuntimeError:
            test_logger.exception("backup failed")

        output = stream.getvalue()
        assert "backup failed" in output
        assert "RuntimeError" in output
        assert "disk full" in output
        assert "Traceback (most recent call last)" in output

    def test_logger_exception_written_to_error_log_file(self, tmp_path):
        """Error log file contains both message and traceback when logger.exception is used."""
        configure_logging(log_dir=str(tmp_path))

        root_logger = logging.getLogger()

        try:
            raise AttributeError("'SessionInfo' object has no attribute 'model'")
        except AttributeError:
            root_logger.exception("Failed to record analytics for session %s", "abc-123")

        error_log = tmp_path / "error.log"
        assert error_log.exists()
        content = error_log.read_text()

        assert "Failed to record analytics for session abc-123" in content
        assert "AttributeError" in content
        assert "'SessionInfo' object has no attribute 'model'" in content

    def test_format_without_exc_info_unchanged(self):
        """Normal log records without exc_info are unaffected."""
        formatter = StandardizedFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="normal message",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)

        assert "normal message" in formatted
        assert "Traceback" not in formatted

    def test_exception_outside_except_block_does_not_crash(self):
        """logger.exception() outside an except block logs NoneType: None, does not raise."""
        formatter = StandardizedFormatter()

        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="unexpected call site",
            args=(),
            exc_info=(None, None, None),
        )

        formatted = formatter.format(record)
        assert "unexpected call site" in formatted
