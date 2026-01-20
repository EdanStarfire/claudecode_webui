"""Comprehensive tests for logging_config module.

Tests cover:
- StandardizedFormatter formatting behavior
- CategoryAdapter category injection
- configure_logging() with various debug flags
- get_logger() with and without categories
- Log file creation and routing
- Error handler attachment
- Log rotation configuration
"""

import logging
import tempfile
from pathlib import Path

import pytest

from ..logging_config import (
    CategoryAdapter,
    StandardizedFormatter,
    configure_logging,
    get_logger,
    get_main_logger,
)


@pytest.fixture
def temp_log_dir():
    """Create a temporary directory for log files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging configuration between tests to prevent pollution."""
    # Clear all handlers and loggers before test
    yield

    # Cleanup after test
    # Reset root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.WARNING)

    # Clear all category loggers
    logger_names = [
        'websocket_debug', 'websocket_verbose', 'sdk_debug',
        'coordinator', 'storage', 'parser', 'error_handler',
        'session_manager', 'legion', 'template_manager'
    ]
    for name in logger_names:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


class TestStandardizedFormatter:
    """Test StandardizedFormatter formatting behavior."""

    def test_format_with_category(self):
        """Test formatter with category in record."""
        formatter = StandardizedFormatter()
        record = logging.LogRecord(
            name='test_logger',
            level=logging.INFO,
            pathname='test.py',
            lineno=1,
            msg='Test message',
            args=(),
            exc_info=None
        )
        record.category = 'TEST_CATEGORY'

        formatted = formatter.format(record)

        # Check format: YYYY-MM-DDTHH:mm:ss.mmm LEVEL - [CATEGORY] Message
        assert 'INFO - [TEST_CATEGORY] Test message' in formatted
        assert 'T' in formatted  # ISO format timestamp

    def test_format_without_category(self):
        """Test formatter falls back to logger name when no category."""
        formatter = StandardizedFormatter()
        record = logging.LogRecord(
            name='test_logger',
            level=logging.WARNING,
            pathname='test.py',
            lineno=1,
            msg='Warning message',
            args=(),
            exc_info=None
        )

        formatted = formatter.format(record)

        # Should use logger name in uppercase as fallback
        assert 'WARNING - [TEST_LOGGER] Warning message' in formatted

    def test_format_includes_milliseconds(self):
        """Test formatter includes milliseconds in timestamp."""
        formatter = StandardizedFormatter()
        record = logging.LogRecord(
            name='test',
            level=logging.DEBUG,
            pathname='test.py',
            lineno=1,
            msg='Debug',
            args=(),
            exc_info=None
        )
        record.msecs = 123.456

        formatted = formatter.format(record)

        # Should have .mmm format
        assert '.123' in formatted


class TestCategoryAdapter:
    """Test CategoryAdapter category injection."""

    def test_adapter_adds_category_to_extra(self):
        """Test that CategoryAdapter adds category to log record."""
        logger = logging.getLogger('test')
        adapter = CategoryAdapter(logger, 'MY_CATEGORY')

        msg, kwargs = adapter.process('Test message', {})

        assert 'extra' in kwargs
        assert kwargs['extra']['category'] == 'MY_CATEGORY'

    def test_adapter_preserves_existing_extra(self):
        """Test that CategoryAdapter preserves existing extra dict."""
        logger = logging.getLogger('test')
        adapter = CategoryAdapter(logger, 'MY_CATEGORY')

        original_extra = {'existing_key': 'existing_value'}
        msg, kwargs = adapter.process('Test', {'extra': original_extra})

        assert kwargs['extra']['existing_key'] == 'existing_value'
        assert kwargs['extra']['category'] == 'MY_CATEGORY'

    def test_adapter_category_appears_in_formatted_output(self, temp_log_dir):
        """Test that category appears in actual log output."""
        configure_logging(debug_sdk=True, log_dir=temp_log_dir)

        logger = get_logger('sdk_debug', category='TEST_CAT')
        logger.debug('Test message')

        # Read log file
        log_file = Path(temp_log_dir) / 'sdk_debug.log'
        content = log_file.read_text()

        assert '[TEST_CAT]' in content
        assert 'Test message' in content


class TestConfigureLoggingBasics:
    """Test configure_logging() basic functionality."""

    def test_creates_log_directory(self, temp_log_dir):
        """Test that configure_logging creates the log directory."""
        log_dir = Path(temp_log_dir) / 'new_logs'

        configure_logging(log_dir=str(log_dir))

        assert log_dir.exists()
        assert log_dir.is_dir()

    def test_default_configuration_no_flags(self, temp_log_dir):
        """Test configuration with no debug flags."""
        configure_logging(log_dir=temp_log_dir)

        # Coordinator should be enabled by default
        coordinator_logger = logging.getLogger('coordinator')
        assert len(coordinator_logger.handlers) > 0

        # Debug loggers should have minimal handlers (just error handlers)
        sdk_logger = logging.getLogger('sdk_debug')
        # Should have error handlers but not debug file handler
        assert len(sdk_logger.handlers) == 2  # error.log + console error

    def test_debug_all_enables_most_categories(self, temp_log_dir):
        """Test that debug_all enables most debug categories."""
        configure_logging(debug_all=True, log_dir=temp_log_dir)

        # Check that key loggers are enabled
        assert len(logging.getLogger('websocket_debug').handlers) > 2
        assert len(logging.getLogger('sdk_debug').handlers) > 2
        assert len(logging.getLogger('storage').handlers) > 2

    def test_ping_pong_excluded_from_debug_all(self, temp_log_dir):
        """Test that ping/pong logging is NOT enabled by debug_all."""
        configure_logging(debug_all=True, log_dir=temp_log_dir)

        verbose_logger = logging.getLogger('websocket_verbose')
        # Should only have error handlers, not debug handlers
        assert len(verbose_logger.handlers) == 2  # error.log + console error


