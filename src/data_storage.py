"""
Data Storage System for Claude Code WebUI

Handles persistent storage of session data including activity logs,
message history, and state persistence.
"""

import gc
import json
import logging
import os
from pathlib import Path
from typing import Any

from .logging_config import get_logger
from .timestamp_utils import get_unix_timestamp

# Get specialized logger for storage debugging
storage_logger = get_logger('storage', category='STORAGE')
# Keep standard logger for errors
logger = logging.getLogger(__name__)


class DataStorageManager:
    """Manages persistent storage for session data"""

    def __init__(self, session_directory: Path):
        self.session_dir = Path(session_directory)
        self.messages_file = self.session_dir / "messages.jsonl"
        self.state_file = self.session_dir / "state.json"
        # Image storage paths (issue #404 - kept for backward compatibility)
        self.images_dir = self.session_dir / "images"
        self.images_metadata_file = self.images_dir / "images.jsonl"
        # Resource storage paths (issue #404 expansion - supports all file types)
        self.resources_dir = self.session_dir / "resources"
        self.resources_metadata_file = self.resources_dir / "resources.jsonl"

    async def initialize(self):
        """Initialize storage directory and files"""
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)

            # Initialize empty files if they don't exist
            if not self.messages_file.exists():
                self.messages_file.touch()

            # Migration: Delete legacy history.json files (no longer used)
            history_file = self.session_dir / "history.json"
            if history_file.exists():
                history_file.unlink()
                storage_logger.info(f"Deleted legacy history.json for session {self.session_dir.name}")

            storage_logger.debug(f"Initialized storage for session {self.session_dir.name}")
        except Exception as e:
            logger.error(f"Failed to initialize storage: {e}")
            raise

    async def append_message(self, message_data: dict[str, Any]):
        """Append a message to the activity log (JSONL format)"""
        try:
            # Add timestamp if not present (Unix timestamp float for consistency)
            if 'timestamp' not in message_data:
                message_data['timestamp'] = get_unix_timestamp()

            # Append to JSONL file
            with open(self.messages_file, 'a', encoding='utf-8') as f:
                json.dump(message_data, f, ensure_ascii=False)
                f.write('\n')


            storage_logger.debug(f"Appended message to {self.session_dir.name}")
        except Exception as e:
            logger.error(f"Failed to append message: {e}")
            raise

    async def read_messages(self, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        """Read messages from activity log with pagination"""
        try:
            messages = []

            if not self.messages_file.exists():
                return messages

            with open(self.messages_file, encoding='utf-8') as f:
                lines = f.readlines()

            # Apply offset and limit
            start_idx = offset
            end_idx = start_idx + limit if limit else None
            selected_lines = lines[start_idx:end_idx]

            for line in selected_lines:
                line = line.strip()
                if line:
                    try:
                        message = json.loads(line)
                        messages.append(message)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse message line: {line[:100]}... Error: {e}")

            return messages
        except Exception as e:
            logger.error(f"Failed to read messages: {e}")
            return []

    async def get_message_count(self) -> int:
        """Get total number of messages in the log"""
        try:
            if not self.messages_file.exists():
                return 0

            count = 0
            with open(self.messages_file, encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        count += 1

            return count
        except Exception as e:
            logger.error(f"Failed to count messages: {e}")
            return 0

    async def clear_messages(self) -> bool:
        """
        Clear all messages from the session.

        Truncates the messages.jsonl file to remove all message history.
        Used when resetting a session.
        """
        try:
            # Truncate messages file
            messages_path = self.messages_file
            if messages_path.exists():
                messages_path.write_text("")  # Truncate to empty
                storage_logger.info(f"Cleared all messages for session {self.session_dir.name}")

            return True

        except Exception as e:
            logger.error(f"Failed to clear messages: {e}")
            return False

    async def cleanup(self):
        """Cleanup and ensure all file handles and directory references are closed"""
        try:
            # Force garbage collection to close any lingering file handles
            gc.collect()

            # Final integrity update

            # Additional cleanup to ensure file handles are released
            # Python file handles should auto-close, but force GC again to be sure
            gc.collect()

            session_name = self.session_dir.name

            # On Windows, clear the Path object references to release directory handles
            if os.name == 'nt':  # Windows
                # Clear all path references that might hold directory handles
                self.session_dir = None
                self.messages_file = None
                self.state_file = None
                self.images_dir = None
                self.images_metadata_file = None
                self.resources_dir = None
                self.resources_metadata_file = None
                # Force another GC to clear the Path objects
                gc.collect()

            storage_logger.debug(f"Cleaned up storage for {session_name}")
        except Exception as e:
            logger.error(f"Failed to cleanup storage: {e}")
            # Still force GC even on error
            gc.collect()

    # =========================================================================
    # Image Storage Methods (Issue #404)
    # =========================================================================

    async def save_image_file(self, image_id: str, image_bytes: bytes) -> bool:
        """
        Save raw image bytes to disk.

        Args:
            image_id: Unique image identifier
            image_bytes: Raw binary image data

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Ensure images directory exists
            self.images_dir.mkdir(parents=True, exist_ok=True)

            # Save binary file
            image_path = self.images_dir / f"{image_id}.bin"
            with open(image_path, 'wb') as f:
                f.write(image_bytes)

            storage_logger.debug(
                f"Saved image {image_id} ({len(image_bytes)} bytes) "
                f"to {self.session_dir.name}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to save image file {image_id}: {e}")
            return False

    async def append_image(self, image_metadata: dict[str, Any]) -> bool:
        """
        Append image metadata to the images JSONL log.

        Args:
            image_metadata: Image metadata dict with image_id, title, etc.

        Returns:
            True if appended successfully, False otherwise
        """
        try:
            # Ensure images directory exists
            self.images_dir.mkdir(parents=True, exist_ok=True)

            # Add timestamp if not present
            if 'timestamp' not in image_metadata:
                image_metadata['timestamp'] = get_unix_timestamp()

            # Append to JSONL file
            with open(self.images_metadata_file, 'a', encoding='utf-8') as f:
                json.dump(image_metadata, f, ensure_ascii=False)
                f.write('\n')

            storage_logger.debug(
                f"Appended image metadata for {image_metadata.get('image_id', 'unknown')} "
                f"to {self.session_dir.name}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to append image metadata: {e}")
            return False

    async def read_images(self) -> list[dict[str, Any]]:
        """
        Read all image metadata from the images JSONL log.

        Returns:
            List of image metadata dicts, sorted by timestamp
        """
        try:
            images = []

            if not self.images_metadata_file.exists():
                return images

            with open(self.images_metadata_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            image = json.loads(line)
                            images.append(image)
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse image line: {e}")

            # Sort by timestamp (oldest first)
            images.sort(key=lambda x: x.get('timestamp', 0))

            return images

        except Exception as e:
            logger.error(f"Failed to read images: {e}")
            return []

    async def get_image_file(self, image_id: str) -> bytes | None:
        """
        Read raw image bytes from disk.

        Args:
            image_id: Image identifier

        Returns:
            Raw image bytes or None if not found
        """
        try:
            image_path = self.images_dir / f"{image_id}.bin"
            if not image_path.exists():
                logger.warning(f"Image file not found: {image_id}")
                return None

            with open(image_path, 'rb') as f:
                return f.read()

        except Exception as e:
            logger.error(f"Failed to read image file {image_id}: {e}")
            return None

    async def delete_image(self, image_id: str) -> bool:
        """
        Delete a single image file (metadata entry remains in JSONL).

        Args:
            image_id: Image identifier to delete

        Returns:
            True if deleted, False otherwise
        """
        try:
            image_path = self.images_dir / f"{image_id}.bin"
            if image_path.exists():
                image_path.unlink()
                storage_logger.debug(f"Deleted image file {image_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"Failed to delete image {image_id}: {e}")
            return False

    async def clear_images(self) -> bool:
        """
        Delete all images and metadata for this session.

        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            import shutil

            if self.images_dir.exists():
                shutil.rmtree(self.images_dir)
                storage_logger.info(f"Cleared all images for session {self.session_dir.name}")

            return True

        except Exception as e:
            logger.error(f"Failed to clear images: {e}")
            return False

    async def get_image_count(self) -> int:
        """
        Get the number of images stored for this session.

        Returns:
            Number of image metadata entries
        """
        try:
            if not self.images_metadata_file.exists():
                return 0

            count = 0
            with open(self.images_metadata_file, encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        count += 1

            return count

        except Exception as e:
            logger.error(f"Failed to count images: {e}")
            return 0

    # =========================================================================
    # Resource Storage Methods (Issue #404 expansion - all file types)
    # =========================================================================

    async def save_resource_file(self, resource_id: str, file_bytes: bytes) -> bool:
        """
        Save raw file bytes to disk.

        Args:
            resource_id: Unique resource identifier
            file_bytes: Raw binary file data

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Ensure resources directory exists
            self.resources_dir.mkdir(parents=True, exist_ok=True)

            # Save binary file
            resource_path = self.resources_dir / f"{resource_id}.bin"
            with open(resource_path, 'wb') as f:
                f.write(file_bytes)

            storage_logger.debug(
                f"Saved resource {resource_id} ({len(file_bytes)} bytes) "
                f"to {self.session_dir.name}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to save resource file {resource_id}: {e}")
            return False

    async def append_resource(self, resource_metadata: dict[str, Any]) -> bool:
        """
        Append resource metadata to the resources JSONL log.

        Args:
            resource_metadata: Resource metadata dict with resource_id, title, etc.

        Returns:
            True if appended successfully, False otherwise
        """
        try:
            # Ensure resources directory exists
            self.resources_dir.mkdir(parents=True, exist_ok=True)

            # Add timestamp if not present
            if 'timestamp' not in resource_metadata:
                resource_metadata['timestamp'] = get_unix_timestamp()

            # Append to JSONL file
            with open(self.resources_metadata_file, 'a', encoding='utf-8') as f:
                json.dump(resource_metadata, f, ensure_ascii=False)
                f.write('\n')

            storage_logger.debug(
                f"Appended resource metadata for {resource_metadata.get('resource_id', 'unknown')} "
                f"to {self.session_dir.name}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to append resource metadata: {e}")
            return False

    async def read_resources(self) -> list[dict[str, Any]]:
        """
        Read all resource metadata from the resources JSONL log.

        Returns:
            List of resource metadata dicts, sorted by timestamp
        """
        try:
            resources = []

            if not self.resources_metadata_file.exists():
                return resources

            with open(self.resources_metadata_file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            resource = json.loads(line)
                            resources.append(resource)
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse resource line: {e}")

            # Sort by timestamp (oldest first)
            resources.sort(key=lambda x: x.get('timestamp', 0))

            return resources

        except Exception as e:
            logger.error(f"Failed to read resources: {e}")
            return []

    async def get_resource_file(self, resource_id: str) -> bytes | None:
        """
        Read raw file bytes from disk.

        Args:
            resource_id: Resource identifier

        Returns:
            Raw file bytes or None if not found
        """
        try:
            resource_path = self.resources_dir / f"{resource_id}.bin"
            if not resource_path.exists():
                logger.warning(f"Resource file not found: {resource_id}")
                return None

            with open(resource_path, 'rb') as f:
                return f.read()

        except Exception as e:
            logger.error(f"Failed to read resource file {resource_id}: {e}")
            return None

    async def delete_resource(self, resource_id: str) -> bool:
        """
        Delete a single resource file (metadata entry remains in JSONL).

        Args:
            resource_id: Resource identifier to delete

        Returns:
            True if deleted, False otherwise
        """
        try:
            resource_path = self.resources_dir / f"{resource_id}.bin"
            if resource_path.exists():
                resource_path.unlink()
                storage_logger.debug(f"Deleted resource file {resource_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"Failed to delete resource {resource_id}: {e}")
            return False

    async def clear_resources(self) -> bool:
        """
        Delete all resources and metadata for this session.

        Returns:
            True if cleared successfully, False otherwise
        """
        try:
            import shutil

            if self.resources_dir.exists():
                shutil.rmtree(self.resources_dir)
                storage_logger.info(f"Cleared all resources for session {self.session_dir.name}")

            return True

        except Exception as e:
            logger.error(f"Failed to clear resources: {e}")
            return False

    async def get_resource_count(self) -> int:
        """
        Get the number of resources stored for this session.

        Returns:
            Number of resource metadata entries
        """
        try:
            if not self.resources_metadata_file.exists():
                return 0

            count = 0
            with open(self.resources_metadata_file, encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        count += 1

            return count

        except Exception as e:
            logger.error(f"Failed to count resources: {e}")
            return 0
