"""
SessionBackend abstraction (issue #498).

A `SessionCoordinator` dispatches every SDK-touching session operation through a
`SessionBackend` instead of reaching into an SDK dict directly. Exactly one backend is
active per process, selected once at startup:

- `LocalBackend` (src/local_backend.py): today's behavior — owns the live `ClaudeSDK`
  instances and drives them directly. Default, and fully backward-compatible.
- `RemoteBackend` (src/remote_backend.py): relays every call to a configured REMOTE
  WebUI instance running headless (`/api/backend/*`, issue #499) and replays its
  streamed events into the local `EventQueue` so the rest of `SessionCoordinator`
  (message callbacks, poll transport) sees no difference from a local session.

There is never a per-session or per-project mix — `SessionCoordinator.backend_mode` is a
single global switch for the whole process (contract for #498/#499).
"""

from collections.abc import Callable
from enum import Enum
from typing import Any, Protocol


class BackendMode(Enum):
    """Which SessionBackend a SessionCoordinator dispatches to."""

    LOCAL = "local"
    REMOTE = "remote"


class SessionBackend(Protocol):
    """Dispatch target for session-domain SDK operations.

    Methods mirror `SessionCoordinator`'s own public session methods closely enough
    that the coordinator can either run its existing local logic (LocalBackend) or
    relay the call wholesale to a configured REMOTE (RemoteBackend) — see each
    coordinator method for the `if backend_mode == REMOTE: relay` branch.
    """

    async def create_session(
        self, session_id: str, project_id: str, config: Any, **kwargs: Any
    ) -> str:
        """Create a session. Only meaningfully invoked for REMOTE dispatch — LOCAL
        session creation is coordinator-local (project/session/storage managers) and
        never calls through the backend."""
        ...

    async def start_session(
        self,
        session_id: str,
        *,
        sdk_kwargs: dict[str, Any] | None = None,
        permission_callback: Callable | None = None,
        auto_approval_callback: Callable | None = None,
    ) -> tuple[bool, str | None]:
        """Start a session. Returns (success, raw_error_message).

        LOCAL: `sdk_kwargs` carries the fully-resolved SDK constructor kwargs (config,
        env, MCP servers, callbacks, ...) assembled by `SessionCoordinator.start_session`'s
        setup phase — this is the "SDK-construction tail" LocalBackend owns.
        REMOTE: `sdk_kwargs`/`auto_approval_callback` are unused (REMOTE resolves its
        own config locally); only `session_id`/`permission_callback` matter.
        """
        ...

    async def send_message(
        self, session_id: str, message: str, metadata: dict[str, Any] | None = None
    ) -> bool: ...

    async def interrupt_session(self, session_id: str) -> bool: ...

    async def set_permission_mode(self, session_id: str, mode: str) -> bool: ...

    async def set_model(self, session_id: str, model: str) -> bool: ...

    async def update_session_name(self, session_id: str, name: str) -> bool:
        """Update a session's display name.

        LOCAL: no-op (True) — the caller already applied the rename to the local
        `session_manager` directly; only invoked at all for REMOTE dispatch.
        REMOTE: relays the rename to REMOTE's own `/sessions/{id}/name` route so its
        persisted name stays in sync too (issue #499) — cosmetic fields are still
        session config that should never silently diverge between Hub and REMOTE.
        """
        ...

    async def update_session_config(self, session_id: str, raw_payload: dict[str, Any]) -> bool:
        """Apply a session config update (PATCH /api/sessions/{id}'s raw request body).

        LOCAL: no-op (True) — the caller already applied the update to the local
        `session_manager` directly; only invoked at all for REMOTE dispatch.
        REMOTE: relays the raw, unprocessed client payload to REMOTE's own
        `/sessions/{id}` PATCH route, so REMOTE independently applies the exact same
        field-transform logic its own router already has (rather than this side
        reimplementing it). Without this, REMOTE only ever sees the config it got at
        session-creation time — any later edit (model, tools, extra_env, MCP servers,
        ...) would silently never reach the process actually running the SDK (#499).
        """
        ...

    async def resolve_permission(
        self, session_id: str, request_id: str, response: dict[str, Any]
    ) -> bool:
        """Resolve a pending permission request.

        LOCAL: delegates to the existing `PermissionService.resolve()`.
        REMOTE: POSTs the response to REMOTE, whose own PermissionService resolves its
        own local Future — the Hub's own permission Future is never used for REMOTE
        sessions (constructed for signature uniformity only).
        """
        ...

    async def get_mcp_status(self, session_id: str) -> dict[str, Any]: ...

    async def get_context_usage(self, session_id: str) -> dict[str, Any]: ...

    async def toggle_mcp_server(self, session_id: str, name: str, enabled: bool) -> None: ...

    async def reconnect_mcp_server(self, session_id: str, name: str) -> None: ...

    async def add_directory(self, session_id: str, directory: str) -> dict[str, Any]: ...

    async def disconnect_session(self, session_id: str) -> bool: ...

    async def terminate_session(self, session_id: str) -> bool: ...

    async def get_session_runtime_info(self, session_id: str) -> dict[str, Any] | None:
        """Runtime-only info an active SDK/REMOTE session can report (queue size, SDK
        info dict) — the SDK-touching subset of what `get_session_info()` returns."""
        ...

    async def is_session_active(self, session_id: str) -> bool: ...

    def active_session_ids(self) -> list[str]: ...
