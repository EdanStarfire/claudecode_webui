"""Tests for the logging configuration module."""

import logging
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.logging_config import setup_logging, get_logger, set_module_log_level


class TestLoggingConfig:
    """Test logging configuration functions."""

    def test_get_logger(self):
        """Test logger creation."""
        logger = get_logger("test_module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_module"

    @patch('src.logging_config.Path')
    @patch('logging.config.dictConfig')
    def test_setup_logging_basic(self, mock_dict_config, mock_path):
        """Test basic logging setup."""
        mock_path.return_value.mkdir = MagicMock()

        setup_logging(
            log_level="DEBUG",
            log_dir="test/logs",
            enable_console=True,
            enable_file=True
        )

        # Verify directory creation
        mock_path.assert_called_with("test/logs")
        mock_path.return_value.mkdir.assert_called_with(parents=True, exist_ok=True)

        # Verify logging config was called
        mock_dict_config.assert_called_once()
        config = mock_dict_config.call_args[0][0]

        # Check configuration structure
        assert config["version"] == 1
        assert config["disable_existing_loggers"] == False
        assert "formatters" in config
        assert "handlers" in config
        assert "loggers" in config
        assert "root" in config

    @patch('src.logging_config.Path')
    @patch('logging.config.dictConfig')
    def test_setup_logging_console_only(self, mock_dict_config, mock_path):
        """Test console-only logging setup."""
        mock_path.return_value.mkdir = MagicMock()

        setup_logging(
            log_level="INFO",
            enable_console=True,
            enable_file=False
        )

        mock_dict_config.assert_called_once()
        config = mock_dict_config.call_args[0][0]

        # Should have console handler but no file handlers
        assert "console" in config["handlers"]
        assert "file_app" not in config["handlers"]
        assert "file_error" not in config["handlers"]
        assert "console" in config["root"]["handlers"]

    @patch('src.logging_config.Path')
    @patch('logging.config.dictConfig')
    def test_setup_logging_file_only(self, mock_dict_config, mock_path):
        """Test file-only logging setup."""
        mock_path.return_value.mkdir = MagicMock()

        setup_logging(
            log_level="WARNING",
            enable_console=False,
            enable_file=True
        )

        mock_dict_config.assert_called_once()
        config = mock_dict_config.call_args[0][0]

        # Should have file handlers but no console handler
        assert "console" not in config["handlers"]
        assert "file_app" in config["handlers"]
        assert "file_error" in config["handlers"]
        assert "file_claude" in config["handlers"]
        assert "console" not in config["root"]["handlers"]

    @patch('src.logging_config.Path')
    @patch('logging.config.dictConfig')
    def test_setup_logging_module_levels(self, mock_dict_config, mock_path):
        """Test module-specific log levels."""
        mock_path.return_value.mkdir = MagicMock()

        setup_logging(log_level="INFO")

        mock_dict_config.assert_called_once()
        config = mock_dict_config.call_args[0][0]

        # Check module-specific configurations
        loggers = config["loggers"]
        assert "claude_sdk" in loggers
        assert "message_parser" in loggers
        assert "web_server" in loggers

        # Third-party modules should have different levels
        assert loggers["fastapi"]["level"] == "WARNING"
        assert loggers["uvicorn"]["level"] == "INFO"

    @patch('logging.getLogger')
    def test_set_module_log_level(self, mock_get_logger):
        """Test dynamic log level adjustment."""
        mock_logger = MagicMock()
        mock_main_logger = MagicMock()

        def get_logger_side_effect(name):
            if name == "test_module":
                return mock_logger
            else:
                return mock_main_logger

        mock_get_logger.side_effect = get_logger_side_effect

        set_module_log_level("test_module", "DEBUG")

        mock_logger.setLevel.assert_called_once_with(logging.DEBUG)
        mock_main_logger.info.assert_called_once()

    @patch('src.logging_config.Path')
    @patch('logging.config.dictConfig')
    def test_log_file_configuration(self, mock_dict_config, mock_path):
        """Test log file handler configurations."""
        mock_path.return_value.mkdir = MagicMock()

        setup_logging(
            log_dir="custom/logs",
            enable_file=True
        )

        mock_dict_config.assert_called_once()
        config = mock_dict_config.call_args[0][0]

        # Check file handler configurations
        handlers = config["handlers"]

        # Main app log
        app_handler = handlers["file_app"]
        assert app_handler["filename"] == "custom/logs/app.log"
        assert app_handler["maxBytes"] == 10485760  # 10MB
        assert app_handler["backupCount"] == 5

        # Error log
        error_handler = handlers["file_error"]
        assert error_handler["filename"] == "custom/logs/error.log"
        assert error_handler["level"] == "ERROR"

        # Claude process log
        claude_handler = handlers["file_claude"]
        assert claude_handler["filename"] == "custom/logs/claude_sdk.log"
        assert claude_handler["level"] == "DEBUG"
        assert claude_handler["backupCount"] == 10

    @patch('src.logging_config.Path')
    @patch('logging.config.dictConfig')
    def test_formatter_configuration(self, mock_dict_config, mock_path):
        """Test log formatter configurations."""
        mock_path.return_value.mkdir = MagicMock()

        setup_logging()

        mock_dict_config.assert_called_once()
        config = mock_dict_config.call_args[0][0]

        # Check formatters
        formatters = config["formatters"]

        detailed_format = formatters["detailed"]["format"]
        assert "%(asctime)s" in detailed_format
        assert "%(name)s" in detailed_format
        assert "%(levelname)s" in detailed_format
        assert "%(filename)s" in detailed_format
        assert "%(lineno)d" in detailed_format
        assert "%(funcName)s" in detailed_format
        assert "%(message)s" in detailed_format

        simple_format = formatters["simple"]["format"]
        assert "%(levelname)s" in simple_format
        assert "%(name)s" in simple_format
        assert "%(message)s" in simple_format

    def test_log_level_validation(self):
        """Test that various log levels work correctly."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

        for level in valid_levels:
            # Should not raise an exception
            with patch('src.logging_config.Path'), \
                 patch('logging.config.dictConfig'):
                setup_logging(log_level=level)

    @patch('src.logging_config.Path')
    @patch('logging.config.dictConfig')
    def test_directory_creation_error_handling(self, mock_dict_config, mock_path):
        """Test handling of directory creation errors."""
        # Mock directory creation to raise an exception
        mock_path.return_value.mkdir.side_effect = OSError("Permission denied")

        # Should still proceed with logging setup
        with pytest.raises(OSError):
            setup_logging(log_dir="/invalid/path")

        # The exception should propagate since we can't create log directory