"""Comprehensive logging configuration for Claude Code WebUI."""

import logging
import logging.config
import os
from pathlib import Path
from typing import Dict, Any


def setup_logging(
    log_level: str = "INFO",
    log_dir: str = "data/logs",
    enable_console: bool = True,
    enable_file: bool = True
) -> None:
    """
    Configure comprehensive logging for the application.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files
        enable_console: Whether to enable console logging
        enable_file: Whether to enable file logging
    """
    # Ensure log directory exists
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # Define log format
    log_format = (
        "%(asctime)s - %(name)s - %(levelname)s - "
        "%(filename)s:%(lineno)d - %(funcName)s - %(message)s"
    )

    # Build logging configuration
    config: Dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "detailed": {
                "format": log_format,
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            "simple": {
                "format": "%(levelname)s - %(name)s - %(message)s"
            }
        },
        "handlers": {},
        "loggers": {
            # Application modules - enhanced for debugging session lifecycle issues
            "claude_sdk": {"level": "DEBUG", "propagate": True},  # Force DEBUG for SDK lifecycle analysis
            "session_manager": {"level": log_level, "propagate": True},
            "session_coordinator": {"level": "DEBUG", "propagate": True},  # Enhanced for session coordination
            "message_parser": {"level": log_level, "propagate": True},
            "sdk_discovery_tool": {"level": log_level, "propagate": True},
            "web_server": {"level": "DEBUG", "propagate": True},  # Enhanced for WebSocket lifecycle analysis

            # Third-party modules (less verbose)
            "fastapi": {"level": "WARNING", "propagate": True},
            "uvicorn": {"level": "INFO", "propagate": True},
            "websockets": {"level": "INFO", "propagate": True},  # Slightly more verbose for WebSocket debugging
        },
        "root": {
            "level": log_level,
            "handlers": []
        }
    }

    # Add console handler if enabled
    if enable_console:
        config["handlers"]["console"] = {
            "class": "logging.StreamHandler",
            "level": log_level,
            "formatter": "simple",
            "stream": "ext://sys.stdout"
        }
        config["root"]["handlers"].append("console")

    # Add file handlers if enabled
    if enable_file:
        # Main application log
        config["handlers"]["file_app"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": log_level,
            "formatter": "detailed",
            "filename": f"{log_dir}/app.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
            "encoding": "utf8"
        }

        # Error-only log
        config["handlers"]["file_error"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "ERROR",
            "formatter": "detailed",
            "filename": f"{log_dir}/error.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
            "encoding": "utf8"
        }

        # Claude Code SDK log
        config["handlers"]["file_claude"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "detailed",
            "filename": f"{log_dir}/claude_sdk.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 10,
            "encoding": "utf8"
        }

        # Specialized SDK lifecycle debugging log
        config["handlers"]["file_sdk_lifecycle"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "detailed",
            "filename": f"{log_dir}/sdk_lifecycle_debug.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
            "encoding": "utf8"
        }

        # WebSocket lifecycle debugging log
        config["handlers"]["file_websocket_lifecycle"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "detailed",
            "filename": f"{log_dir}/websocket_lifecycle_debug.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
            "encoding": "utf8"
        }

        config["root"]["handlers"].extend(["file_app", "file_error"])
        config["loggers"]["claude_sdk"]["handlers"] = ["file_claude", "file_sdk_lifecycle"]
        config["loggers"]["web_server"]["handlers"] = ["file_websocket_lifecycle"]

    # Apply configuration
    logging.config.dictConfig(config)

    # Log startup message
    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging configured - Level: {log_level}, "
        f"Console: {enable_console}, File: {enable_file}"
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for the specified module.

    Args:
        name: Logger name (typically __name__ from calling module)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


def set_module_log_level(module_name: str, level: str) -> None:
    """
    Dynamically adjust log level for a specific module.

    Args:
        module_name: Name of the module logger
        level: New log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    logger = logging.getLogger(module_name)
    logger.setLevel(getattr(logging, level.upper()))

    main_logger = logging.getLogger(__name__)
    main_logger.info(f"Set {module_name} log level to {level}")