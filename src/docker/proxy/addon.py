"""
Proxy addon for Claude WebUI (issue #1134).

Replaces the static credentials.json model with a REST-fetch approach:
  - load(): reads session_token + session_id from mounted files, fetches secrets
            from the WebUI resolve endpoint via httpx.
  - request(): blocks non-allowlisted hosts; refreshes expiring OAuth2 tokens
               (per-placeholder asyncio.Lock prevents parallel refresh);
               injects placeholder → real-value via type-specific handler.
  - response(): scrubs raw values from inbound responses (defense-in-depth,
                all types); writes back captured tokens via session-scoped PATCH.

Typed injection/scrub handlers are inlined (addon.py must be self-contained
inside the Docker image — it cannot import from the host src/ tree).
"""

import asyncio
import base64
import ipaddress
import json
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from mitmproxy import ctx, http, tcp

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

ALLOWLIST_PATH = "/etc/proxy/allowlist.json"
SESSION_TOKEN_PATH = "/etc/proxy/session_token"
SESSION_ID_PATH = "/etc/proxy/session_id"
LOG_DIR = "/var/log/proxy"
WEBUI_BASE_URL = os.environ.get("WEBUI_BASE_URL", "http://host.docker.internal:8000")
SOCKS5_LOG_FILENAME = "socks5.log"

_BINARY_CONTENT_TYPES = frozenset({
    "image/", "audio/", "video/",
    "application/octet-stream", "application/zip",
    "application/gzip", "application/pdf",
})


# ---------------------------------------------------------------------------
# Injection helpers — one function per secret type
# ---------------------------------------------------------------------------

def _inject_generic(flow: http.HTTPFlow, record: dict, placeholder: str) -> bool:
    """Replace placeholder anywhere in headers, query string, and body."""
    value = record.get("value", "")
    if not value:
        return False
    modified = False
    for name, val in list(flow.request.headers.items()):
        if placeholder in val:
            flow.request.headers[name] = val.replace(placeholder, value)
            modified = True
    qs = flow.request.query_string
    if isinstance(qs, bytes):
        qs = qs.decode("utf-8", errors="replace")
    if placeholder in qs:
        flow.request.query_string = qs.replace(placeholder, value)
        modified = True
    if flow.request.content and placeholder.encode() in flow.request.content:
        flow.request.content = flow.request.content.replace(
            placeholder.encode(), value.encode()
        )
        modified = True
    return modified


def _inject_bearer(flow: http.HTTPFlow, record: dict, placeholder: str) -> bool:
    """Replace entire header value with 'Bearer {value}' when placeholder found."""
    value = record.get("value", "")
    if not value:
        return False
    modified = False
    for name, val in list(flow.request.headers.items()):
        if placeholder in val:
            flow.request.headers[name] = f"Bearer {value}"
            modified = True
    return modified


def _inject_basic_auth(flow: http.HTTPFlow, record: dict, placeholder: str) -> bool:
    """Replace header containing placeholder with 'Basic base64(username:password)'."""
    username = record.get("username") or ""
    password = record.get("value", "")
    if not password:
        return False
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    basic = f"Basic {encoded}"
    modified = False
    for name, val in list(flow.request.headers.items()):
        if placeholder in val:
            flow.request.headers[name] = basic
            modified = True
    return modified


def _inject_api_key(flow: http.HTTPFlow, record: dict, placeholder: str) -> bool:
    """Inject at the configured location (header or query_param) per InjectionSpec."""
    value = record.get("value", "")
    if not value:
        return False
    injection = record.get("injection") or {}
    location = injection.get("location", "header")
    modified = False

    if location == "query_param":
        qs = flow.request.query_string
        if isinstance(qs, bytes):
            qs = qs.decode("utf-8", errors="replace")
        if placeholder in qs:
            flow.request.query_string = qs.replace(placeholder, value)
            modified = True
    else:
        prefix = injection.get("prefix", "Bearer")
        formatted = f"{prefix} {value}".strip() if prefix else value
        for name, val in list(flow.request.headers.items()):
            if placeholder in val:
                flow.request.headers[name] = formatted
                modified = True
    return modified