class TestIndividualDebugFlags:
    """Test individual debug flag behavior."""

    def test_debug_websocket_flag(self, temp_log_dir):
        """Test debug_websocket flag enables websocket logging."""
        configure_logging(debug_websocket=True, log_dir=temp_log_dir)

        ws_logger = logging.getLogger('websocket_debug')
        # Should have: file handler + console + error.log + console error
        assert len(ws_logger.handlers) >= 3

    def test_debug_sdk_flag(self, temp_log_dir):
        """Test debug_sdk flag enables SDK logging."""
        configure_logging(debug_sdk=True, log_dir=temp_log_dir)

        sdk_logger = logging.getLogger('sdk_debug')
        assert len(sdk_logger.handlers) >= 3

    def test_debug_storage_flag(self, temp_log_dir):
        """Test debug_storage flag enables storage logging."""
        configure_logging(debug_storage=True, log_dir=temp_log_dir)

        storage_logger = logging.getLogger('storage')
        assert len(storage_logger.handlers) >= 3

    def test_debug_permissions_enables_sdk_logging(self, temp_log_dir):
        """Test that debug_permissions also enables SDK logging."""
        configure_logging(debug_permissions=True, log_dir=temp_log_dir)

        # SDK logger should be enabled when permissions are enabled
        sdk_logger = logging.getLogger('sdk_debug')
        assert len(sdk_logger.handlers) >= 3


class TestLogFileCreation:
    """Test log file creation behavior."""

    def test_log_files_created_when_enabled(self, temp_log_dir):
        """Test that log files are created for enabled categories."""
        configure_logging(debug_sdk=True, debug_storage=True, log_dir=temp_log_dir)

        # Write some log messages
        sdk_logger = logging.getLogger('sdk_debug')
        sdk_logger.debug('SDK test message')

        storage_logger = logging.getLogger('storage')
        storage_logger.debug('Storage test message')

        # Check files exist
        sdk_log = Path(temp_log_dir) / 'sdk_debug.log'
        storage_log = Path(temp_log_dir) / 'storage.log'

        assert sdk_log.exists()
        assert storage_log.exists()

    def test_log_files_not_created_when_disabled(self, temp_log_dir):
        """Test that log files are not created for disabled categories."""
        configure_logging(debug_sdk=True, log_dir=temp_log_dir)

        # Don't write to storage logger
        storage_log = Path(temp_log_dir) / 'storage.log'

        # File should not exist since storage debug is not enabled
        # and no messages were written
        assert not storage_log.exists()

    def test_error_log_always_created(self, temp_log_dir):
        """Test that error.log is always created."""
        configure_logging(log_dir=temp_log_dir)

        # Write an error
        root_logger = logging.getLogger()
        root_logger.error('Test error')

        error_log = Path(temp_log_dir) / 'error.log'
        assert error_log.exists()


class TestLogRouting:
    """Test log message routing behavior."""

    def test_debug_routes_to_category_file(self, temp_log_dir):
        """Test that DEBUG messages route to category file when enabled."""
        configure_logging(debug_sdk=True, log_dir=temp_log_dir)

        logger = logging.getLogger('sdk_debug')
        logger.debug('Debug test message')

        sdk_log = Path(temp_log_dir) / 'sdk_debug.log'
        content = sdk_log.read_text()

        assert 'Debug test message' in content

    def test_error_routes_to_error_log(self, temp_log_dir):
        """Test that ERROR messages always route to error.log."""
        configure_logging(log_dir=temp_log_dir)

        logger = logging.getLogger('sdk_debug')
        logger.error('Error test message')

        error_log = Path(temp_log_dir) / 'error.log'
        content = error_log.read_text()

        assert 'Error test message' in content

    def test_error_routes_to_both_files(self, temp_log_dir):
        """Test that ERROR messages route to both category file AND error.log."""
        configure_logging(debug_sdk=True, log_dir=temp_log_dir)

        logger = logging.getLogger('sdk_debug')
        logger.error('Error in SDK')

        # Should be in SDK log
        sdk_log = Path(temp_log_dir) / 'sdk_debug.log'
        sdk_content = sdk_log.read_text()
        assert 'Error in SDK' in sdk_content

        # Should also be in error log
        error_log = Path(temp_log_dir) / 'error.log'
        error_content = error_log.read_text()
        assert 'Error in SDK' in error_content

    def test_disabled_category_still_routes_errors(self, temp_log_dir):
        """Test that errors are logged even when category debug is disabled."""
        configure_logging(debug_sdk=False, log_dir=temp_log_dir)

        logger = logging.getLogger('sdk_debug')
        logger.error('Error without debug enabled')

        # Should still appear in error.log
        error_log = Path(temp_log_dir) / 'error.log'
        content = error_log.read_text()
        assert 'Error without debug enabled' in content


