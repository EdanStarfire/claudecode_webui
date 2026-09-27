"""DriverContext — the scenario driver's HTTP client and shared run state.

A plain `httpx.AsyncClient` wrapper against the Frontend API, authenticating
exactly like the browser does (`Authorization: Bearer <token>`, confirmed at
`src/routers/core.py`'s `auth_check()`). No `backend/` imports outside this
package's own core loop — every action is a real HTTP call.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx


class UnsafeTargetError(Exception):
    """Raised when the driver is pointed at a host that doesn't look like a
    loopback/private test instance and `--allow-remote` wasn't passed (AC1)."""


def _is_loopback_or_private(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        try:
            resolved = socket.gethostbyname(host)
            addr = ipaddress.ip_address(resolved)
        except (socket.gaierror, ValueError):
            return host in ("localhost",)
    return addr.is_loopback or addr.is_private


def guard_target(base_url: str, *, allow_remote: bool) -> None:
    """AC1's production guard. There is no reliable "is this prod" signal from
    the API itself, so the safety gate is host-shape-based plus an explicit
    opt-out — refuses anything that isn't loopback/private unless overridden.
    """
    if allow_remote:
        return
    host = urlparse(base_url).hostname or ""
    if not _is_loopback_or_private(host):
        raise UnsafeTargetError(
            f"Refusing to run against '{host}' — it does not look like a loopback/private "
            f"test instance. Pass --allow-remote to override this safety check."
        )


@dataclass
class DriverContext:
    base_url: str
    token: str | None = None
    http_timeout: float = 30.0
    client: httpx.AsyncClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self.client = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=self.http_timeout
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def get_json(self, path: str, *, params: dict | None = None, timeout: float | None = None) -> dict:
        resp = await self.client.get(path, params=params, timeout=timeout or self.http_timeout)
        resp.raise_for_status()
        return resp.json()

    async def post_json(self, path: str, *, json: dict | None = None, timeout: float | None = None) -> dict:
        resp = await self.client.post(path, json=json or {}, timeout=timeout or self.http_timeout)
        resp.raise_for_status()
        return resp.json()

    async def post_empty(self, path: str, *, timeout: float | None = None) -> dict:
        resp = await self.client.post(path, timeout=timeout or self.http_timeout)
        resp.raise_for_status()
        return resp.json()