_INJECT_DISPATCH: dict = {
    "generic": _inject_generic,
    "api_key": _inject_api_key,
    "bearer": _inject_bearer,
    "basic_auth": _inject_basic_auth,
    "oauth2": _inject_bearer,   # OAuth2 access token injects same as bearer
    "ssh": lambda _f, _r, _p: False,
}


# ---------------------------------------------------------------------------
# Scrub helpers — defense-in-depth for all types
# ---------------------------------------------------------------------------

def _is_binary_response(flow: http.HTTPFlow) -> bool:
    if not flow.response:
        return True
    ct = flow.response.headers.get("content-type", "")
    return any(ct.startswith(bt) for bt in _BINARY_CONTENT_TYPES)


def _scrub_response_headers(flow: http.HTTPFlow, value: str, placeholder: str) -> bool:
    if not flow.response:
        return False
    modified = False
    for name, val in list(flow.response.headers.items()):
        if value in val:
            flow.response.headers[name] = val.replace(value, placeholder)
            modified = True
    return modified


def _scrub_response_body(flow: http.HTTPFlow, value: str, placeholder: str) -> bool:
    if not flow.response or not flow.response.content:
        return False
    if _is_binary_response(flow):
        return False
    enc_val = value.encode()
    if enc_val not in flow.response.content:
        return False
    flow.response.content = flow.response.content.replace(enc_val, placeholder.encode())
    return True


def _capture_from_response(flow: http.HTTPFlow, record: dict) -> str | None:
    """Try to extract a new token value from the response using configured matchers."""
    scrub_cfg = record.get("scrub") or {}
    if not scrub_cfg:
        return None

    url_path_pattern = scrub_cfg.get("url_path")
    if url_path_pattern and url_path_pattern not in flow.request.path:
        return None

    if not flow.response or not flow.response.content:
        return None

    # JSON-path matcher (simple dotted-path only, e.g. "$.access_token")
    jsonpath_expr = scrub_cfg.get("matcher_jsonpath")
    if jsonpath_expr:
        try:
            body = json.loads(flow.response.content)
            parts = jsonpath_expr.lstrip("$.").split(".")
            node = body
            for part in parts:
                node = node[part]
            if isinstance(node, str):
                return node
        except Exception:
            pass

    # Regex matcher
    regex_expr = scrub_cfg.get("matcher_regex")
    if regex_expr:
        try:
            text = flow.response.content.decode("utf-8", errors="replace")
            m = re.search(regex_expr, text)
            if m:
                return m.group(1) if m.lastindex else m.group(0)
        except Exception:
            pass

    return None


def _scrub_everywhere(
    flow: http.HTTPFlow, record: dict, placeholder: str
) -> tuple[bool, str | None]:
    """Scrub raw value from response headers and body. Return (modified, captured)."""
    value = record.get("value", "")
    if not value:
        return False, None

    modified = False
    modified |= _scrub_response_headers(flow, value, placeholder)
    modified |= _scrub_response_body(flow, value, placeholder)

    captured = None
    scrub_cfg = record.get("scrub") or {}
    if scrub_cfg.get("update_on_change"):
        captured = _capture_from_response(flow, record)

    return modified, captured


# ---------------------------------------------------------------------------
# OAuth2 refresh helpers
# ---------------------------------------------------------------------------

def _should_refresh(record: dict) -> bool:
    if record.get("type") != "oauth2":
        return False
    refresh_cfg = record.get("refresh") or {}
    expires_at_str = refresh_cfg.get("expires_at")
    if not expires_at_str:
        return False
    try:
        exp = datetime.fromisoformat(
            expires_at_str.replace("Z", "+00:00") if expires_at_str.endswith("Z") else expires_at_str
        )
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        buffer = int(refresh_cfg.get("buffer_seconds", 60))
        return datetime.now(UTC) >= exp - timedelta(seconds=buffer)
    except Exception:
        return False


