"""
ImageViewerMCPTools - MCP tool for displaying images in the WebUI task panel.

This module provides an MCP tool that agents can use to:
- Display images (screenshots, diagrams, visual content) to users in real-time
- Support browser automation debugging workflows
- Enable visual feedback during agent execution

Implementation uses Claude Agent SDK's @tool decorator and create_sdk_mcp_server.
Tools are exposed to agents with names like: mcp__images__register_image
"""

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from claude_agent_sdk import create_sdk_mcp_server, tool
except ImportError:
    # For testing environments without SDK
    tool = None
    create_sdk_mcp_server = None

if TYPE_CHECKING:
    from src.session_coordinator import SessionCoordinator

logger = logging.getLogger(__name__)

# Constants
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit per image
MAX_IMAGES_PER_SESSION = 50  # Maximum images per session
SUPPORTED_FORMATS = {"png", "jpeg", "jpg", "webp", "gif", "bmp", "tiff", "tif"}
SUPPORTED_EXTENSIONS = {".png", ".jpeg", ".jpg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}


class ImageViewerMCPTools:
    """
    MCP tools for image display in WebUI task panel.

    Creates an MCP server with tools for registering images for display.
    """

    def __init__(
        self,
        session_coordinator: "SessionCoordinator",
        broadcast_callback: Any = None
    ):
        """
        Initialize ImageViewerMCPTools.

        Args:
            session_coordinator: SessionCoordinator instance for data storage access
            broadcast_callback: Async callback to broadcast image_registered events
                               Signature: async def callback(session_id: str, image_data: dict)
        """
        self.session_coordinator = session_coordinator
        self.broadcast_callback = broadcast_callback

    def create_mcp_server_for_session(self, session_id: str):
        """
        Create session-specific MCP server with image tools.

        Args:
            session_id: The session ID to scope images to

        Returns:
            MCP server instance with register_image tool
        """
        if not tool or not create_sdk_mcp_server:
            logger.warning("Claude Agent SDK not available, cannot create MCP server")
            return None

        @tool(
            "register_image",
            """Display an image file in the WebUI task panel for user review.

Use this tool to show visual content such as:
- Screenshots from browser automation or the filesystem
- Generated images or diagrams
- Visual test results
- UI state captures

Images appear in the Task Panel where users can view thumbnails,
click to expand full-size, and navigate between multiple images.

USAGE: Simply provide the absolute path to an image file. The backend will
read the file directly - no base64 encoding needed.

Example:
  register_image(file_path="/home/user/screenshot.png", title="Login Page")

Supported formats: PNG, JPEG, WebP, GIF, BMP, TIFF
Maximum size: 10MB per image
Maximum images per session: 50""",
            {
                "file_path": str,      # Absolute path to image file (required)
                "title": str,          # Short caption (optional, default: filename)
                "description": str     # Detailed description (optional)
            }
        )
        async def register_image_tool(args: dict[str, Any]) -> dict[str, Any]:
            """Register an image for display in the task panel."""
            return await self._handle_register_image(session_id, args)

        # Create and return MCP server with image tool
        return create_sdk_mcp_server(
            name="images",
            version="1.0.0",
            tools=[register_image_tool]
        )

    async def _handle_register_image(
        self,
        session_id: str,
        args: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Handle register_image tool call.

        Args:
            session_id: Session to register image for
            args: {
                "file_path": str,       # Path to image file
                "title": str,           # Optional title
                "description": str      # Optional description
            }

        Returns:
            Tool result with success/error
        """
        try:
            # Extract parameters
            file_path_str = args.get("file_path", "").strip()
            title = args.get("title", "").strip()
            description = args.get("description", "").strip()

            # Validate required parameter
            if not file_path_str:
                return {
                    "content": [{
                        "type": "text",
                        "text": "Error: 'file_path' parameter is required and cannot be empty"
                    }],
                    "is_error": True
                }

            # Resolve and validate path
            file_path = Path(file_path_str).expanduser().resolve()

            if not file_path.exists():
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Error: File not found: {file_path}"
                    }],
                    "is_error": True
                }

            if not file_path.is_file():
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Error: Path is not a file: {file_path}"
                    }],
                    "is_error": True
                }

            # Check file extension
            ext = file_path.suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"Error: Unsupported file extension '{ext}'. "
                            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
                        )
                    }],
                    "is_error": True
                }

            # Check file size
            file_size = file_path.stat().st_size
            if file_size > MAX_IMAGE_SIZE_BYTES:
                size_mb = file_size / (1024 * 1024)
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Error: Image too large ({size_mb:.2f}MB). Maximum size is 10MB."
                    }],
                    "is_error": True
                }

            if file_size == 0:
                return {
                    "content": [{
                        "type": "text",
                        "text": "Error: Image file is empty (0 bytes)"
                    }],
                    "is_error": True
                }

            # Read file and validate format
            image_bytes = file_path.read_bytes()
            image_format = self._detect_image_format(image_bytes)

            if not image_format:
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"Error: File does not appear to be a valid image. "
                            f"Supported formats: {', '.join(sorted(SUPPORTED_FORMATS))}"
                        )
                    }],
                    "is_error": True
                }

            # Check session image count limit
            storage_manager = await self._get_storage_manager(session_id)
            if storage_manager:
                existing_images = await storage_manager.read_images()
                if len(existing_images) >= MAX_IMAGES_PER_SESSION:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Error: Maximum images per session reached ({MAX_IMAGES_PER_SESSION})"
                        }],
                        "is_error": True
                    }

            # Generate unique image ID
            image_id = str(uuid.uuid4())

            # Use filename as default title if not provided
            if not title:
                title = file_path.name

            # Create image metadata
            from src.timestamp_utils import get_unix_timestamp
            image_metadata = {
                "image_id": image_id,
                "session_id": session_id,
                "title": title,
                "description": description,
                "format": image_format,
                "size_bytes": file_size,
                "original_path": str(file_path),
                "timestamp": get_unix_timestamp()
            }

            # Store image file and metadata
            if storage_manager:
                # Save binary file (copy to session storage)
                await storage_manager.save_image_file(image_id, image_bytes)

                # Append metadata to JSONL
                await storage_manager.append_image(image_metadata)

                logger.info(
                    f"Registered image {image_id} for session {session_id}: "
                    f"{title} ({file_size / 1024:.1f}KB)"
                )
            else:
                logger.warning(f"No storage manager for session {session_id}, image not persisted")

            # Broadcast to WebSocket
            if self.broadcast_callback:
                try:
                    await self.broadcast_callback(session_id, image_metadata)
                except Exception as e:
                    logger.error(f"Failed to broadcast image_registered: {e}")

            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Image registered successfully.\n"
                        f"- ID: {image_id}\n"
                        f"- Title: {title}\n"
                        f"- Format: {image_format.upper()}\n"
                        f"- Size: {file_size / 1024:.1f}KB\n"
                        f"- Source: {file_path}\n\n"
                        f"The image is now visible in the Task Panel."
                    )
                }],
                "is_error": False
            }

        except PermissionError:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: Permission denied reading file: {file_path_str}"
                }],
                "is_error": True
            }
        except Exception as e:
            logger.error(f"Error in register_image: {e}", exc_info=True)
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error registering image: {str(e)}"
                }],
                "is_error": True
            }

    def _detect_image_format(self, image_bytes: bytes) -> str | None:
        """
        Detect image format from magic bytes.

        Args:
            image_bytes: Raw image bytes

        Returns:
            Format string ('png', 'jpeg', 'webp', 'gif', 'bmp', 'tiff') or None if unsupported
        """
        if len(image_bytes) < 12:
            return None

        # PNG: 89 50 4E 47 0D 0A 1A 0A
        if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            return "png"

        # JPEG: FF D8 FF
        if image_bytes[:3] == b'\xff\xd8\xff':
            return "jpeg"

        # WebP: RIFF....WEBP
        if image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
            return "webp"

        # GIF: GIF87a or GIF89a
        if image_bytes[:6] in (b'GIF87a', b'GIF89a'):
            return "gif"

        # BMP: BM
        if image_bytes[:2] == b'BM':
            return "bmp"

        # TIFF: II (little-endian) or MM (big-endian)
        if image_bytes[:4] in (b'II\x2a\x00', b'MM\x00\x2a'):
            return "tiff"

        return None

    async def _get_storage_manager(self, session_id: str):
        """
        Get the DataStorageManager for a session.

        Args:
            session_id: Session ID

        Returns:
            DataStorageManager instance or None
        """
        try:
            # Get from session coordinator's active SDK instances
            sdk_instance = self.session_coordinator._active_sdks.get(session_id)
            if sdk_instance and sdk_instance.storage_manager:
                return sdk_instance.storage_manager

            # Fallback: create from session info
            session_info = await self.session_coordinator.session_manager.get_session_info(
                session_id
            )
            if session_info:
                from src.data_storage import DataStorageManager
                session_dir = self.session_coordinator.data_dir / "sessions" / session_id
                storage = DataStorageManager(session_dir)
                await storage.initialize()
                return storage

        except Exception as e:
            logger.error(f"Failed to get storage manager for {session_id}: {e}")

        return None
