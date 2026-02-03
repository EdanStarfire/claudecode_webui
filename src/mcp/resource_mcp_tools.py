"""
ResourceMCPTools - MCP tool for displaying resources in the WebUI task panel.

This module provides MCP tools that agents can use to:
- Display images (screenshots, diagrams, visual content) to users in real-time
- Present files (code, config, logs) for user review
- Support browser automation debugging workflows
- Enable visual feedback during agent execution

Implementation uses Claude Agent SDK's @tool decorator and create_sdk_mcp_server.
Tools are exposed to agents with names like: mcp__resources__register_resource
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
MAX_RESOURCE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit per resource
MAX_RESOURCES_PER_SESSION = 100  # Maximum resources per session

# Supported file extensions (matching file_upload.py)
SUPPORTED_EXTENSIONS = {
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
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico', '.tiff', '.tif',
    # Data files
    '.sql', '.graphql',
}

# Image extensions for format detection
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico', '.tiff', '.tif'}

# MIME type mapping
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
    '.bmp': 'image/bmp',
    '.ico': 'image/x-icon',
    '.tiff': 'image/tiff',
    '.tif': 'image/tiff',
}


class ResourceMCPTools:
    """
    MCP tools for resource display in WebUI task panel.

    Creates an MCP server with tools for registering resources (images, files)
    for display in the task panel gallery.
    """

    def __init__(
        self,
        session_coordinator: "SessionCoordinator",
        broadcast_callback: Any = None
    ):
        """
        Initialize ResourceMCPTools.

        Args:
            session_coordinator: SessionCoordinator instance for data storage access
            broadcast_callback: Async callback to broadcast resource_registered events
                               Signature: async def callback(session_id: str, resource_data: dict)
        """
        self.session_coordinator = session_coordinator
        self.broadcast_callback = broadcast_callback

    def create_mcp_server_for_session(self, session_id: str):
        """
        Create session-specific MCP server with resource tools.

        Args:
            session_id: The session ID to scope resources to

        Returns:
            MCP server instance with register_resource tool
        """
        if not tool or not create_sdk_mcp_server:
            logger.warning("Claude Agent SDK not available, cannot create MCP server")
            return None

        @tool(
            "register_resource",
            """Present a file to the user in the WebUI Resource Gallery.

Use this tool to display files for user review, such as:
- Screenshots, diagrams, and visual content
- Code files, logs, and configuration files
- Generated output files
- Any file you want the user to easily access

Resources appear in the Task Panel where users can:
- View thumbnails (images) or file icons (other types)
- Click to preview or download files
- Add resources back to the chat as context

USAGE: Provide the absolute path to a file. The backend reads it directly.

Examples:
  register_resource(file_path="/home/user/screenshot.png", title="Login Page")
  register_resource(file_path="/tmp/output.json", title="API Response")
  register_resource(file_path="/var/log/app.log", title="Application Log")

Supported types: Text, Code, Config, Images, Data files
Maximum size: 10MB per resource
Maximum resources per session: 100""",
            {
                "file_path": str,      # Absolute path to file (required)
                "title": str,          # Short caption (optional, default: filename)
                "description": str     # Detailed description (optional)
            }
        )
        async def register_resource_tool(args: dict[str, Any]) -> dict[str, Any]:
            """Register a resource for display in the task panel."""
            return await self._handle_register_resource(session_id, args)

        # Keep backward compatibility with register_image
        @tool(
            "register_image",
            """Display an image file in the WebUI task panel for user review.

DEPRECATED: Use register_resource() instead - it supports all file types.