async def _do_refresh(record: dict, get_partner_value) -> dict:
    """POST to token_url, return dict of updated fields."""
    r = record.get("refresh") or {}
    token_url = r.get("token_url", "")
    client_id = r.get("client_id", "")
    refresh_token_name = r.get("refresh_token_secret_name", "")
    client_secret_name = r.get("client_secret_secret_name")

    if not token_url or not refresh_token_name:
        raise ValueError("oauth2 refresh requires token_url and refresh_token_secret_name")

    refresh_token = await get_partner_value(refresh_token_name)
    if not refresh_token:
        raise ValueError(f"refresh token '{refresh_token_name}' is empty or missing")

    data: dict = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret_name:
        cs = await get_partner_value(client_secret_name)
        if cs:
            data["client_secret"] = cs

    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=data, timeout=30.0)
        resp.raise_for_status()
        body = resp.json()

    new_access = body.get("access_token")
    if not new_access:
        raise ValueError(f"token_url response missing access_token: {list(body.keys())}")

    updates: dict = {"value": new_access}
    updated_refresh = dict(r)

    new_refresh_token = body.get("refresh_token")
    if new_refresh_token:
        updates["_new_refresh_token"] = new_refresh_token
        updates["_new_refresh_token_name"] = refresh_token_name

    expires_in = body.get("expires_in")
    if expires_in:
        new_exp = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        updated_refresh["expires_at"] = new_exp.isoformat()
        updates["refresh"] = updated_refresh

    return updates


# ---------------------------------------------------------------------------
# Main addon class
# ---------------------------------------------------------------------------

