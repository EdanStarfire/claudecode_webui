"""
Comprehensive Error Handling System for Claude Code WebUI

Provides centralized error detection, reporting, and recovery mechanisms
for all components in the system.
"""

import asyncio
import traceback
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """Error categories for classification"""
    SDK_ERROR = "sdk_error"
    SESSION_ERROR = "session_error"
    STORAGE_ERROR = "storage_error"
    NETWORK_ERROR = "network_error"
    VALIDATION_ERROR = "validation_error"
    SYSTEM_ERROR = "system_error"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class ErrorReport:
    """Comprehensive error report"""
    error_id: str
    session_id: Optional[str]
    category: ErrorCategory
    severity: ErrorSeverity
    message: str
    exception_type: str
    exception_details: str
    stack_trace: str
    context: Dict[str, Any]
    timestamp: datetime
    resolved: bool = False
    resolution_notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        data = asdict(self)
        data['category'] = self.category.value
        data['severity'] = self.severity.value
        data['timestamp'] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ErrorReport':
        """Create from dictionary"""
        data['category'] = ErrorCategory(data['category'])
        data['severity'] = ErrorSeverity(data['severity'])
        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


class ErrorHandler:
    """Centralized error handling and reporting system"""

    def __init__(self):
        self._error_reports: Dict[str, ErrorReport] = {}
        self._error_callbacks: List[Callable] = []
        self._recovery_handlers: Dict[ErrorCategory, List[Callable]] = {}
        self._error_counter = 0

    def register_error_callback(self, callback: Callable):
        """Register callback for error notifications"""
        self._error_callbacks.append(callback)

    def register_recovery_handler(self, category: ErrorCategory, handler: Callable):
        """Register recovery handler for specific error category"""
        if category not in self._recovery_handlers:
            self._recovery_handlers[category] = []
        self._recovery_handlers[category].append(handler)

    async def handle_exception(
        self,
        exception: Exception,
        session_id: Optional[str] = None,
        context: Dict[str, Any] = None,
        category: ErrorCategory = ErrorCategory.UNKNOWN_ERROR
    ) -> str:
        """Handle an exception and create error report"""
        try:
            self._error_counter += 1
            error_id = f"error_{self._error_counter:06d}"

            # Classify error and determine severity
            category, severity = self._classify_error(exception, category)

            # Create error report
            error_report = ErrorReport(
                error_id=error_id,
                session_id=session_id,
                category=category,
                severity=severity,
                message=str(exception),
                exception_type=type(exception).__name__,
                exception_details=self._extract_exception_details(exception),
                stack_trace=traceback.format_exc(),
                context=context or {},
                timestamp=datetime.now(timezone.utc)
            )

            # Store error report
            self._error_reports[error_id] = error_report

            # Log error
            log_level = self._get_log_level(severity)
            logger.log(log_level, f"Error {error_id}: {error_report.message}")
            logger.debug(f"Error details {error_id}: {error_report.exception_details}")

            # Notify callbacks
            await self._notify_error_callbacks(error_report)

            # Attempt recovery
            await self._attempt_recovery(error_report)

            return error_id

        except Exception as e:
            logger.critical(f"Error in error handler itself: {e}")
            return "error_handler_failure"

    def _classify_error(self, exception: Exception, hint_category: ErrorCategory) -> tuple[ErrorCategory, ErrorSeverity]:
        """Classify error based on exception type and context"""
        exception_type = type(exception).__name__

        # SDK-related errors
        if "claude_code" in str(exception).lower() or "sdk" in str(exception).lower():
            category = ErrorCategory.SDK_ERROR
            if "permission" in str(exception).lower():
                severity = ErrorSeverity.MEDIUM
            elif "authentication" in str(exception).lower():
                severity = ErrorSeverity.HIGH
            else:
                severity = ErrorSeverity.MEDIUM

        # Asyncio errors (check before storage errors since TimeoutError might inherit from OSError)
        elif isinstance(exception, (asyncio.TimeoutError,)):
            category = ErrorCategory.SYSTEM_ERROR
            severity = ErrorSeverity.MEDIUM
        elif isinstance(exception, TimeoutError):
            category = ErrorCategory.SYSTEM_ERROR
            severity = ErrorSeverity.MEDIUM

        # Storage errors
        elif isinstance(exception, (OSError, IOError, PermissionError)):
            category = ErrorCategory.STORAGE_ERROR
            if isinstance(exception, PermissionError):
                severity = ErrorSeverity.HIGH
            else:
                severity = ErrorSeverity.MEDIUM

        # Network errors
        elif "connection" in str(exception).lower() or "network" in str(exception).lower():
            category = ErrorCategory.NETWORK_ERROR
            severity = ErrorSeverity.MEDIUM

        # Validation errors
        elif isinstance(exception, (ValueError, TypeError)):
            category = ErrorCategory.VALIDATION_ERROR
            severity = ErrorSeverity.LOW

        # System errors
        elif isinstance(exception, (MemoryError, SystemError)):
            category = ErrorCategory.SYSTEM_ERROR
            severity = ErrorSeverity.CRITICAL

        # Use hint if no specific classification found
        else:
            category = hint_category
            severity = ErrorSeverity.MEDIUM

        return category, severity

    def _extract_exception_details(self, exception: Exception) -> str:
        """Extract detailed information from exception"""
        details = []
        details.append(f"Type: {type(exception).__name__}")
        details.append(f"Message: {str(exception)}")

        # Add exception-specific details
        if hasattr(exception, 'args') and exception.args:
            details.append(f"Args: {exception.args}")

        if hasattr(exception, 'errno'):
            details.append(f"Error Code: {exception.errno}")

        if hasattr(exception, 'filename'):
            details.append(f"Filename: {exception.filename}")

        return "\n".join(details)

    def _get_log_level(self, severity: ErrorSeverity) -> int:
        """Get logging level for severity"""
        severity_map = {
            ErrorSeverity.LOW: logging.INFO,
            ErrorSeverity.MEDIUM: logging.WARNING,
            ErrorSeverity.HIGH: logging.ERROR,
            ErrorSeverity.CRITICAL: logging.CRITICAL
        }
        return severity_map.get(severity, logging.WARNING)

    async def _notify_error_callbacks(self, error_report: ErrorReport):
        """Notify registered error callbacks"""
        for callback in self._error_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(error_report)
                else:
                    callback(error_report)
            except Exception as e:
                logger.error(f"Error in error callback: {e}")

    async def _attempt_recovery(self, error_report: ErrorReport):
        """Attempt automatic recovery for the error"""
        handlers = self._recovery_handlers.get(error_report.category, [])

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    success = await handler(error_report)
                else:
                    success = handler(error_report)

                if success:
                    error_report.resolved = True
                    handler_name = getattr(handler, '__name__', 'unknown_handler')
                    error_report.resolution_notes = f"Recovered by {handler_name}"
                    logger.info(f"Error {error_report.error_id} recovered by {handler_name}")
                    break

            except Exception as e:
                handler_name = getattr(handler, '__name__', 'unknown_handler')
                logger.error(f"Error in recovery handler {handler_name}: {e}")

    async def handle_sdk_exception(
        self,
        exception: Exception,
        session_id: str,
        operation: str,
        context: Dict[str, Any] = None
    ) -> str:
        """Handle SDK-specific exceptions with enhanced context"""
        enhanced_context = {
            "operation": operation,
            "sdk_version": "unknown",  # Could be retrieved from SDK
            **(context or {})
        }

        # Create error report manually for SDK errors to ensure correct category
        try:
            self._error_counter += 1
            error_id = f"error_{self._error_counter:06d}"

            # Force SDK category
            category = ErrorCategory.SDK_ERROR
            severity = ErrorSeverity.MEDIUM

            # Create error report
            error_report = ErrorReport(
                error_id=error_id,
                session_id=session_id,
                category=category,
                severity=severity,
                message=str(exception),
                exception_type=type(exception).__name__,
                exception_details=self._extract_exception_details(exception),
                stack_trace=traceback.format_exc(),
                context=enhanced_context,
                timestamp=datetime.now(timezone.utc)
            )

            # Store error report
            self._error_reports[error_id] = error_report

            # Log error
            log_level = self._get_log_level(severity)
            logger.log(log_level, f"SDK Error {error_id}: {error_report.message}")

            # Notify callbacks
            await self._notify_error_callbacks(error_report)

            return error_id

        except Exception as e:
            logger.critical(f"Error in SDK error handler: {e}")
            return "sdk_error_handler_failure"

    async def handle_storage_exception(
        self,
        exception: Exception,
        session_id: str,
        file_path: str,
        operation: str,
        context: Dict[str, Any] = None
    ) -> str:
        """Handle storage-specific exceptions"""
        enhanced_context = {
            "file_path": file_path,
            "operation": operation,
            **(context or {})
        }

        # Create error report manually for storage errors to ensure correct category
        try:
            self._error_counter += 1
            error_id = f"error_{self._error_counter:06d}"

            # Force storage category
            category = ErrorCategory.STORAGE_ERROR
            severity = ErrorSeverity.MEDIUM

            # Create error report
            error_report = ErrorReport(
                error_id=error_id,
                session_id=session_id,
                category=category,
                severity=severity,
                message=str(exception),
                exception_type=type(exception).__name__,
                exception_details=self._extract_exception_details(exception),
                stack_trace=traceback.format_exc(),
                context=enhanced_context,
                timestamp=datetime.now(timezone.utc)
            )

            # Store error report
            self._error_reports[error_id] = error_report

            # Log error
            log_level = self._get_log_level(severity)
            logger.log(log_level, f"Storage Error {error_id}: {error_report.message}")

            # Notify callbacks
            await self._notify_error_callbacks(error_report)

            return error_id

        except Exception as e:
            logger.critical(f"Error in storage error handler: {e}")
            return "storage_error_handler_failure"

    async def handle_session_exception(
        self,
        exception: Exception,
        session_id: str,
        session_state: str,
        context: Dict[str, Any] = None
    ) -> str:
        """Handle session management exceptions"""
        enhanced_context = {
            "session_state": session_state,
            **(context or {})
        }

        # Create error report manually for session errors to ensure correct category
        try:
            self._error_counter += 1
            error_id = f"error_{self._error_counter:06d}"

            # Force session category
            category = ErrorCategory.SESSION_ERROR
            severity = ErrorSeverity.MEDIUM

            # Create error report
            error_report = ErrorReport(
                error_id=error_id,
                session_id=session_id,
                category=category,
                severity=severity,
                message=str(exception),
                exception_type=type(exception).__name__,
                exception_details=self._extract_exception_details(exception),
                stack_trace=traceback.format_exc(),
                context=enhanced_context,
                timestamp=datetime.now(timezone.utc)
            )

            # Store error report
            self._error_reports[error_id] = error_report

            # Log error
            log_level = self._get_log_level(severity)
            logger.log(log_level, f"Session Error {error_id}: {error_report.message}")

            # Notify callbacks
            await self._notify_error_callbacks(error_report)

            return error_id

        except Exception as e:
            logger.critical(f"Error in session error handler: {e}")
            return "session_error_handler_failure"

    def get_error_report(self, error_id: str) -> Optional[ErrorReport]:
        """Get error report by ID"""
        return self._error_reports.get(error_id)

    def list_error_reports(
        self,
        session_id: Optional[str] = None,
        category: Optional[ErrorCategory] = None,
        severity: Optional[ErrorSeverity] = None,
        resolved: Optional[bool] = None
    ) -> List[ErrorReport]:
        """List error reports with filtering"""
        reports = list(self._error_reports.values())

        if session_id:
            reports = [r for r in reports if r.session_id == session_id]

        if category:
            reports = [r for r in reports if r.category == category]

        if severity:
            reports = [r for r in reports if r.severity == severity]

        if resolved is not None:
            reports = [r for r in reports if r.resolved == resolved]

        # Sort by timestamp (newest first)
        reports.sort(key=lambda r: r.timestamp, reverse=True)
        return reports

    def get_error_statistics(self) -> Dict[str, Any]:
        """Get error statistics"""
        total_errors = len(self._error_reports)
        resolved_errors = sum(1 for r in self._error_reports.values() if r.resolved)

        # Count by category
        category_counts = {}
        for report in self._error_reports.values():
            category = report.category.value
            category_counts[category] = category_counts.get(category, 0) + 1

        # Count by severity
        severity_counts = {}
        for report in self._error_reports.values():
            severity = report.severity.value
            severity_counts[severity] = severity_counts.get(severity, 0) + 1

        return {
            "total_errors": total_errors,
            "resolved_errors": resolved_errors,
            "unresolved_errors": total_errors - resolved_errors,
            "resolution_rate": resolved_errors / total_errors if total_errors > 0 else 0,
            "category_breakdown": category_counts,
            "severity_breakdown": severity_counts
        }

    def clear_resolved_errors(self):
        """Clear resolved error reports to free memory"""
        self._error_reports = {
            error_id: report
            for error_id, report in self._error_reports.items()
            if not report.resolved
        }
        logger.info("Cleared resolved error reports")


# Global error handler instance
global_error_handler = ErrorHandler()