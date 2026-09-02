"""
LocalBackend: the default SessionBackend (issue #498).

Owns the live `ClaudeSDK` instances and the SDK factory DI seam (`set_sdk_factory`,
used to inject `MockClaudeSDK` for testing). This is a 1:1 relocation of the
`_active_sdks`/`_sdk_factory` state `SessionCoordinator` used to own directly — every
method here is a thin wrapper around an SDK call, identical to the inline logic that
used to live in `SessionCoordinator`. Coordinator-level orchestration (session state
transitions, storage, callbacks, config resolution) stays in `SessionCoordinator`;
only the raw "talk to the SDK object" primitives move here.
"""

import logging
from collections.abc import Callable
from typing import Any

from .claude_sdk import ClaudeSDK

logger = logging.getLogger(__name__)


class LocalBackend:
    """SessionBackend implementation that drives local `ClaudeSDK` instances directly."""

    def __init__(self) -> None:
        self._active_sdks: dict[str, ClaudeSDK] = {}
        self._sdk_factory = self._default_sdk_factory

    @staticmethod
    def _default_sdk_factory(session_id, working_directory, **kwargs):
        """Default factory that strips session_name before calling ClaudeSDK."""
        kwargs.pop("session_name", None)
        return ClaudeSDK(session_id=session_id, working_directory=working_directory, **kwargs)

    def set_sdk_factory(self, factory) -> None:
        """Set custom SDK factory for testing (e.g., MockClaudeSDK)."""
        self._sdk_factory = factory

    def get_sdk(self, session_id: str) -> ClaudeSDK | None:
        return self._active_sdks.get(session_id)

    def register_sdk(self, session_id: str, sdk: ClaudeSDK) -> None:
        self._active_sdks[session_id] = sdk

    def remove_sdk(self, session_id: str) -> None:
        self._active_sdks.pop(session_id, None)

    def active_session_ids(self) -> list[str]:
        return list(self._active_sdks.keys())

    async def is_session_active(self, session_id: str) -> bool:
        return session_id in self._active_sdks

    # ------------------------------------------------------------------
    # SessionBackend Protocol
    # ------------------------------------------------------------------

    async def create_session(
        self, session_id: str, project_id: str, config: Any, **kwargs: Any
    ) -> str:
        # LOCAL session creation is coordinator-local (project/session/storage
        # managers) and never dispatches through the backend — see
        # SessionCoordinator.create_session. Present only for Protocol conformance.
        return session_id

    async def start_session(
        self,
        session_id: str,
        *,
        sdk_kwargs: dict[str, Any] | None = None,
        permission_callback: Callable | None = None,
        auto_approval_callback: Callable | None = None,
    ) -> tuple[bool, str | None]:
        """Construct the SDK via the factory and start it — the "SDK-construction
        tail" of `SessionCoordinator.start_session`. All config/env resolution has
        already happened by the time this is called; `sdk_kwargs` is the fully-
        resolved kwargs dict for the `ClaudeSDK` constructor.

        Returns (success, raw_error_message). On failure the SDK is removed from
        `_active_sdks` before returning, matching prior inline behavior.
        """
        if sdk_kwargs is None:
            raise ValueError("LocalBackend.start_session requires sdk_kwargs")

        sdk = self._sdk_factory(session_id=session_id, **sdk_kwargs)
        # Issue #707: Set auto-approval callback so can_use_tool can notify us
        if auto_approval_callback is not None:
            sdk.auto_approval_callback = auto_approval_callback
        self._active_sdks[session_id] = sdk

        try:
            started = await sdk.start()
        except Exception as e:
            logger.exception(f"SDK raised while starting session {session_id}")
            self._active_sdks.pop(session_id, None)
            return False, str(e)

        if not started:
            raw_error_message = getattr(
                sdk.info, "error_message", "Unknown error occurred while starting Claude Code"
            )
            self._active_sdks.pop(session_id, None)
            return False, raw_error_message

        return True, None

    async def send_message(
        self, session_id: str, message: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return False
        return await sdk.send_message(message, metadata=metadata)

    async def interrupt_session(self, session_id: str) -> bool:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return False
        return await sdk.interrupt_session()

    async def set_permission_mode(self, session_id: str, mode: str) -> bool:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return False
        return await sdk.set_permission_mode(mode)

    async def set_model(self, session_id: str, model: str) -> bool:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return False
        return await sdk.set_model(model)

    async def update_session_name(self, session_id: str, name: str) -> bool:
        # LOCAL renames are coordinator-local (session_manager) and never dispatch
        # through the backend — see SessionCoordinator.update_session_name. Present
        # only for Protocol conformance.
        return True

    async def update_session_config(self, session_id: str, raw_payload: dict[str, Any]) -> bool:
        # LOCAL config updates are coordinator-local (session_manager) and never
        # dispatch through the backend — see SessionCoordinator.update_session_config.
        # Present only for Protocol conformance.
        return True

    async def resolve_permission(
        self, session_id: str, request_id: str, response: dict[str, Any]
    ) -> bool:
        # LOCAL resolution goes through PermissionService.resolve() directly —
        # SessionCoordinator/web_server own the PermissionService instance, so this
        # is not exercised on LocalBackend (present for Protocol conformance).
        raise NotImplementedError("LocalBackend permission resolution goes through PermissionService")

    async def get_mcp_status(self, session_id: str) -> dict[str, Any]:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return {"servers": []}
        return await sdk.get_mcp_status()

    async def get_context_usage(self, session_id: str) -> dict[str, Any]:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return {}
        return await sdk.get_context_usage()

    async def toggle_mcp_server(self, session_id: str, name: str, enabled: bool) -> None:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            raise RuntimeError(f"No active SDK found for session {session_id}")
        await sdk.toggle_mcp_server(name, enabled)

    async def reconnect_mcp_server(self, session_id: str, name: str) -> None:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            raise RuntimeError(f"No active SDK found for session {session_id}")
        await sdk.reconnect_mcp_server(name)

    async def add_directory(self, session_id: str, directory: str) -> dict[str, Any]:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            raise ValueError(f"No active SDK found for session {session_id}")
        return await sdk.register_repo_root(directory)

    async def disconnect_session(self, session_id: str) -> bool:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return True  # Already disconnected
        success = await sdk.disconnect()
        if success:
            self._active_sdks.pop(session_id, None)
        return success

    async def terminate_session(self, session_id: str) -> bool:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return True
        result = await sdk.terminate()
        self._active_sdks.pop(session_id, None)
        return result

    async def get_session_runtime_info(self, session_id: str) -> dict[str, Any] | None:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return None
        info = sdk.get_info()
        info.update({"queue_size": sdk.get_queue_size()})
        return info

    async def wait_for_session_ready(self, session_id: str, timeout: float = 60.0) -> bool:
        sdk = self._active_sdks.get(session_id)
        if not sdk:
            return False
        if sdk.is_running():
            return True
        return await sdk.wait_until_ready(timeout=timeout)