class ProxyAddon:
    """Typed-secret proxy addon. Fetches secrets via REST at load; injects/scrubs per-type."""

    def __init__(self):
        self._session_token: str = ""
        self._session_id: str = ""
        self._records: dict[str, dict] = {}        # placeholder → full record dict
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self.allowed_domains: set[str] = set()
        self.logger = logging.getLogger("proxy.addon")
        self._log_file = None
        self._socks5_log_file = None
        if Path(LOG_DIR).is_dir():
            log_path = Path(LOG_DIR) / "access.log"
            self._log_file = open(log_path, "a", buffering=1)  # line-buffered  # noqa: SIM115
            socks5_path = Path(LOG_DIR) / SOCKS5_LOG_FILENAME
            self._socks5_log_file = open(socks5_path, "a", buffering=1)  # noqa: SIM115

    async def load(self, loader) -> None:
        """Read session credentials and fetch secrets from WebUI resolve endpoint."""
        try:
            self._session_token = Path(SESSION_TOKEN_PATH).read_text().strip()
            self._session_id = Path(SESSION_ID_PATH).read_text().strip()
        except FileNotFoundError as exc:
            ctx.log.error(f"[proxy] Session files not found: {exc}. No credentials loaded.")
            return

        data = json.loads(Path(ALLOWLIST_PATH).read_text())
        self.allowed_domains = set(data.get("domains", []))
        ctx.log.info(f"[proxy] Loaded {len(self.allowed_domains)} allowed domains")

        try:
            self._records = await self._fetch_resolve()
            self._refresh_locks = {ph: asyncio.Lock() for ph in self._records}
            ctx.log.info(
                f"[proxy] Loaded {len(self._records)} secret(s) for session {self._session_id}"
            )
        except Exception as exc:
            ctx.log.error(f"[proxy] Failed to fetch secrets for session {self._session_id}: {exc}")

    async def request(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host
        client_ip = (
            flow.client_conn.peername[0] if flow.client_conn.peername else "unknown"
        )

        if not self._is_allowed(host):
            ctx.log.warn(f"[proxy] DENY {client_ip} -> {host}{flow.request.path}")
            flow.response = http.Response.make(
                403,
                json.dumps({"error": "domain_blocked", "domain": host}),
                {"Content-Type": "application/json"},
            )
            flow.metadata["denied"] = True
            self._write_access_log(flow, allowed=False)
            return

        credential_used = None
        for ph, record in self._records.items():
            secret_type = record.get("type", "generic")

            # Per-placeholder lock prevents parallel refresh on the same token.
            if secret_type == "oauth2" and _should_refresh(record):
                lock = self._refresh_locks.get(ph)
                if lock:
                    async with lock:
                        if _should_refresh(record):   # double-check after acquiring lock
                            try:
                                updates = await _do_refresh(record, self._get_partner_value)
                                record.update(updates)
                                await self._patch_secret(record["name"], updates)
                                # Update rotated refresh token record if present
                                new_rt_name = updates.get("_new_refresh_token_name")
                                new_rt_val = updates.get("_new_refresh_token")
                                if new_rt_name and new_rt_val:
                                    for rec2 in self._records.values():
                                        if rec2.get("name") == new_rt_name:
                                            rec2["value"] = new_rt_val
                                            await self._patch_secret(new_rt_name, {"value": new_rt_val})
                                            break
                            except Exception as exc:
                                ctx.log.warn(
                                    f"[proxy] Refresh failed for {record['name']}: {exc}"
                                )
                                await self._emit_ui_event("secret_refresh_failed", {
                                    "name": record["name"],
                                    "error": str(exc),
                                })

            inject_fn = _INJECT_DISPATCH.get(secret_type, _inject_generic)
            if inject_fn(flow, record, ph):
                credential_used = record.get("name")

        flow.metadata["credential_used"] = credential_used
        if credential_used:
            ctx.log.info(
                f"[proxy] ALLOW {client_ip} -> {host}{flow.request.path} [cred:{credential_used}]"
            )
        else:
            ctx.log.info(f"[proxy] ALLOW {client_ip} -> {host}{flow.request.path}")

    async def response(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("denied"):
            return

        for ph, record in self._records.items():
            _modified, captured = _scrub_everywhere(flow, record, ph)
            if captured is not None:
                record["value"] = captured
                await self._patch_secret(record["name"], {"value": captured})

        self._write_access_log(
            flow,
            allowed=True,
            credential_used=flow.metadata.get("credential_used"),
        )

    def tcp_start(self, flow: tcp.TCPFlow) -> None:
        """SOCKS5 allowlist enforcement for non-HTTP TCP connections (issue #1052).

        Called by mitmproxy after the SOCKS5 CONNECT handshake, before connecting
        to the upstream server. Enforces the same allowlist as HTTP/HTTPS:
          - IP-literal destinations are always rejected (fail-closed).
          - Hostname destinations must match the in-memory allowed_domains set.
        All decisions are logged to socks5.log.
        """
        try:
            addr = flow.server_conn.address
        except AttributeError:
            # Destination metadata unavailable — fail-closed.
            ctx.log.warn("[proxy:socks5] Missing server_conn.address — killing flow")
            self._write_socks5_log(host="unknown", port=0, allowed=False, reason="no_address")
            flow.kill()
            return

        if not addr:
            ctx.log.warn("[proxy:socks5] Empty server address — killing flow")
            self._write_socks5_log(host="unknown", port=0, allowed=False, reason="empty_address")
            flow.kill()
            return

        host = addr[0] if isinstance(addr, (tuple, list)) else str(addr)
        port = addr[1] if isinstance(addr, (tuple, list)) and len(addr) > 1 else 0

        # Reject IP literals unconditionally — SOCKS5 should only be used for
        # named hosts (SSH via git/ssh, not raw IP connections).
        try:
            ipaddress.ip_address(host)
            ctx.log.warn(f"[proxy:socks5] DENY IP literal {host}:{port}")
            self._write_socks5_log(host=host, port=port, allowed=False, reason="ip_literal")
            flow.kill()
            return
        except ValueError:
            pass  # not an IP — continue to hostname check

        if not self._is_allowed(host):
            ctx.log.warn(f"[proxy:socks5] DENY {host}:{port} (not in allowlist)")
            self._write_socks5_log(host=host, port=port, allowed=False, reason="not_allowlisted")
            flow.kill()
            return

        ctx.log.info(f"[proxy:socks5] ALLOW {host}:{port}")
        self._write_socks5_log(host=host, port=port, allowed=True, reason="ok")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_resolve(self) -> dict[str, dict]:
        """GET /api/sessions/{id}/secrets/resolve → {placeholder: record_dict}."""
        url = f"{WEBUI_BASE_URL}/api/sessions/{self._session_id}/secrets/resolve"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {self._session_token}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        return {
            secret["placeholder"]: secret
            for secret in data.get("secrets", [])
            if secret.get("placeholder")
        }

    async def _get_partner_value(self, name: str) -> str:
        """Return the value of a sibling record (e.g. refresh_token) by name."""
        for record in self._records.values():
            if record.get("name") == name:
                return record.get("value", "")
        raise KeyError(f"Partner secret '{name}' not found in loaded records")

    async def _patch_secret(self, name: str, updates: dict) -> None:
        """PATCH /api/sessions/{id}/secrets/{name} with updated fields."""
        body: dict = {}
        if "value" in updates:
            body["value"] = updates["value"]
        if "refresh" in updates:
            body["refresh"] = updates["refresh"]
        if not body:
            return
        url = f"{WEBUI_BASE_URL}/api/sessions/{self._session_id}/secrets/{name}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.patch(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {self._session_token}"},
                    timeout=10.0,
                )
                if resp.status_code not in (200, 202, 204):
                    ctx.log.warn(f"[proxy] PATCH {name} returned {resp.status_code}")
        except Exception as exc:
            ctx.log.warn(f"[proxy] PATCH {name} failed: {exc}")

    async def _emit_ui_event(self, event_type: str, data: dict) -> None:
        """POST /api/sessions/{id}/events with a proxy-originated event."""
        url = f"{WEBUI_BASE_URL}/api/sessions/{self._session_id}/events"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    url,
                    json={"type": event_type, "data": data},
                    headers={"Authorization": f"Bearer {self._session_token}"},
                    timeout=5.0,
                )
        except Exception as exc:
            ctx.log.warn(f"[proxy] Failed to emit UI event {event_type}: {exc}")

    def _is_allowed(self, host: str) -> bool:
        return any(
            host == domain or host.endswith("." + domain)
            for domain in self.allowed_domains
        )

    def _inject_credentials(self, flow: http.HTTPFlow) -> str | None:
        """Compatibility shim for tests — dispatches to typed inject handler."""
        for ph, record in self._records.items():
            secret_type = record.get("type", "generic")
            inject_fn = _INJECT_DISPATCH.get(secret_type, _inject_generic)
            if inject_fn(flow, record, ph):
                return record.get("name")
        return None

    def _write_access_log(
        self,
        flow: http.HTTPFlow,
        allowed: bool,
        credential_used: str | None = None,
    ) -> None:
        if not self._log_file:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "session_id": self._session_id,
            "scheme": flow.request.scheme,
            "host": flow.request.pretty_host,
            "port": flow.request.port,
            "method": flow.request.method,
            "path": flow.request.path,
            "status": flow.response.status_code if flow.response else 0,
            "bytes": len(flow.response.content) if flow.response and flow.response.content else 0,
            "allowed": allowed,
            "credential_used": credential_used,
        }
        self._log_file.write(json.dumps(entry) + "\n")

    def _write_socks5_log(
        self,
        host: str,
        port: int,
        allowed: bool,
        reason: str,
    ) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "session_id": self._session_id,
            "host": host,
            "port": port,
            "allowed": allowed,
            "reason": reason,
        }
        if self._socks5_log_file:
            self._socks5_log_file.write(json.dumps(entry) + "\n")
        else:
            # Log to access log if socks5 log not available
            ctx.log.info(f"[proxy:socks5] {json.dumps(entry)}")


# Backward-compat alias kept for legacy test imports
DomainFilter = ProxyAddon

addons = [ProxyAddon()]