This tool is kept for backward compatibility but simply calls register_resource.""",
            {
                "file_path": str,
                "title": str,
                "description": str
            }
        )
        async def register_image_tool(args: dict[str, Any]) -> dict[str, Any]:
            """Register an image (backward compatibility wrapper)."""
            return await self._handle_register_resource(session_id, args)

        # Create and return MCP server with resource tools
        return create_sdk_mcp_server(
            name="resources",
            version="2.0.0",
            tools=[register_resource_tool, register_image_tool]
        )

    async def _handle_register_resource(
        self,
        session_id: str,
        args: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Handle register_resource tool call.

        Args:
            session_id: Session to register resource for
            args: {
                "file_path": str,       # Path to file
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
            if ext and ext not in SUPPORTED_EXTENSIONS:
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"Error: Unsupported file extension '{ext}'. "
                            f"Supported types: text, code, config, images, data files."
                        )
                    }],
                    "is_error": True
                }

            # Check file size
            file_size = file_path.stat().st_size
            if file_size > MAX_RESOURCE_SIZE_BYTES:
                size_mb = file_size / (1024 * 1024)
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Error: File too large ({size_mb:.2f}MB). Maximum size is 10MB."
                    }],
                    "is_error": True
                }

            if file_size == 0:
                return {
                    "content": [{
                        "type": "text",
                        "text": "Error: File is empty (0 bytes)"
                    }],
                    "is_error": True
                }

            # Determine if this is an image
            is_image = ext in IMAGE_EXTENSIONS
            mime_type = MIME_TYPES.get(ext, 'application/octet-stream')

            # Read file
            file_bytes = file_path.read_bytes()

            # For images, validate format
            resource_format = None
            if is_image:
                resource_format = self._detect_image_format(file_bytes)
                if not resource_format and ext != '.svg':
                    return {
                        "content": [{
                            "type": "text",
                            "text": "Error: File does not appear to be a valid image."
                        }],
                        "is_error": True
                    }
                if ext == '.svg':
                    resource_format = 'svg'
            else:
                # Use extension as format for non-images
                resource_format = ext.lstrip('.')

            # Check session resource count limit
            storage_manager = await self._get_storage_manager(session_id)
            if storage_manager:
                existing_resources = await storage_manager.read_resources()
                if len(existing_resources) >= MAX_RESOURCES_PER_SESSION:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Error: Maximum resources per session reached ({MAX_RESOURCES_PER_SESSION})"
                        }],
                        "is_error": True
                    }

            # Generate unique resource ID
            resource_id = str(uuid.uuid4())

            # Use filename as default title if not provided
            if not title:
                title = file_path.name

            # Create resource metadata
            from src.timestamp_utils import get_unix_timestamp
            resource_metadata = {
                "resource_id": resource_id,
                "session_id": session_id,
                "title": title,
                "description": description,
                "format": resource_format,
                "mime_type": mime_type,
                "is_image": is_image,
                "size_bytes": file_size,
                "original_path": str(file_path),
                "original_name": file_path.name,
                "timestamp": get_unix_timestamp()
            }

            # Store resource file and metadata
            if storage_manager:
                # Save binary file (copy to session storage)
                await storage_manager.save_resource_file(resource_id, file_bytes)

                # Append metadata to JSONL
                await storage_manager.append_resource(resource_metadata)

                logger.info(
                    f"Registered resource {resource_id} for session {session_id}: "
                    f"{title} ({file_size / 1024:.1f}KB, {resource_format})"
                )
            else:
                logger.warning(f"No storage manager for session {session_id}, resource not persisted")

            # Broadcast to WebSocket
            if self.broadcast_callback:
                try:
                    await self.broadcast_callback(session_id, resource_metadata)
                except Exception as e:
                    logger.error(f"Failed to broadcast resource_registered: {e}")

            type_label = "Image" if is_image else "File"
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"{type_label} registered successfully.\n"
                        f"- ID: {resource_id}\n"
                        f"- Title: {title}\n"
                        f"- Type: {resource_format.upper()}\n"
                        f"- Size: {file_size / 1024:.1f}KB\n"
                        f"- Source: {file_path}\n\n"
                        f"The {type_label.lower()} is now visible in the Resource Gallery."
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
            logger.error(f"Error in register_resource: {e}", exc_info=True)
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error registering resource: {str(e)}"
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

        # ICO: 00 00 01 00
        if image_bytes[:4] == b'\x00\x00\x01\x00':
            return "ico"

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


# Backward compatibility alias
ImageViewerMCPTools = ResourceMCPTools
