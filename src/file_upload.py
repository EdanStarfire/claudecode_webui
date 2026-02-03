"""
File Upload Management for Claude Code WebUI

Handles file validation, storage, and management for message attachments.
Files are stored in session-specific directories and paths are passed to Claude
for reading via the Read tool.
"""

import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from .logging_config import get_logger

# Get specialized logger for file upload operations
upload_logger = get_logger('file_upload', category='FILE_UPLOAD')
logger = logging.getLogger(__name__)

# File size limit: 5MB per file
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_FILE_SIZE_MB = 5

# Allowed file extensions (text, code, images, logs, config)
ALLOWED_EXTENSIONS = {
    # Text files
    '.txt', '.log', '.md', '.rst', '.csv', '.tsv',
    # Code files
    '.py', '.js', '.ts', '.jsx', '.tsx', '.vue', '.html', '.css', '.scss', '.sass',
    '.java', '.c', '.cpp', '.h', '.hpp', '.cs', '.go', '.rs', '.rb', '.php',
    '.swift', '.kt', '.scala', '.sh', '.bash', '.zsh', '.ps1', '.bat', '.cmd',
    # Config files
    '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env', '.properties',
    '.xml', '.plist',
    # Image files
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico',
    # Data files
    '.sql', '.graphql',
}

# MIME type mapping for common extensions
MIME_TYPES = {
    # Text
    '.txt': 'text/plain',
    '.log': 'text/plain',
    '.md': 'text/markdown',
    '.csv': 'text/csv',
    # Code
    '.py': 'text/x-python',
    '.js': 'text/javascript',
    '.ts': 'text/typescript',
    '.html': 'text/html',
    '.css': 'text/css',
    '.json': 'application/json',
    '.yaml': 'text/yaml',
    '.yml': 'text/yaml',
    '.xml': 'application/xml',
    # Images
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
}


@dataclass
class UploadedFileInfo:
    """Information about an uploaded file"""
    file_id: str
    original_name: str
    stored_name: str
    stored_path: str
    size_bytes: int
    mime_type: str
    uploaded_at: datetime

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'file_id': self.file_id,
            'original_name': self.original_name,
            'stored_name': self.stored_name,
            'stored_path': self.stored_path,
            'size_bytes': self.size_bytes,
            'mime_type': self.mime_type,
            'uploaded_at': self.uploaded_at.isoformat(),
        }


