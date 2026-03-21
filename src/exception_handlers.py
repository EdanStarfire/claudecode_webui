"""
Centralized exception handling decorator for FastAPI route handlers.
"""

import functools
import logging

from fastapi import HTTPException

from src.logging_config import get_debug_flag

logger = logging.getLogger(__name__)


def handle_exceptions(action: str, *, value_error_status: int | None = None):
    """
    Decorator for FastAPI route handlers that centralizes exception handling.

    - HTTPException is re-raised unchanged (preserves 4xx codes)
    - If value_error_status is provided, ValueError is caught and raised as
      HTTPException with that status code (typically 400)
    - All other exceptions are logged and raised as HTTPException 500

    Usage:
        @handle_exceptions("create session")
        async def create_session(...):
            ...

        @handle_exceptions("create schedule", value_error_status=400)
        async def create_schedule(...):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                raise
            except ValueError as e:
                if value_error_status is not None:
                    raise HTTPException(status_code=value_error_status, detail=str(e)) from e
                logger.exception("Failed to %s", action)
                detail = str(e) if get_debug_flag('debug_error_handler') else "An internal error occurred"
                raise HTTPException(status_code=500, detail=detail) from e
            except Exception as e:
                logger.exception("Failed to %s", action)
                detail = str(e) if get_debug_flag('debug_error_handler') else "An internal error occurred"
                raise HTTPException(status_code=500, detail=detail) from e
        return wrapper
    return decorator
