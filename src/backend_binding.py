"""BackendBinding: Frontend's one connection to its Backend (issue #2268 AC5).

A seam for #1818's eventual multi-backend UI — today there is always exactly
one BackendBinding per Frontend process (no registry/keyed collection; that's
deferred to #1818, where a second binding would actually get populated).
Composes everything that distinguishes embedded-with-supervisor mode from a
manually-configured remote Backend into the one binary distinction all 8
existing `backend_supervisor is None`-style checks collapsed into:
`is_embedded`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .backend_client import BackendClient
from .backend_supervisor import BackendSupervisor


@dataclass
class BackendBinding:
    """Everything ClaudeWebUI needs to know about its one Backend connection."""

    client: BackendClient
    token: str
    supervisor: BackendSupervisor | None = None
    # Frontend's own networking/backend_connection config section (issue #498
    # config-split design) — same file src/frontend_config.py reads/writes.
    config_file: Path | None = None
    # Issue #1789 (via #498): dynamic custom OAuth callback paths for shared
    # MCP servers, mirrored here as relay routes since only Frontend is
    # typically publicly reachable.
    oauth_callback_paths: set[str] = field(default_factory=set)

    @property
    def is_embedded(self) -> bool:
        """True when this Frontend auto-started its own local Backend
        (single-user self-hosted default, issue #498 Phase 3); False for a
        manually-configured remote Backend — exactly one of the two always
        holds, enforced at construction (see ClaudeWebUI.__init__)."""
        return self.supervisor is not None

    @property
    def degraded(self) -> bool:
        """True only in embedded mode, once the supervisor's own crash-loop
        restart budget is exhausted. Always False in remote mode — there is
        no local supervisor here to restart (issue #1989)."""
        return self.supervisor is not None and self.supervisor.degraded

    async def stop_supervisor(self) -> None:
        """Stop the embedded Backend subprocess. No-op in remote mode — this
        binding owns no process to stop (/api/system/restart's remote-mode
        path restarts Backend a different way, via its own REST endpoint)."""
        if self.supervisor is not None:
            await self.supervisor.stop()
