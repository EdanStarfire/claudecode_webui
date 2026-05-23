"""
LinksMCPTools - MCP tools for registering persistent named links per session.

Exposes tools to agents:
  mcp__links__register_link  — register or update a named URL
  mcp__links__list_links     — list currently registered links
"""

import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

try:
    from claude_agent_sdk import create_sdk_mcp_server, tool
except ImportError:
    tool = None
    create_sdk_mcp_server = None

if TYPE_CHECKING:
    from src.session_coordinator import SessionCoordinator

logger = logging.getLogger(__name__)

MAX_LABEL_LENGTH = 200
MAX_URL_LENGTH = 2000


def _validate_url(url: str) -> str | None:
    """Return error message if URL is invalid, else None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "URL must use http:// or https:// scheme"
    if not parsed.netloc:
        return "URL is missing a hostname"
    return None


class LinksMCPTools:
    """MCP tools for registering persistent named links in a session."""

    def __init__(
        self,
        session_coordinator: "SessionCoordinator",
        broadcast_callback: Any = None,
    ):
        self.session_coordinator = session_coordinator
        self.broadcast_callback = broadcast_callback

    def create_mcp_server_for_session(self, session_id: str):
        """Create session-scoped MCP server with link tools."""
        if not tool or not create_sdk_mcp_server:
            logger.warning("Claude Agent SDK not available, cannot create links MCP server")
            return None

        @tool(
            "register_link",
            """Register a named URL link for this session.

The link appears in the Links panel of the WebUI sidebar and persists across
page refreshes, conversation compaction, session resets, and server restarts.
Links are removed only when the session is deleted.

If a link with the same label already exists it is updated (upsert semantics).

Arguments:
  label  — Short display name (required, 1-200 characters).
  url    — Destination URL (required, must use http:// or https://).

Examples:
  register_link(label="GitHub Issue #1530", url="https://github.com/owner/repo/issues/1530")
  register_link(label="Dashboard", url="https://example.com/dashboard")""",
            {
                "label": str,
                "url": str,
            },
        )
        async def register_link_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await self._handle_register_link(session_id, args)

        @tool(
            "list_links",
            """List all named links registered for this session.

Returns a JSON array of link objects, each with:
  label          — Display name
  url            — Destination URL
  registered_at  — ISO timestamp when last registered/updated

Use this to check what links are already registered before deciding to upsert.""",
            {},
        )
        async def list_links_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await self._handle_list_links(session_id, args)

        return create_sdk_mcp_server(
            name="links",
            version="1.0.0",
            tools=[register_link_tool, list_links_tool],
        )

    async def _handle_register_link(
        self, session_id: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle register_link tool call."""
        try:
            label = (args.get("label") or "").strip()
            url = (args.get("url") or "").strip()

            if not label:
                return {
                    "content": [{"type": "text", "text": "Error: 'label' is required and cannot be empty"}],
                    "is_error": True,
                }
            if len(label) > MAX_LABEL_LENGTH:
                return {
                    "content": [{"type": "text", "text": f"Error: 'label' exceeds {MAX_LABEL_LENGTH} characters"}],
                    "is_error": True,
                }
            if not url:
                return {
                    "content": [{"type": "text", "text": "Error: 'url' is required and cannot be empty"}],
                    "is_error": True,
                }
            if len(url) > MAX_URL_LENGTH:
                return {
                    "content": [{"type": "text", "text": f"Error: 'url' exceeds {MAX_URL_LENGTH} characters"}],
                    "is_error": True,
                }
            url_error = _validate_url(url)
            if url_error:
                return {
                    "content": [{"type": "text", "text": f"Error: {url_error}"}],
                    "is_error": True,
                }

            entry = await self.session_coordinator.session_manager.upsert_link(
                session_id, label, url
            )

            if self.broadcast_callback:
                try:
                    await self.broadcast_callback(session_id, entry)
                except Exception:
                    logger.exception(f"Failed to broadcast link_registered for {session_id}")

            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Link registered successfully.\n"
                        f"- Label: {entry['label']}\n"
                        f"- URL: {entry['url']}\n"
                        f"- Registered at: {entry['registered_at']}"
                    ),
                }],
                "is_error": False,
            }

        except Exception:
            logger.exception(f"Error in register_link for session {session_id}")
            return {
                "content": [{"type": "text", "text": "Error: Failed to register link"}],
                "is_error": True,
            }

    async def _handle_list_links(
        self, session_id: str, _args: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle list_links tool call."""
        try:
            session_info = await self.session_coordinator.session_manager.get_session_info(
                session_id
            )
            if not session_info:
                return {
                    "content": [{"type": "text", "text": "Error: Session not found"}],
                    "is_error": True,
                }
            links = session_info.links or []
            return {
                "content": [{
                    "type": "text",
                    "text": f"Found {len(links)} link(s).\n\n{json.dumps(links, indent=2)}",
                }],
                "is_error": False,
            }
        except Exception:
            logger.exception(f"Error in list_links for session {session_id}")
            return {
                "content": [{"type": "text", "text": "Error: Failed to list links"}],
                "is_error": True,
            }