class TestGetLogger:
    """Test get_logger() function behavior."""

    def test_get_logger_without_category(self):
        """Test get_logger returns standard logger without category."""
        logger = get_logger('test_logger')

        assert isinstance(logger, logging.Logger)
        assert not isinstance(logger, CategoryAdapter)

    def test_get_logger_with_category(self):
        """Test get_logger returns CategoryAdapter with category."""
        logger = get_logger('test_logger', category='TEST_CAT')

        assert isinstance(logger, CategoryAdapter)
        assert logger.category == 'TEST_CAT'

    def test_get_logger_category_in_logs(self, temp_log_dir):
        """Test that category from get_logger appears in log output."""
        configure_logging(debug_sdk=True, log_dir=temp_log_dir)

        logger = get_logger('sdk_debug', category='MY_MODULE')
        logger.debug('Module message')

        sdk_log = Path(temp_log_dir) / 'sdk_debug.log'
        content = sdk_log.read_text()

        assert '[MY_MODULE]' in content
        assert 'Module message' in content


class TestLogRotation:
    """Test log rotation configuration."""

    def test_rotating_file_handler_configured(self, temp_log_dir):
        """Test that RotatingFileHandler is used for log files."""
        configure_logging(debug_sdk=True, log_dir=temp_log_dir)

        logger = logging.getLogger('sdk_debug')

        # Check that at least one handler is a RotatingFileHandler
        has_rotating = any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            for h in logger.handlers
        )
        assert has_rotating

    def test_rotation_settings(self, temp_log_dir):
        """Test that rotation maxBytes and backupCount are configured."""
        from ..logging_config import LOG_BACKUP_COUNT, MAX_LOG_FILE_SIZE

        configure_logging(debug_sdk=True, log_dir=temp_log_dir)

        logger = logging.getLogger('sdk_debug')
        rotating_handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]

        assert len(rotating_handlers) > 0
        handler = rotating_handlers[0]
        assert handler.maxBytes == MAX_LOG_FILE_SIZE
        assert handler.backupCount == LOG_BACKUP_COUNT


class TestGetMainLogger:
    """Test get_main_logger() convenience function."""

    def test_get_main_logger_returns_root(self):
        """Test that get_main_logger returns root logger."""
        main_logger = get_main_logger()
        root_logger = logging.getLogger()

        assert main_logger is root_logger


class TestIntegration:
    """Integration tests for complete logging workflows."""

    def test_full_logging_workflow(self, temp_log_dir):
        """Test complete logging workflow with multiple components."""
        # Configure with multiple flags
        configure_logging(
            debug_sdk=True,
            debug_storage=True,
            debug_websocket=True,
            log_dir=temp_log_dir
        )

        # Create loggers with categories
        sdk_logger = get_logger('sdk_debug', category='SDK')
        storage_logger = get_logger('storage', category='STORAGE')
        ws_logger = get_logger('websocket_debug', category='WS')

        # Write various log levels
        sdk_logger.debug('SDK debug message')
        sdk_logger.error('SDK error message')

        storage_logger.info('Storage info message')
        storage_logger.error('Storage error message')

        ws_logger.debug('WebSocket debug message')

        # Verify files exist
        assert (Path(temp_log_dir) / 'sdk_debug.log').exists()
        assert (Path(temp_log_dir) / 'storage.log').exists()
        assert (Path(temp_log_dir) / 'websocket_debug.log').exists()
        assert (Path(temp_log_dir) / 'error.log').exists()

        # Verify content routing
        sdk_content = (Path(temp_log_dir) / 'sdk_debug.log').read_text()
        assert '[SDK]' in sdk_content
        assert 'SDK debug message' in sdk_content

        error_content = (Path(temp_log_dir) / 'error.log').read_text()
        assert 'SDK error message' in error_content
        assert 'Storage error message' in error_content

    def test_multiple_categories_simultaneously(self, temp_log_dir):
        """Test multiple loggers with different categories writing simultaneously."""
        configure_logging(debug_sdk=True, log_dir=temp_log_dir)

        # Create multiple loggers with different categories
        logger1 = get_logger('sdk_debug', category='MODULE_A')
        logger2 = get_logger('sdk_debug', category='MODULE_B')
        logger3 = get_logger('sdk_debug', category='MODULE_C')

        # Write messages
        logger1.debug('Message from A')
        logger2.debug('Message from B')
        logger3.debug('Message from C')

        # Verify all categories appear in log
        content = (Path(temp_log_dir) / 'sdk_debug.log').read_text()
        assert '[MODULE_A]' in content
        assert '[MODULE_B]' in content
        assert '[MODULE_C]' in content
        assert 'Message from A' in content
        assert 'Message from B' in content
        assert 'Message from C' in content