class FileUploadError(Exception):
    """Custom exception for file upload errors"""

    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class FileUploadManager:
    """
    Manages file uploads for session attachments.

    Files are stored in: data/sessions/{session-id}/attachments/
    Each file gets a unique ID to prevent naming conflicts.
    """

    def __init__(self, sessions_dir: Path):
        """
        Initialize file upload manager.

        Args:
            sessions_dir: Base directory for all sessions (e.g., data/sessions)
        """
        self.sessions_dir = Path(sessions_dir)

    def _get_attachments_dir(self, session_id: str) -> Path:
        """Get the attachments directory for a session"""
        return self.sessions_dir / session_id / "attachments"

    def _ensure_attachments_dir(self, session_id: str) -> Path:
        """Ensure attachments directory exists for a session"""
        attachments_dir = self._get_attachments_dir(session_id)
        attachments_dir.mkdir(parents=True, exist_ok=True)
        return attachments_dir

    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename to prevent path traversal and other attacks.

        Args:
            filename: Original filename from upload

        Returns:
            Sanitized filename safe for filesystem storage
        """
        # Remove any path components (prevent path traversal)
        filename = os.path.basename(filename)

        # Remove leading/trailing whitespace and dots
        filename = filename.strip().strip('.')

        # Replace potentially dangerous characters
        # Allow only alphanumeric, dots, underscores, hyphens
        filename = re.sub(r'[^\w.\-]', '_', filename)

        # Ensure we have a filename
        if not filename:
            filename = 'unnamed_file'

        # Limit length (keep extension intact)
        max_name_length = 100
        if len(filename) > max_name_length:
            name, ext = os.path.splitext(filename)
            name = name[:max_name_length - len(ext)]
            filename = name + ext

        return filename

    def _get_mime_type(self, filename: str) -> str:
        """Get MIME type for a file based on extension"""
        ext = os.path.splitext(filename)[1].lower()
        return MIME_TYPES.get(ext, 'application/octet-stream')

    def _validate_file_extension(self, filename: str) -> None:
        """
        Validate that file has an allowed extension.

        Args:
            filename: Name of the file to validate

        Raises:
            FileUploadError: If extension is not allowed
        """
        ext = os.path.splitext(filename)[1].lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            raise FileUploadError(
                f"File type '{ext}' is not supported. Allowed types: text, code, images, config files.",
                'UNSUPPORTED_FILE_TYPE'
            )

    def _validate_file_size(self, size_bytes: int) -> None:
        """
        Validate that file size is within limits.

        Args:
            size_bytes: Size of file in bytes

        Raises:
            FileUploadError: If file exceeds size limit
        """
        if size_bytes > MAX_FILE_SIZE_BYTES:
            size_mb = size_bytes / (1024 * 1024)
            raise FileUploadError(
                f"File size ({size_mb:.1f}MB) exceeds maximum allowed size ({MAX_FILE_SIZE_MB}MB).",
                'FILE_TOO_LARGE'
            )

    async def upload_file(
        self,
        session_id: str,
        filename: str,
        file_data: bytes | BinaryIO,
        content_type: str | None = None
    ) -> UploadedFileInfo:
        """
        Upload a file for a session.

        Args:
            session_id: ID of the session to upload to
            filename: Original filename
            file_data: File content as bytes or file-like object
            content_type: Optional MIME type (auto-detected if not provided)

        Returns:
            UploadedFileInfo with metadata about the stored file

        Raises:
            FileUploadError: If validation fails
        """
        # Read file data if it's a file-like object
        if hasattr(file_data, 'read'):
            file_bytes = file_data.read()
        else:
            file_bytes = file_data

        # Validate file size
        self._validate_file_size(len(file_bytes))

        # Sanitize filename
        safe_filename = self._sanitize_filename(filename)

        # Validate extension
        self._validate_file_extension(safe_filename)

        # Generate unique file ID
        file_id = str(uuid.uuid4())[:8]

        # Create stored filename with ID prefix to avoid conflicts
        name, ext = os.path.splitext(safe_filename)
        stored_name = f"{file_id}_{safe_filename}"

        # Ensure attachments directory exists
        attachments_dir = self._ensure_attachments_dir(session_id)

        # Write file
        stored_path = attachments_dir / stored_name
        try:
            stored_path.write_bytes(file_bytes)
        except Exception as e:
            logger.error(f"Failed to write file {stored_name}: {e}")
            raise FileUploadError(
                "Failed to save uploaded file. Please try again.",
                'STORAGE_ERROR'
            ) from e

        # Determine MIME type
        mime_type = content_type or self._get_mime_type(safe_filename)

        # Create file info
        file_info = UploadedFileInfo(
            file_id=file_id,
            original_name=filename,
            stored_name=stored_name,
            stored_path=str(stored_path.absolute()),
            size_bytes=len(file_bytes),
            mime_type=mime_type,
            uploaded_at=datetime.now(UTC)
        )

        upload_logger.info(
            f"Uploaded file for session {session_id}: {filename} -> {stored_name} "
            f"({len(file_bytes)} bytes)"
        )

        return file_info

    async def delete_file(self, session_id: str, file_id: str) -> bool:
        """
        Delete an uploaded file.

        Args:
            session_id: ID of the session
            file_id: ID of the file to delete

        Returns:
            True if file was deleted, False if not found
        """
        attachments_dir = self._get_attachments_dir(session_id)
        if not attachments_dir.exists():
            return False

        # Find file with matching ID prefix
        for file_path in attachments_dir.iterdir():
            if file_path.name.startswith(f"{file_id}_"):
                try:
                    file_path.unlink()
                    upload_logger.info(f"Deleted file {file_id} from session {session_id}")
                    return True
                except Exception as e:
                    logger.error(f"Failed to delete file {file_id}: {e}")
                    return False

        return False

    async def get_file_info(self, session_id: str, file_id: str) -> UploadedFileInfo | None:
        """
        Get information about an uploaded file.

        Args:
            session_id: ID of the session
            file_id: ID of the file

        Returns:
            UploadedFileInfo if found, None otherwise
        """
        attachments_dir = self._get_attachments_dir(session_id)
        if not attachments_dir.exists():
            return None

        # Find file with matching ID prefix
        for file_path in attachments_dir.iterdir():
            if file_path.name.startswith(f"{file_id}_"):
                # Extract original name (remove ID prefix)
                stored_name = file_path.name
                original_name = stored_name[len(file_id) + 1:]  # +1 for underscore

                stat = file_path.stat()
                return UploadedFileInfo(
                    file_id=file_id,
                    original_name=original_name,
                    stored_name=stored_name,
                    stored_path=str(file_path.absolute()),
                    size_bytes=stat.st_size,
                    mime_type=self._get_mime_type(original_name),
                    uploaded_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                )

        return None

    async def list_files(self, session_id: str) -> list[UploadedFileInfo]:
        """
        List all uploaded files for a session.

        Args:
            session_id: ID of the session

        Returns:
            List of UploadedFileInfo objects
        """
        attachments_dir = self._get_attachments_dir(session_id)
        if not attachments_dir.exists():
            return []

        files = []
        for file_path in attachments_dir.iterdir():
            if file_path.is_file():
                # Parse file ID from name (format: {id}_{original_name})
                name_parts = file_path.name.split('_', 1)
                if len(name_parts) == 2:
                    file_id, original_name = name_parts
                else:
                    file_id = name_parts[0]
                    original_name = file_path.name

                stat = file_path.stat()
                files.append(UploadedFileInfo(
                    file_id=file_id,
                    original_name=original_name,
                    stored_name=file_path.name,
                    stored_path=str(file_path.absolute()),
                    size_bytes=stat.st_size,
                    mime_type=self._get_mime_type(original_name),
                    uploaded_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                ))

        return files

    async def cleanup_session_attachments(self, session_id: str) -> int:
        """
        Delete all attachments for a session.

        Args:
            session_id: ID of the session

        Returns:
            Number of files deleted
        """
        attachments_dir = self._get_attachments_dir(session_id)
        if not attachments_dir.exists():
            return 0

        count = 0
        for file_path in attachments_dir.iterdir():
            if file_path.is_file():
                try:
                    file_path.unlink()
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to delete attachment {file_path}: {e}")

        # Try to remove the empty directory
        try:
            attachments_dir.rmdir()
        except OSError:
            pass  # Directory not empty or other issue

        upload_logger.info(f"Cleaned up {count} attachments for session {session_id}")
        return count
