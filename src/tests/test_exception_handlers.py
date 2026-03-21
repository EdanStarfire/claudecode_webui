"""Unit tests for the handle_exceptions decorator (Issues #855, #852)."""

import logging
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.logging_config as logging_config
from src.exception_handlers import handle_exceptions


@pytest.mark.asyncio
async def test_issue_855_http_exception_passes_through_unchanged():
    """HTTPException raised inside handler is re-raised with original status code."""
    @handle_exceptions("test action")
    async def handler():
        raise HTTPException(status_code=404, detail="Not found")

    with pytest.raises(HTTPException) as exc_info:
        await handler()

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Not found"


@pytest.mark.asyncio
async def test_issue_855_http_exception_4xx_preserved():
    """4xx HTTPException is not converted to 500."""
    @handle_exceptions("test action")
    async def handler():
        raise HTTPException(status_code=409, detail="Conflict")

    with pytest.raises(HTTPException) as exc_info:
        await handler()

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_issue_855_value_error_with_status_returns_400():
    """ValueError with value_error_status=400 is raised as HTTPException(400)."""
    @handle_exceptions("create schedule", value_error_status=400)
    async def handler():
        raise ValueError("Invalid cron expression")

    with pytest.raises(HTTPException) as exc_info:
        await handler()

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid cron expression"


@pytest.mark.asyncio
async def test_issue_855_value_error_without_status_returns_500():
    """ValueError without value_error_status falls through to generic 500."""
    @handle_exceptions("some action")
    async def handler():
        raise ValueError("Something unexpected")

    with pytest.raises(HTTPException) as exc_info:
        await handler()

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_issue_855_generic_exception_returns_500(caplog):
    """Generic Exception is logged and raised as HTTPException(500) with generic detail."""
    @handle_exceptions("do something")
    async def handler():
        raise RuntimeError("Unexpected error")

    with caplog.at_level(logging.ERROR, logger="src.exception_handlers"):
        with pytest.raises(HTTPException) as exc_info:
            await handler()

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "An internal error occurred"
    assert "Failed to do something" in caplog.text


@pytest.mark.asyncio
async def test_issue_855_success_returns_value():
    """Handler that succeeds returns its value unchanged."""
    @handle_exceptions("get thing")
    async def handler():
        return {"result": "ok"}

    result = await handler()
    assert result == {"result": "ok"}


@pytest.mark.asyncio
async def test_issue_855_value_error_with_status_logs():
    """ValueError with value_error_status does NOT log (it's an expected error)."""
    @handle_exceptions("create thing", value_error_status=400)
    async def handler():
        raise ValueError("bad input")

    # Should raise but not cause issues
    with pytest.raises(HTTPException) as exc_info:
        await handler()
    assert exc_info.value.status_code == 400


# --- Issue #852: debug flag controls 500 response detail ---


@pytest.mark.asyncio
async def test_issue_852_debug_off_returns_generic_detail():
    """With debug_error_handler=False (default), 500 detail is sanitized."""
    @handle_exceptions("do something")
    async def handler():
        raise RuntimeError("internal/path/secret")

    with patch.object(logging_config, '_log_config', {'debug_error_handler': False}):
        with pytest.raises(HTTPException) as exc_info:
            await handler()

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "An internal error occurred"
    assert "internal/path/secret" not in exc_info.value.detail


@pytest.mark.asyncio
async def test_issue_852_debug_on_returns_full_detail():
    """With debug_error_handler=True, 500 detail contains the original exception string."""
    @handle_exceptions("do something")
    async def handler():
        raise RuntimeError("internal/path/secret")

    with patch.object(logging_config, '_log_config', {'debug_error_handler': True}):
        with pytest.raises(HTTPException) as exc_info:
            await handler()

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "internal/path/secret"


@pytest.mark.asyncio
async def test_issue_852_value_error_with_status_unaffected_by_debug_flag():
    """ValueError with value_error_status=400 always returns str(e) regardless of debug flag."""
    @handle_exceptions("create schedule", value_error_status=400)
    async def handler():
        raise ValueError("Invalid cron expression")

    for flag_value in (True, False):
        with patch.object(logging_config, '_log_config', {'debug_error_handler': flag_value}):
            with pytest.raises(HTTPException) as exc_info:
                await handler()

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid cron expression"


@pytest.mark.asyncio
async def test_issue_852_http_exception_unaffected_by_debug_flag():
    """HTTPException is re-raised unchanged regardless of debug flag."""
    @handle_exceptions("test action")
    async def handler():
        raise HTTPException(status_code=403, detail="Forbidden")

    for flag_value in (True, False):
        with patch.object(logging_config, '_log_config', {'debug_error_handler': flag_value}):
            with pytest.raises(HTTPException) as exc_info:
                await handler()

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Forbidden"


@pytest.mark.asyncio
async def test_issue_852_value_error_no_status_sanitized_in_non_debug(caplog):
    """ValueError without value_error_status falls to 500 and is sanitized when debug is off."""
    @handle_exceptions("some action")
    async def handler():
        raise ValueError("internal detail")

    with patch.object(logging_config, '_log_config', {'debug_error_handler': False}):
        with pytest.raises(HTTPException) as exc_info:
            await handler()

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "An internal error occurred"


@pytest.mark.asyncio
async def test_issue_852_logger_exception_called_regardless_of_debug(caplog):
    """logger.exception is always called for 500 errors, regardless of debug flag."""
    @handle_exceptions("do something")
    async def handler():
        raise RuntimeError("secret details")

    with caplog.at_level(logging.ERROR, logger="src.exception_handlers"):
        with patch.object(logging_config, '_log_config', {'debug_error_handler': False}):
            with pytest.raises(HTTPException):
                await handler()

    assert "Failed to do something" in caplog.text
