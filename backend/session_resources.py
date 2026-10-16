"""
Session Resources for Claude Code WebUI

Extracted from SessionCoordinator (issue #2189, epic #1990 stage 7
`coordinator-split`, PR 5): owns the module-level resource-formatting
helpers, resource-gallery CRUD/broadcast, proxy-log reading, and
uploaded-file tracking. This is a mechanical extraction — SessionCoordinator
keeps every public method as a one-line delegate, and every per-session
shared dict stays constructed on the coordinator; SessionResources receives
the same dict objects by reference, never copies.

This PR also permanently resolves PR 4's one deliberately-left temporary
seam: `_group_resources_by_filename`/`_apply_resource_filters` were left on
SessionCoordinator as module-level functions, reachable by
`archive_coordinator.py` only via a temporary property indirection. They
now live here permanently, and `archive_coordinator.py` imports them
directly.

Three fields are genuinely owned by this module, not live-property
passthroughs (confirmed via exhaustive grep that nothing outside this
cluster touches them directly): `_uploaded_file_paths`,
`_resource_broadcast_callback`, `_link_broadcast_callback`.

One pre-existing latent bug is preserved verbatim, not fixed:
`get_proxy_logs` calls `self.logger.warning(...)` on two malformed-input
branches, but neither SessionCoordinator nor this module ever actually sets
`self.logger` (only the module-level `logger` exists) — a dormant
AttributeError never hit because no current test feeds malformed proxy-log
lines into those branches.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from .data_storage import DataStorageManager

# Get specialized logger for coordinator actions (same category as the
# coordinator's own coord_logger — this is still "coordinator actions" from
# an operator's perspective, just physically relocated).
coord_logger = get_logger('coordinator', category='COORDINATOR')
logger = logging.getLogger(__name__)

# Resource format groups for filtering
_TEXT_RESOURCE_FORMATS = {
    "py", "js", "ts", "json", "md", "txt", "yaml", "yml",
    "toml", "cfg", "ini", "log", "csv", "xml", "html", "css",
    "sh", "bash", "rs", "go", "java", "c", "cpp", "h", "rb",
    "php", "sql", "r", "swift", "kt",
}


def _apply_resource_filters(
    resources: list[dict],
    search: str | None,
    format_filter: str | None,
    sort: str,
) -> list[dict]:
    """Filter and sort a list of resource dicts. Returns a new list."""
    result = list(resources)

    if search:
        q = search.lower()
        result = [
            r
            for r in result
            if q in (r.get("title") or "").lower() or q in (r.get("original_name") or "").lower()
        ]

    if format_filter:
        if format_filter == "image":
            result = [r for r in result if r.get("is_image")]
        elif format_filter == "video":
            result = [r for r in result if r.get("is_video")]
        elif format_filter == "text":
            result = [r for r in result if r.get("format") in _TEXT_RESOURCE_FORMATS]
        else:
            result = [r for r in result if r.get("format") == format_filter]

    if sort == "newest":
        result.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    elif sort == "oldest":
        result.sort(key=lambda x: x.get("timestamp", 0))
    elif sort == "name-asc":
        result.sort(key=lambda x: (x.get("title") or x.get("original_name") or "").lower())
    elif sort == "name-desc":
        result.sort(
            key=lambda x: (x.get("title") or x.get("original_name") or "").lower(),
            reverse=True,
        )

    return result


def _group_resources_by_filename(resources: list[dict]) -> list[dict]:
    """Group resources into versions by original_name (case-insensitive).

    Issue #1680: re-registering a resource under the same filename represents
    an update, not a new independent entry. Groups by the full filename
    (including extension) so "report.md" and "report.txt" stay separate.

    Each returned item is the group's latest entry (by timestamp), augmented
    with `version_count`. Groups with more than one entry also carry a
    `versions` list (newest-first, each entry tagged with `version_number`,
    oldest = 1). Single-entry groups pass through with only `version_count=1`.
    """
    groups: dict[str, list[dict]] = {}
    for r in resources:
        key = (r.get("original_name") or "").lower()
        groups.setdefault(key, []).append(r)

    result = []
    for entries in groups.values():
        ordered = sorted(entries, key=lambda x: x.get("timestamp", 0))
        version_count = len(ordered)
        versions = [dict(entry, version_number=i + 1) for i, entry in enumerate(ordered)]
        versions.reverse()  # newest first

        latest = dict(versions[0])
        latest["version_count"] = version_count
        if version_count > 1:
            latest["versions"] = versions
        result.append(latest)

    return result


def _tail_read_lines(path: "Path", limit: int) -> list[str]:
    """Read the last `limit` lines from a file efficiently using a deque."""
    from collections import deque

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return list(deque(f, maxlen=limit))
    except OSError:
        return []


def _count_file_lines(path: "Path") -> int:
    """Count total lines in a file efficiently."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


