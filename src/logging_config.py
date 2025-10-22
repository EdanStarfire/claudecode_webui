"""Comprehensive logging configuration for Claude Code WebUI.

This module provides a multi-tier logging system with:
- Configurable debug logging via CLI flags
- Category-specific log files
- Standardized log formatting
- Console and file output control
"""

import logging
import logging.handlers
from pathlib import Path
from typing import Optional


class StandardizedFormatter(logging.Formatter):
    """Custom formatter with standardized timestamp and category formatting.

    Format: YYYY-MM-DDTHH:mm:ss.mmm LEVEL - [CATEGORY] Message
    """

    def format(self, record):
        """Format log record with timestamp, level, category, and message."""
        # Get timestamp with milliseconds
        timestamp = self.formatTime(record, '%Y-%m-%dT%H:%M:%S')
        ms = f"{record.msecs:03.0f}"

        # Get category from record (default to logger name)
        category = getattr(record, 'category', record.name.upper())

        # Format: YYYY-MM-DDTHH:mm:ss.mmm LEVEL - [CATEGORY] Message
        return f"{timestamp}.{ms} {record.levelname} - [{category}] {record.getMessage()}"


class CategoryAdapter(logging.LoggerAdapter):
    """Logger adapter that adds category to log records."""

    def __init__(self, logger, category):
        super().__init__(logger, {})
        self.category = category

    def process(self, msg, kwargs):
        """Add category to the log record."""
        # Get the record's extra dict or create one
        extra = kwargs.get('extra', {})
        extra['category'] = self.category
        kwargs['extra'] = extra
        return msg, kwargs


# Global registry of configured loggers
_configured_loggers = {}
_log_config = {}


def configure_logging(
    debug_websocket: bool = False,
    debug_sdk: bool = False,
    debug_permissions: bool = False,
    debug_storage: bool = False,
    debug_parser: bool = False,
    debug_error_handler: bool = False,
    debug_legion: bool = False,
    debug_all: bool = False,
    log_dir: str = "data/logs"
) -> None:
    """Configure the logging system with CLI flag controls.

    Args:
        debug_websocket: Enable WebSocket lifecycle debugging
        debug_sdk: Enable SDK integration debugging
        debug_permissions: Enable permission callback debugging
        debug_storage: Enable data storage debugging
        debug_parser: Enable message parser debugging
        debug_error_handler: Enable error handler debugging
        debug_legion: Enable Legion multi-agent system debugging
        debug_all: Enable all debug logging
        log_dir: Directory for log files
    """
    global _log_config

    # Ensure log directory exists
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # Store configuration for get_logger to reference
    _log_config = {
        'debug_websocket': debug_websocket or debug_all,
        'debug_sdk': debug_sdk or debug_all,
        'debug_permissions': debug_permissions or debug_all,
        'debug_storage': debug_storage or debug_all,
        'debug_parser': debug_parser or debug_all,
        'debug_error_handler': debug_error_handler or debug_all,
        'debug_legion': debug_legion or debug_all,
        'debug_session_manager': debug_all,
        'log_dir': log_dir
    }

    # Create formatter
    formatter = StandardizedFormatter()

    # Create shared error handler (all ERROR+ logs go here)
    error_handler = logging.handlers.RotatingFileHandler(
        f"{log_dir}/error.log",
        maxBytes=10485760,  # 10MB
        backupCount=5,
        encoding='utf8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    # Create console handler for errors
    console_error_handler = logging.StreamHandler()
    console_error_handler.setLevel(logging.ERROR)
    console_error_handler.setFormatter(formatter)

    # Configure logger definitions
    logger_configs = {
        'websocket_debug': {
            'file': f"{log_dir}/websocket_debug.log",
            'enabled': _log_config['debug_websocket'],
            'console': _log_config['debug_websocket'],
            'level': logging.DEBUG
        },
        'sdk_debug': {
            'file': f"{log_dir}/sdk_debug.log",
            'enabled': _log_config['debug_sdk'] or _log_config['debug_permissions'],
            'console': _log_config['debug_sdk'] or _log_config['debug_permissions'],
            'level': logging.DEBUG
        },
        'coordinator': {
            'file': f"{log_dir}/coordinator.log",
            'enabled': True,  # Always enabled
            'console': True,  # Always to console
            'level': logging.INFO
        },
        'storage': {
            'file': f"{log_dir}/storage.log",
            'enabled': _log_config['debug_storage'],
            'console': _log_config['debug_storage'],
            'level': logging.DEBUG
        },
        'parser': {
            'file': f"{log_dir}/parser.log",
            'enabled': _log_config['debug_parser'],
            'console': _log_config['debug_parser'],
            'level': logging.DEBUG
        },
        'error_handler': {
            'file': f"{log_dir}/error.log",
            'enabled': _log_config['debug_error_handler'],
            'console': _log_config['debug_error_handler'],
            'level': logging.DEBUG
        },
        'session_manager': {
            'file': f"{log_dir}/session_manager.log",
            'enabled': _log_config['debug_session_manager'],
            'console': _log_config['debug_session_manager'],
            'level': logging.DEBUG
        },
        'legion': {
            'file': f"{log_dir}/legion.log",
            'enabled': _log_config['debug_legion'],
            'console': _log_config['debug_legion'],
            'level': logging.DEBUG
        }
    }

    # Create loggers with handlers
    for logger_name, config in logger_configs.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(config['level'])
        logger.handlers.clear()
        logger.propagate = False

        if config['enabled']:
            # File handler
            file_handler = logging.handlers.RotatingFileHandler(
                config['file'],
                maxBytes=10485760,  # 10MB
                backupCount=5,
                encoding='utf8'
            )
            file_handler.setLevel(config['level'])
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

            # Console handler if enabled
            if config['console']:
                console_handler = logging.StreamHandler()
                console_handler.setLevel(config['level'])
                console_handler.setFormatter(formatter)
                logger.addHandler(console_handler)

        # Always add error handler to send ERROR+ to error.log
        logger.addHandler(error_handler)

        # Always add console error handler for ERROR+ to console
        logger.addHandler(console_error_handler)

    # Configure root logger for general errors
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    root_logger.handlers.clear()
    root_logger.addHandler(error_handler)
    root_logger.addHandler(console_error_handler)

    # Log configuration status
    coord_logger = logging.getLogger('coordinator')
    coord_logger.info("Logging system configured")


def get_logger(name: str, category: Optional[str] = None) -> logging.Logger:
    """Get a logger instance with optional category.

    Args:
        name: Logger name (e.g., 'websocket_debug', 'sdk_debug', 'coordinator')
        category: Optional category tag for log messages (e.g., 'WS_LIFECYCLE', 'SDK')

    Returns:
        Configured logger instance or CategoryAdapter if category provided
    """
    logger = logging.getLogger(name)

    if category:
        # Return adapter that adds category to all log records
        return CategoryAdapter(logger, category)

    return logger


# Convenience function for getting main application logger
def get_main_logger() -> logging.Logger:
    """Get the main application logger for general errors."""
    return logging.getLogger()
