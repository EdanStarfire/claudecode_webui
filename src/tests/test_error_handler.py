"""Tests for error_handler module."""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

from ..error_handler import (
    ErrorHandler, ErrorReport, ErrorSeverity, ErrorCategory,
    global_error_handler
)


@pytest.fixture
def error_handler():
    """Create a fresh error handler for testing."""
    return ErrorHandler()


@pytest.fixture
def sample_exception():
    """Sample exception for testing."""
    return ValueError("Test error message")


@pytest.fixture
def sample_context():
    """Sample context data for testing."""
    return {
        "operation": "test_operation",
        "user_id": "test_user",
        "additional_info": "test data"
    }


class TestErrorSeverity:
    """Test ErrorSeverity enum."""

    def test_error_severity_values(self):
        """Test ErrorSeverity enum values."""
        assert ErrorSeverity.LOW.value == "low"
        assert ErrorSeverity.MEDIUM.value == "medium"
        assert ErrorSeverity.HIGH.value == "high"
        assert ErrorSeverity.CRITICAL.value == "critical"


class TestErrorCategory:
    """Test ErrorCategory enum."""

    def test_error_category_values(self):
        """Test ErrorCategory enum values."""
        assert ErrorCategory.SDK_ERROR.value == "sdk_error"
        assert ErrorCategory.SESSION_ERROR.value == "session_error"
        assert ErrorCategory.STORAGE_ERROR.value == "storage_error"
        assert ErrorCategory.NETWORK_ERROR.value == "network_error"
        assert ErrorCategory.VALIDATION_ERROR.value == "validation_error"
        assert ErrorCategory.SYSTEM_ERROR.value == "system_error"
        assert ErrorCategory.UNKNOWN_ERROR.value == "unknown_error"


