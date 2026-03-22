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

import json
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

from src.docker_utils import translate_docker_tmp_path

if TYPE_CHECKING:
    from src.session_coordinator import SessionCoordinator

logger = logging.getLogger(__name__)

# Constants
MAX_RESOURCE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit per resource

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

    @staticmethod
    def _get_resource_url(session_id: str, resource_id: str, title: str, is_image: bool) -> str:
        """
        Build a markdown URL for a resource.

        For images: ![{title}](/api/sessions/{sid}/resources/{rid})
        For non-images: /api/sessions/{sid}/resources/{rid}
        """
        url = f"/api/sessions/{session_id}/resources/{resource_id}"
        if is_image:
            return f"![{title}]({url})"
        return url

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

INLINE DISPLAY: For image resources, the response includes a `markdown` field with
ready-to-use markdown. To display the image inline in your response, paste the markdown
from the `markdown` field directly into your message text.

USAGE: Provide the absolute path to a file. The backend reads it directly.

Examples:
  register_resource(file_path="/home/user/screenshot.png", title="Login Page")
  register_resource(file_path="/tmp/output.json", title="API Response")
  register_resource(file_path="/var/log/app.log", title="Application Log")

Supported types: Text, Code, Config, Images, Data files
Maximum size: 10MB per resource""",
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

        @tool(
            "list_resources",
            """List all resources registered in the current session.

Use this to discover files and images that have been uploaded or registered,
along with their metadata and markdown URLs for inline embedding.

Each resource includes:
- resource_id: Unique identifier
- original_name: Original filename
- title: Display title
- format: File format (e.g. 'png', 'py', 'json')
- mime_type: MIME type
- is_image: Whether the resource is an image
- size_bytes: File size
- markdown: Ready-to-use URL (image markdown for images, plain URL for files)

Optional: Use format_filter to filter by format type (e.g. "image" for images only,
or a specific extension like "png", "json").""",
            {
                "format_filter": str,  # Optional filter: "image" or specific format
            }
        )
        async def list_resources_tool(args: dict[str, Any]) -> dict[str, Any]:
            """List all session resources with metadata."""
            return await self._handle_list_resources(session_id, args)

        @tool(
            "get_resource",
            """Get metadata for a specific resource by ID or filename.

Returns a single resource with its metadata and markdown URL for inline embedding.
Provide either resource_id or filename (case-insensitive match on original filename).