class SessionResources:
    """Owns resource-gallery CRUD/broadcast, proxy-log reading, and
    uploaded-file tracking.
    """

    def __init__(self, *, coordinator: Any):
        # Issue #2189: every collaborator manager and shared per-session dict is a
        # LIVE property lookup against the coordinator (below), not a value
        # snapshotted here at construction time — same discipline as
        # SessionLauncher/MessagePipeline/ToolCallTracker/ArchiveCoordinator
        # (PR 1-4).
        self._coordinator = coordinator

        # Issue #403: Uploaded file paths per session for auto-approve Read
        # permissions. Maps session_id -> set of absolute file paths.
        # Genuinely owned by this module — confirmed via exhaustive grep that
        # nothing outside this cluster touches it directly.
        self._uploaded_file_paths: dict[str, set[str]] = {}

        # Issue #404/#1530: broadcast callbacks for resource_registered/
        # link_registered events, injected by web_server. Genuinely owned by
        # this module — everything external goes through the public setter
        # methods below, which stay coordinator delegates.
        self._resource_broadcast_callback: Callable[[str, dict], None] | None = None
        self._link_broadcast_callback: Callable[[str, dict], None] | None = None

    # --- Collaborator managers: live passthrough to the coordinator ----------
    session_manager = property(lambda self: self._coordinator.session_manager)
    data_dir = property(lambda self: self._coordinator.data_dir)
    resource_mcp_tools = property(lambda self: self._coordinator.resource_mcp_tools)

    # --- Shared per-session state: live passthrough to the coordinator -------
    _storage_managers = property(lambda self: self._coordinator._storage_managers)

    # ==================== RESOURCE GALLERY QUERY/BROADCAST (Issue #404) ====================

    async def get_session_resources(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        format_filter: str | None = None,
        sort: str = "newest",
    ) -> dict:
        """
        Get resource metadata for a session, paginated with optional filter/sort.

        Issue #404: Used by REST endpoint to list session resources.
        Issue #972: Added server-side search, format_filter, and sort params.

        Args:
            session_id: Session ID
            limit: Maximum number of resources to return
            offset: Number of resources to skip
            search: Substring match on title or original_name (case-insensitive)
            format_filter: "image", "text", or a specific extension (e.g. "pdf")
            sort: "newest" | "oldest" | "name-asc" | "name-desc"

        Returns:
            Paginated dict with resources, total, limit, offset, has_more
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            # Try to get from session directory
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        all_resources: list[dict] = []
        if storage_manager:
            all_resources = await storage_manager.read_resources()

        all_resources = _group_resources_by_filename(all_resources)
        all_resources = _apply_resource_filters(all_resources, search, format_filter, sort)

        total = len(all_resources)
        sliced = all_resources[offset : offset + limit]
        return {
            "resources": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def get_session_resource_by_id(self, session_id: str, resource_id: str) -> dict | None:
        """
        Look up a single resource's metadata by ID against the raw (ungrouped) log.

        Issue #1680: byte-serving endpoints must resolve the exact version
        requested, not just the latest representative of its filename group.

        Args:
            session_id: Session ID
            resource_id: Resource ID

        Returns:
            Resource metadata dict or None
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if not storage_manager:
            return None

        all_resources = await storage_manager.read_resources()
        return next((r for r in all_resources if r.get("resource_id") == resource_id), None)

    async def get_session_resource_file(self, session_id: str, resource_id: str) -> bytes | None:
        """
        Get raw file bytes for a specific resource.

        Issue #404: Used by REST endpoint to serve resource files.

        Args:
            session_id: Session ID
            resource_id: Resource ID

        Returns:
            Raw file bytes or None
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if storage_manager:
            return await storage_manager.get_resource_file(resource_id)

        return None

    async def get_proxy_logs(self, session_id: str, log_type: str = "http", limit: int = 200) -> dict:
        """
        Get proxy log entries for a session.

        Issue #1102: Reads access.log (HTTP) or dns.log (DNS) from the proxy sidecar
        data directory and returns the last `limit` parsed entries.
        Issue #1214: Also supports socks5.log for SOCKS5 connection entries.

        Args:
            session_id: Session ID
            log_type: "http", "dns", or "socks5"
            limit: Maximum number of entries to return (tail-read)

        Returns:
            dict with entries, total_lines, and log_type
        """
        session_dir = self.data_dir / "sessions" / session_id
        if log_type == "http":
            log_path = session_dir / "docker_claude_data" / "proxy" / "access.log"
        elif log_type == "socks5":
            log_path = session_dir / "docker_claude_data" / "proxy" / "socks5.log"
        else:
            log_path = session_dir / "docker_claude_data" / "proxy" / "dns.log"

        if not log_path.exists():
            return {"entries": [], "total_lines": 0, "log_type": log_type}

        # Tail-read: read last `limit` lines efficiently
        lines = _tail_read_lines(log_path, limit)
        total_lines = _count_file_lines(log_path)

        entries = []
        if log_type == "http":
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    entries.append({
                        "ts": raw.get("ts"),
                        "host": raw.get("host"),
                        "method": raw.get("method"),
                        "path": raw.get("path"),
                        "status": raw.get("status"),
                        "allowed": raw.get("allowed"),
                        "credential_used": raw.get("credential_used"),
                        "scheme": raw.get("scheme"),
                        "port": raw.get("port"),
                        "bytes": raw.get("bytes"),
                        "routed_via_litellm": raw.get("routed_via_litellm", False),
                        "original_host": raw.get("original_host", raw.get("host")),
                    })
                except (json.JSONDecodeError, ValueError):
                    self.logger.warning(f"Skipping malformed HTTP proxy log line in session {session_id}")
        elif log_type == "socks5":
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    entries.append({
                        "ts": raw.get("ts"),
                        "host": raw.get("host"),
                        "port": raw.get("port"),
                        "allowed": raw.get("allowed"),
                        "reason": raw.get("reason"),
                    })
                except (json.JSONDecodeError, ValueError):
                    self.logger.warning(f"Skipping malformed SOCKS5 proxy log line in session {session_id}")
        else:
            # CoreDNS log format:
            # [INFO] 10.0.0.2:54312 - 12345 "A IN api.github.com. udp 128 false 4096" NOERROR qr,rd,ra 73 0.023s
            dns_pattern = re.compile(
                r'\[INFO\].*?"(\w+) IN (\S+?)\. \w+ \d+ \w+ \d+"\s+(\w+)'
            )
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                m = dns_pattern.search(line)
                if m:
                    entries.append({
                        "query_type": m.group(1),
                        "hostname": m.group(2),
                        "result": m.group(3),
                    })

        return {"entries": entries, "total_lines": total_lines, "log_type": log_type}

    async def remove_session_resource(self, session_id: str, resource_id: str) -> bool:
        """
        Soft-remove a resource from the session display.

        Issue #423: Appends a removal marker to resources.jsonl so the resource
        is hidden from the gallery. The .bin file is NOT deleted.

        Args:
            session_id: Session ID
            resource_id: Resource ID to remove from display

        Returns:
            True if removed successfully, False otherwise
        """
        storage_manager = self._storage_managers.get(session_id)
        if not storage_manager:
            session_dir = await self.session_manager.get_session_directory(session_id)
            if session_dir:
                storage_manager = DataStorageManager(session_dir)
                await storage_manager.initialize()

        if storage_manager:
            return await storage_manager.remove_resource_from_display(resource_id)

        return False

    def set_resource_broadcast_callback(self, callback: Callable[[str, dict], None]) -> None:
        """
        Set the callback for broadcasting resource_registered events to WebSocket.

        Issue #404: Resource MCP tool integration.

        Args:
            callback: Async function(session_id, resource_metadata) for WebSocket broadcast
        """
        self._resource_broadcast_callback = callback
        coord_logger.info("Resource broadcast callback registered in SessionCoordinator")

    # Backward compatibility alias
    def set_image_broadcast_callback(self, callback: Callable[[str, dict], None]) -> None:
        """Deprecated: Use set_resource_broadcast_callback instead."""
        self.set_resource_broadcast_callback(callback)

    def set_link_broadcast_callback(self, callback: Callable[[str, dict], None]) -> None:
        """Set the callback for broadcasting link_registered events to the session poll queue.

        Issue #1530: Called by web_server after coordinator is initialised.
        """
        self._link_broadcast_callback = callback
        coord_logger.info("Link broadcast callback registered in SessionCoordinator")

    async def _broadcast_resource_registered(self, session_id: str, resource_metadata: dict) -> None:
        """
        Broadcast resource_registered event via the injected callback.

        Issue #404: Called by ResourceMCPTools when a resource is registered.

        Args:
            session_id: Session that registered the resource
            resource_metadata: Resource metadata dict (resource_id, title, is_image, etc.)
        """
        if self._resource_broadcast_callback:
            try:
                if asyncio.iscoroutinefunction(self._resource_broadcast_callback):
                    await self._resource_broadcast_callback(session_id, resource_metadata)
                else:
                    self._resource_broadcast_callback(session_id, resource_metadata)
            except Exception:
                logger.exception(f"Failed to broadcast resource_registered for {session_id}")

    async def _broadcast_link_registered(self, session_id: str, link: dict) -> None:
        """Broadcast link_registered event via the injected callback.

        Issue #1530: Called by LinksMCPTools when a link is registered or updated.
        """
        if self._link_broadcast_callback:
            try:
                if asyncio.iscoroutinefunction(self._link_broadcast_callback):
                    await self._link_broadcast_callback(session_id, link)
                else:
                    self._link_broadcast_callback(session_id, link)
            except Exception:
                logger.exception(f"Failed to broadcast link_registered for {session_id}")

    async def get_session_links(self, session_id: str) -> list[dict]:
        """Return the list of agent-registered links for a session.

        Issue #1530: Used by the REST endpoint GET /api/sessions/{id}/links.
        """
        session_info = await self.session_manager.get_session_info(session_id)
        if not session_info:
            return []
        return session_info.links or []

    # ==================== UPLOADED FILE TRACKING (Issue #403) ====================

    async def register_uploaded_file(self, session_id: str, file_path: str) -> None:
        """
        Register an uploaded file path for auto-approve Read permissions.

        When a user uploads a file, the path is registered so that subsequent
        Read tool requests for that path can be auto-approved without prompting.

        Args:
            session_id: Session ID that owns the file
            file_path: Absolute path to the uploaded file
        """
        if session_id not in self._uploaded_file_paths:
            self._uploaded_file_paths[session_id] = set()
        self._uploaded_file_paths[session_id].add(file_path)
        coord_logger.debug(f"Registered uploaded file for session {session_id}: {file_path}")

    async def unregister_uploaded_file(self, session_id: str, file_path: str) -> None:
        """
        Unregister an uploaded file path from auto-approve tracking.

        Called when a file is deleted by the user.

        Args:
            session_id: Session ID that owns the file
            file_path: Absolute path to the uploaded file
        """
        if session_id in self._uploaded_file_paths:
            self._uploaded_file_paths[session_id].discard(file_path)
            coord_logger.debug(f"Unregistered uploaded file for session {session_id}: {file_path}")

    def is_uploaded_file(self, session_id: str, file_path: str) -> bool:
        """
        Check if a file path is an uploaded file for a session.

        Used by permission callbacks to auto-approve Read requests for
        user-uploaded files.

        Args:
            session_id: Session ID to check
            file_path: Absolute path to check

        Returns:
            True if the file was uploaded by the user for this session
        """
        return file_path in self._uploaded_file_paths.get(session_id, set())

    def get_uploaded_files(self, session_id: str) -> set[str]:
        """
        Get all uploaded file paths for a session.

        Args:
            session_id: Session ID

        Returns:
            Set of absolute file paths uploaded to this session
        """
        return self._uploaded_file_paths.get(session_id, set()).copy()

    def clear_uploaded_files(self, session_id: str) -> None:
        """
        Clear all uploaded file tracking for a session.

        Called when a session is deleted or reset.

        Args:
            session_id: Session ID to clear
        """
        if session_id in self._uploaded_file_paths:
            del self._uploaded_file_paths[session_id]
            coord_logger.debug(f"Cleared uploaded files tracking for session {session_id}")

    # ==================== RESOURCE GALLERY INTEGRATION (Issue #404) ====================

    async def register_uploaded_resource(
        self,
        session_id: str,
        file_path: str,
        title: str | None = None,
        description: str | None = None
    ) -> dict | None:
        """
        Register an uploaded file to the resource gallery.

        When a user uploads a file through the attachment system,
        this automatically adds it to the task panel resource gallery.

        Args:
            session_id: Session ID that owns the resource
            file_path: Absolute path to the uploaded file
            title: Title for the resource (required — the underlying MCP handler
                rejects an empty title, issue #1912)
            description: Optional description

        Returns:
            Dict with resource_id and markdown URL, or None if registration failed
        """
        if not self.resource_mcp_tools:
            coord_logger.warning("Resource MCP tools not available")
            return None

        # Build args matching the MCP tool interface
        args = {
            "file_path": file_path,
            "title": title or "",
            "description": description or ""
        }

        # Use the MCP tool's internal handler directly
        result = await self.resource_mcp_tools._handle_register_resource(session_id, args)

        if result.get("is_error"):
            error_text = result.get("content", [{}])[0].get("text", "Unknown error")
            raise ValueError(f"Failed to register resource: {error_text}")

        coord_logger.info(f"Registered uploaded resource to gallery for session {session_id}: {title}")

        # Extract resource_id from result text and build metadata for caller
        result_text = result.get("content", [{}])[0].get("text", "")
        resource_id = None
        for line in result_text.splitlines():
            if line.startswith("- ID: "):
                resource_id = line[len("- ID: "):].strip()
                break

        if resource_id:
            from backend.mcp.resource_mcp_tools import ResourceMCPTools
            is_image = any(
                file_path.lower().endswith(ext)
                for ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico', '.tiff', '.tif')
            )
            return {
                "resource_id": resource_id,
                "markdown": ResourceMCPTools._get_resource_url(
                    session_id, resource_id, title or "", is_image
                ),
            }
        return None

    # Backward compatibility alias
    async def register_uploaded_image(
        self,
        session_id: str,
        file_path: str,
        title: str | None = None,
        description: str | None = None
    ) -> None:
        """Deprecated: Use register_uploaded_resource instead."""
        await self.register_uploaded_resource(session_id, file_path, title, description)