class TestErrorReport:
    """Test ErrorReport dataclass."""

    def test_error_report_creation(self):
        """Test ErrorReport creation."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        report = ErrorReport(
            error_id="test_error_001",
            session_id="test_session",
            category=ErrorCategory.VALIDATION_ERROR,
            severity=ErrorSeverity.MEDIUM,
            message="Test error",
            exception_type="ValueError",
            exception_details="Type: ValueError\nMessage: Test error",
            stack_trace="Traceback...",
            context={"key": "value"},
            timestamp=now
        )

        assert report.error_id == "test_error_001"
        assert report.session_id == "test_session"
        assert report.category == ErrorCategory.VALIDATION_ERROR
        assert report.severity == ErrorSeverity.MEDIUM
        assert report.resolved is False

    def test_error_report_to_dict(self):
        """Test ErrorReport to_dict conversion."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        report = ErrorReport(
            error_id="test_error_001",
            session_id="test_session",
            category=ErrorCategory.VALIDATION_ERROR,
            severity=ErrorSeverity.MEDIUM,
            message="Test error",
            exception_type="ValueError",
            exception_details="Details",
            stack_trace="Stack",
            context={},
            timestamp=now
        )

        data = report.to_dict()

        assert data["error_id"] == "test_error_001"
        assert data["category"] == "validation_error"
        assert data["severity"] == "medium"
        assert data["timestamp"] == now.isoformat()

    def test_error_report_from_dict(self):
        """Test ErrorReport from_dict creation."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        data = {
            "error_id": "test_error_001",
            "session_id": "test_session",
            "category": "validation_error",
            "severity": "medium",
            "message": "Test error",
            "exception_type": "ValueError",
            "exception_details": "Details",
            "stack_trace": "Stack",
            "context": {},
            "timestamp": now.isoformat(),
            "resolved": False,
            "resolution_notes": None
        }

        report = ErrorReport.from_dict(data)

        assert report.error_id == "test_error_001"
        assert report.category == ErrorCategory.VALIDATION_ERROR
        assert report.severity == ErrorSeverity.MEDIUM
        assert report.timestamp == now


class TestErrorHandler:
    """Test ErrorHandler functionality."""

    def test_error_handler_initialization(self, error_handler):
        """Test error handler initialization."""
        assert isinstance(error_handler._error_reports, dict)
        assert isinstance(error_handler._error_callbacks, list)
        assert isinstance(error_handler._recovery_handlers, dict)
        assert error_handler._error_counter == 0

    def test_register_error_callback(self, error_handler):
        """Test registering error callbacks."""
        callback = MagicMock()
        error_handler.register_error_callback(callback)

        assert callback in error_handler._error_callbacks

    def test_register_recovery_handler(self, error_handler):
        """Test registering recovery handlers."""
        handler = MagicMock()
        category = ErrorCategory.SDK_ERROR

        error_handler.register_recovery_handler(category, handler)

        assert category in error_handler._recovery_handlers
        assert handler in error_handler._recovery_handlers[category]

    @pytest.mark.asyncio
    async def test_handle_exception_basic(self, error_handler, sample_exception, sample_context):
        """Test basic exception handling."""
        session_id = "test_session"

        error_id = await error_handler.handle_exception(
            sample_exception,
            session_id=session_id,
            context=sample_context,
            category=ErrorCategory.VALIDATION_ERROR
        )

        assert error_id is not None
        assert error_id in error_handler._error_reports

        report = error_handler._error_reports[error_id]
        assert report.session_id == session_id
        assert report.category == ErrorCategory.VALIDATION_ERROR
        assert report.message == str(sample_exception)
        assert report.context == sample_context

    @pytest.mark.asyncio
    async def test_handle_exception_with_callback(self, error_handler, sample_exception):
        """Test exception handling with callback."""
        callback = AsyncMock()
        error_handler.register_error_callback(callback)

        error_id = await error_handler.handle_exception(sample_exception)

        assert callback.called
        call_args = callback.call_args[0]
        assert isinstance(call_args[0], ErrorReport)

    @pytest.mark.asyncio
    async def test_handle_exception_with_recovery(self, error_handler, sample_exception):
        """Test exception handling with recovery."""
        recovery_handler = MagicMock(return_value=True)
        error_handler.register_recovery_handler(ErrorCategory.VALIDATION_ERROR, recovery_handler)

        error_id = await error_handler.handle_exception(
            sample_exception,
            category=ErrorCategory.VALIDATION_ERROR
        )

        assert recovery_handler.called
        report = error_handler._error_reports[error_id]
        assert report.resolved is True

    def test_classify_error_sdk_error(self, error_handler):
        """Test error classification for SDK errors."""
        exception = Exception("claude_code SDK error")
        category, severity = error_handler._classify_error(exception, ErrorCategory.UNKNOWN_ERROR)

        assert category == ErrorCategory.SDK_ERROR

    def test_classify_error_storage_error(self, error_handler):
        """Test error classification for storage errors."""
        exception = OSError("File not found")
        category, severity = error_handler._classify_error(exception, ErrorCategory.UNKNOWN_ERROR)

        assert category == ErrorCategory.STORAGE_ERROR

    def test_classify_error_permission_error(self, error_handler):
        """Test error classification for permission errors."""
        exception = PermissionError("Access denied")
        category, severity = error_handler._classify_error(exception, ErrorCategory.UNKNOWN_ERROR)

        assert category == ErrorCategory.STORAGE_ERROR
        assert severity == ErrorSeverity.HIGH

    def test_classify_error_validation_error(self, error_handler):
        """Test error classification for validation errors."""
        exception = ValueError("Invalid value")
        category, severity = error_handler._classify_error(exception, ErrorCategory.UNKNOWN_ERROR)

        assert category == ErrorCategory.VALIDATION_ERROR
        assert severity == ErrorSeverity.LOW

    def test_classify_error_system_error(self, error_handler):
        """Test error classification for system errors."""
        exception = MemoryError("Out of memory")
        category, severity = error_handler._classify_error(exception, ErrorCategory.UNKNOWN_ERROR)

        assert category == ErrorCategory.SYSTEM_ERROR
        assert severity == ErrorSeverity.CRITICAL

    def test_classify_error_timeout_error(self, error_handler):
        """Test error classification for timeout errors."""
        exception = asyncio.TimeoutError("Operation timed out")
        category, severity = error_handler._classify_error(exception, ErrorCategory.UNKNOWN_ERROR)

        assert category == ErrorCategory.SYSTEM_ERROR
        assert severity == ErrorSeverity.MEDIUM

    def test_extract_exception_details(self, error_handler):
        """Test exception details extraction."""
        exception = ValueError("Test error", "additional arg")
        details = error_handler._extract_exception_details(exception)

        assert "Type: ValueError" in details
        assert "Message: ('Test error', 'additional arg')" in details
        assert "Args: ('Test error', 'additional arg')" in details

    def test_extract_exception_details_with_filename(self, error_handler):
        """Test exception details extraction with filename."""
        exception = FileNotFoundError("File not found")
        exception.filename = "/test/file.txt"

        details = error_handler._extract_exception_details(exception)

        assert "Filename: /test/file.txt" in details

    @pytest.mark.asyncio
    async def test_handle_sdk_exception(self, error_handler, sample_exception):
        """Test SDK-specific exception handling."""
        session_id = "test_session"
        operation = "query_execution"
        context = {"query": "test query"}

        error_id = await error_handler.handle_sdk_exception(
            sample_exception,
            session_id,
            operation,
            context
        )

        report = error_handler._error_reports[error_id]
        assert report.category == ErrorCategory.SDK_ERROR
        assert report.context["operation"] == operation
        assert report.context["query"] == "test query"

    @pytest.mark.asyncio
    async def test_handle_storage_exception(self, error_handler, sample_exception):
        """Test storage-specific exception handling."""
        session_id = "test_session"
        file_path = "/test/file.txt"
        operation = "read"

        error_id = await error_handler.handle_storage_exception(
            sample_exception,
            session_id,
            file_path,
            operation
        )

        report = error_handler._error_reports[error_id]
        assert report.category == ErrorCategory.STORAGE_ERROR
        assert report.context["file_path"] == file_path
        assert report.context["operation"] == operation

    @pytest.mark.asyncio
    async def test_handle_session_exception(self, error_handler, sample_exception):
        """Test session-specific exception handling."""
        session_id = "test_session"
        session_state = "starting"

        error_id = await error_handler.handle_session_exception(
            sample_exception,
            session_id,
            session_state
        )

        report = error_handler._error_reports[error_id]
        assert report.category == ErrorCategory.SESSION_ERROR
        assert report.context["session_state"] == session_state

    def test_get_error_report(self, error_handler):
        """Test getting error report by ID."""
        from datetime import datetime, timezone

        report = ErrorReport(
            error_id="test_001",
            session_id="session_001",
            category=ErrorCategory.VALIDATION_ERROR,
            severity=ErrorSeverity.LOW,
            message="Test error",
            exception_type="ValueError",
            exception_details="Details",
            stack_trace="Stack",
            context={},
            timestamp=datetime.now(timezone.utc)
        )

        error_handler._error_reports["test_001"] = report

        retrieved_report = error_handler.get_error_report("test_001")
        assert retrieved_report == report

        # Test non-existent report
        assert error_handler.get_error_report("nonexistent") is None

    def test_list_error_reports_no_filter(self, error_handler):
        """Test listing error reports without filters."""
        from datetime import datetime, timezone

        reports = []
        for i in range(3):
            report = ErrorReport(
                error_id=f"test_{i:03d}",
                session_id=f"session_{i}",
                category=ErrorCategory.VALIDATION_ERROR,
                severity=ErrorSeverity.LOW,
                message=f"Test error {i}",
                exception_type="ValueError",
                exception_details="Details",
                stack_trace="Stack",
                context={},
                timestamp=datetime.now(timezone.utc)
            )
            reports.append(report)
            error_handler._error_reports[report.error_id] = report

        listed_reports = error_handler.list_error_reports()
        assert len(listed_reports) == 3

    def test_list_error_reports_with_filters(self, error_handler):
        """Test listing error reports with filters."""
        from datetime import datetime, timezone

        # Create reports with different properties
        report1 = ErrorReport(
            error_id="test_001",
            session_id="session_1",
            category=ErrorCategory.SDK_ERROR,
            severity=ErrorSeverity.HIGH,
            message="SDK error",
            exception_type="Exception",
            exception_details="Details",
            stack_trace="Stack",
            context={},
            timestamp=datetime.now(timezone.utc),
            resolved=False
        )

        report2 = ErrorReport(
            error_id="test_002",
            session_id="session_2",
            category=ErrorCategory.STORAGE_ERROR,
            severity=ErrorSeverity.LOW,
            message="Storage error",
            exception_type="OSError",
            exception_details="Details",
            stack_trace="Stack",
            context={},
            timestamp=datetime.now(timezone.utc),
            resolved=True
        )

        error_handler._error_reports["test_001"] = report1
        error_handler._error_reports["test_002"] = report2

        # Filter by session
        filtered = error_handler.list_error_reports(session_id="session_1")
        assert len(filtered) == 1
        assert filtered[0].session_id == "session_1"

        # Filter by category
        filtered = error_handler.list_error_reports(category=ErrorCategory.SDK_ERROR)
        assert len(filtered) == 1
        assert filtered[0].category == ErrorCategory.SDK_ERROR

        # Filter by severity
        filtered = error_handler.list_error_reports(severity=ErrorSeverity.HIGH)
        assert len(filtered) == 1
        assert filtered[0].severity == ErrorSeverity.HIGH

        # Filter by resolved status
        filtered = error_handler.list_error_reports(resolved=True)
        assert len(filtered) == 1
        assert filtered[0].resolved is True

    def test_get_error_statistics(self, error_handler):
        """Test getting error statistics."""
        from datetime import datetime, timezone

        # Create some test reports
        for i in range(5):
            resolved = i < 3  # First 3 are resolved
            category = ErrorCategory.SDK_ERROR if i < 2 else ErrorCategory.STORAGE_ERROR
            severity = ErrorSeverity.HIGH if i < 1 else ErrorSeverity.LOW

            report = ErrorReport(
                error_id=f"test_{i:03d}",
                session_id=f"session_{i}",
                category=category,
                severity=severity,
                message=f"Test error {i}",
                exception_type="Exception",
                exception_details="Details",
                stack_trace="Stack",
                context={},
                timestamp=datetime.now(timezone.utc),
                resolved=resolved
            )
            error_handler._error_reports[report.error_id] = report

        stats = error_handler.get_error_statistics()

        assert stats["total_errors"] == 5
        assert stats["resolved_errors"] == 3
        assert stats["unresolved_errors"] == 2
        assert stats["resolution_rate"] == 0.6
        assert stats["category_breakdown"]["sdk_error"] == 2
        assert stats["category_breakdown"]["storage_error"] == 3
        assert stats["severity_breakdown"]["high"] == 1
        assert stats["severity_breakdown"]["low"] == 4

    def test_clear_resolved_errors(self, error_handler):
        """Test clearing resolved errors."""
        from datetime import datetime, timezone

        # Create mix of resolved and unresolved reports
        for i in range(4):
            resolved = i < 2  # First 2 are resolved
            report = ErrorReport(
                error_id=f"test_{i:03d}",
                session_id=f"session_{i}",
                category=ErrorCategory.VALIDATION_ERROR,
                severity=ErrorSeverity.LOW,
                message=f"Test error {i}",
                exception_type="ValueError",
                exception_details="Details",
                stack_trace="Stack",
                context={},
                timestamp=datetime.now(timezone.utc),
                resolved=resolved
            )
            error_handler._error_reports[report.error_id] = report

        # Initially should have 4 reports
        assert len(error_handler._error_reports) == 4

        # Clear resolved errors
        error_handler.clear_resolved_errors()

        # Should have 2 unresolved reports left
        assert len(error_handler._error_reports) == 2
        for report in error_handler._error_reports.values():
            assert not report.resolved


class TestGlobalErrorHandler:
    """Test global error handler instance."""

    def test_global_error_handler_exists(self):
        """Test that global error handler instance exists."""
        assert global_error_handler is not None
        assert isinstance(global_error_handler, ErrorHandler)