At least one of resource_id or filename must be provided.""",
            {
                "resource_id": str,  # Optional: exact resource ID
                "filename": str,    # Optional: original filename (case-insensitive)
            }
        )
        async def get_resource_tool(args: dict[str, Any]) -> dict[str, Any]:
            """Get a specific resource by ID or filename."""
            return await self._handle_get_resource(session_id, args)

        # Create and return MCP server with resource tools
        return create_sdk_mcp_server(
            name="resources",
            version="2.0.0",
            tools=[
                register_resource_tool,
                register_image_tool,
                list_resources_tool,
                get_resource_tool,
            ]
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

            # Issue #820: Translate /tmp/ paths for Docker sessions
            file_path_str = await translate_docker_tmp_path(
                file_path_str, session_id, self.session_coordinator
            )

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

            # Get storage manager for this session
            storage_manager = await self._get_storage_manager(session_id)

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
            markdown_field = ""
            if is_image:
                markdown_field = self._get_resource_url(
                    session_id, resource_id, title, is_image=True
                )
            result_text = (
                f"{type_label} registered successfully.\n"
                f"- ID: {resource_id}\n"
                f"- Title: {title}\n"
                f"- Type: {resource_format.upper()}\n"
                f"- Size: {file_size / 1024:.1f}KB\n"
                f"- Source: {file_path}\n"
            )
            if markdown_field:
                result_text += (
                    f"- Markdown: {markdown_field}\n\n"
                    f"The image is now visible in the Resource Gallery. "
                    f"To display it inline in your response, paste the markdown above "
                    f"into your message text."
                )
            else:
                result_text += (
                    "\nThe file is now visible in the Resource Gallery."
                )
            return {
                "content": [{
                    "type": "text",
                    "text": result_text
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

    async def _handle_list_resources(
        self,
        session_id: str,
        args: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle list_resources tool call."""
        try:
            storage_manager = await self._get_storage_manager(session_id)
            if not storage_manager:
                return {
                    "content": [{"type": "text", "text": "Error: No storage available for this session"}],
                    "is_error": True
                }

            resources = await storage_manager.read_resources()
            format_filter = (args.get("format_filter") or "").strip().lower()

            if format_filter:
                if format_filter == "image":
                    resources = [r for r in resources if r.get("is_image")]
                else:
                    resources = [
                        r for r in resources
                        if (r.get("format") or "").lower() == format_filter
                    ]

            # Augment each resource with markdown URL
            result_items = []
            for r in resources:
                item = {
                    "resource_id": r.get("resource_id"),
                    "original_name": r.get("original_name"),
                    "title": r.get("title"),
                    "format": r.get("format"),
                    "mime_type": r.get("mime_type"),
                    "is_image": r.get("is_image", False),
                    "size_bytes": r.get("size_bytes"),
                    "markdown": self._get_resource_url(
                        session_id,
                        r.get("resource_id", ""),
                        r.get("title", r.get("original_name", "")),
                        r.get("is_image", False),
                    ),
                }
                result_items.append(item)

            result_text = json.dumps(result_items, indent=2)
            summary = f"Found {len(result_items)} resource(s)"
            if format_filter:
                summary += f" matching filter '{format_filter}'"

            return {
                "content": [{"type": "text", "text": f"{summary}.\n\n{result_text}"}],
                "is_error": False
            }

        except Exception as e:
            logger.error(f"Error in list_resources: {e}", exc_info=True)
            return {
                "content": [{"type": "text", "text": f"Error listing resources: {e!s}"}],
                "is_error": True
            }

    async def _handle_get_resource(
        self,
        session_id: str,
        args: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle get_resource tool call."""
        try:
            resource_id = (args.get("resource_id") or "").strip()
            filename = (args.get("filename") or "").strip()

            if not resource_id and not filename:
                return {
                    "content": [{"type": "text", "text": "Error: Provide at least one of 'resource_id' or 'filename'"}],
                    "is_error": True
                }

            storage_manager = await self._get_storage_manager(session_id)
            if not storage_manager:
                return {
                    "content": [{"type": "text", "text": "Error: No storage available for this session"}],
                    "is_error": True
                }

            resources = await storage_manager.read_resources()
            match = None

            if resource_id:
                match = next((r for r in resources if r.get("resource_id") == resource_id), None)

            if not match and filename:
                filename_lower = filename.lower()
                match = next(
                    (r for r in resources if (r.get("original_name") or "").lower() == filename_lower),
                    None,
                )

            if not match:
                lookup = resource_id or filename
                return {
                    "content": [{"type": "text", "text": f"Error: No resource found matching '{lookup}'"}],
                    "is_error": True
                }

            item = {
                "resource_id": match.get("resource_id"),
                "original_name": match.get("original_name"),
                "title": match.get("title"),
                "format": match.get("format"),
                "mime_type": match.get("mime_type"),
                "is_image": match.get("is_image", False),
                "size_bytes": match.get("size_bytes"),
                "markdown": self._get_resource_url(
                    session_id,
                    match.get("resource_id", ""),
                    match.get("title", match.get("original_name", "")),
                    match.get("is_image", False),
                ),
            }
            return {
                "content": [{"type": "text", "text": json.dumps(item, indent=2)}],
                "is_error": False
            }

        except Exception as e:
            logger.error(f"Error in get_resource: {e}", exc_info=True)
            return {
                "content": [{"type": "text", "text": f"Error getting resource: {e!s}"}],
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
