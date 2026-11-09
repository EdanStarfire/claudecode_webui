"""InboundManager: owns one InboundService per relay (issue #2103 stage 3a).

Lifecycle: ``start()`` in coordinator initialize (after the delivery gate exists), ``stop()`` in
cleanup BEFORE sessions are terminated. Credential / relay changes restart the relay's service
(``schedule_refresh``); a deleted relay's service is stopped and its state files removed.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import constants as const
from .service import InboundService, InstructionSink, TransportFactory
from .sink import enqueue_external_comm
from .slack_socket import SlackSocketTransport

if TYPE_CHECKING:
    from backend.relay.audit import RelayAuditLog
    from backend.relay.service import RelayService
    from backend.relay_manager import RelayManager
    from backend.session_manager import SessionManager

logger = logging.getLogger(__name__)


class InboundManager:
    def __init__(
        self,
        *,
        relay_manager: RelayManager,
        relay_service: RelayService,
        session_manager: SessionManager,
        audit: RelayAuditLog,
        relays_root: Path,
        transport_factory: TransportFactory | None = None,
        service_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._manager = relay_manager
        self._relay_service = relay_service
        self._sessions = session_manager
        self._audit = audit
        self._root = relays_root
        self._service_kwargs = service_kwargs or {}
        self._transport_factory = transport_factory or self._default_transport
        self.sink: InstructionSink | None = None  # set by the coordinator
        self.services: dict[str, InboundService] = {}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._started = False

    def _default_transport(self, relay_id: str) -> SlackSocketTransport:
        return SlackSocketTransport(lambda: self._manager.get_token(relay_id, "app"))

    async def _unwired_sink(self, *args, **kwargs):  # pragma: no cover - misconfiguration guard
        raise RuntimeError("inbound sink is not wired")

    def _build(self, relay_id: str) -> InboundService:
        return InboundService(
            relay_id,
            relay_manager=self._manager,
            relay_service=self._relay_service,
            session_manager=self._sessions,
            audit=self._audit,
            sink=self.sink or self._unwired_sink,
            relays_root=self._root,
            transport_factory=self._transport_factory,
            **self._service_kwargs,
        )

    def bind(self, coordinator) -> None:
        """Wire the delivery sink to the coordinator (called once from its constructor)."""
        self.sink = partial(enqueue_external_comm, coordinator)

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        async with self._lock:
            self._started = True
            try:
                for relay_id in list(self._manager.relays):
                    await self._start_one(relay_id)
            except Exception:  # inbound never blocks Backend start
                logger.exception("Relay inbound failed to start")

    async def _start_one(self, relay_id: str) -> None:
        service = self._build(relay_id)
        self.services[relay_id] = service
        try:
            await service.start()
        except Exception:
            logger.exception("Inbound service failed to start")
            self.services.pop(relay_id, None)

    async def stop(self) -> None:
        async with self._lock:
            self._started = False
            for task in list(self._tasks):
                task.cancel()
            for relay_id, service in list(self.services.items()):
                try:
                    await service.stop()
                except Exception:
                    logger.exception("Inbound service stop failed")
                self.services.pop(relay_id, None)

    async def refresh(self, relay_id: str) -> None:
        """Restart one relay's service after a credential / relay-setting change."""
        async with self._lock:
            if not self._started:
                return
            old = self.services.pop(relay_id, None)
            if old is not None:
                await old.stop()
            if relay_id in self._manager.relays:
                await self._start_one(relay_id)
            else:  # relay deleted: its inbound state goes with it (the audit log stays)
                for name in ("inbound_state.json", "subscriptions.json"):
                    (self._root / relay_id / name).unlink(missing_ok=True)

    def schedule_refresh(self, relay_id: str) -> None:
        """Sync trigger for RelayManager change listeners (never raises)."""
        try:
            task = asyncio.get_running_loop().create_task(self.refresh(relay_id))
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---------------------------------------------------------------- queries

    def status_for(self, relay_id: str) -> dict[str, Any]:
        service = self.services.get(relay_id)
        if service is None:
            return {"status": const.STATUS_STOPPED, "detail": None, "connected_at": None, "inflight": 0}
        return service.status_dict()

    def has_thread_grant(
        self, session_id: str, relay_id: str, channel: str, thread_ts: str
    ) -> bool:
        service = self.services.get(relay_id)
        return bool(service and service.has_thread_grant(session_id, channel, thread_ts))

    # ----------------------------------------------------------- session hooks

    async def _fan_out(self, method: str, session_id: str) -> None:
        for service in list(self.services.values()):
            try:
                await getattr(service, method)(session_id)
            except Exception:
                logger.exception("Inbound %s hook failed", method)

    async def on_session_deleted(self, session_id: str) -> None:
        await self._fan_out("on_session_deleted", session_id)

    async def on_session_disposed(self, session_id: str) -> None:
        await self._fan_out("on_session_disposed", session_id)

    async def on_session_started(self, session_id: str) -> None:
        await self._fan_out("on_session_started", session_id)
