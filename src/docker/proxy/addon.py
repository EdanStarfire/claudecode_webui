"""
Proxy addon for Claude WebUI (issue #1134).

Replaces the static credentials.json model with a REST-fetch approach:
  - load(): reads session_token + session_id from mounted files, fetches secrets
            from the WebUI resolve endpoint via httpx.
  - requestheaders(): fires after client headers arrive, before upstream forwarding.
                      Enforces allowlist, refreshes expiring OAuth2 tokens, and
                      injects placeholder → real-value for all header/query locations.
  - request(): body-only injection (when flow.request.content is buffered). Under
               mitmproxy stream_large_bodies, content may be None — body injection
               no-ops in that case (companion issue tracks streamed-body scrub).
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
WEBUI_BASE_URL = os.environ.get("WEBUI_BASE_URL", "http://cc-webui.internal:8000")
SOCKS5_LOG_FILENAME = "socks5.log"

_BINARY_CONTENT_TYPES = frozenset({
    "image/", "audio/", "video/",
    "application/octet-stream", "application/zip",
    "application/gzip", "application/pdf",
})


# ---------------------------------------------------------------------------
# Injection helpers — one function per secret type
# ---------------------------------------------------------------------------

def _inject_generic_headers(flow: http.HTTPFlow, record: dict, placeholder: str) -> bool:
    """Replace placeholder in headers and query string (header phase only)."""
    value = record.get("value", "")
    if not value:
        return False
    modified = False
    for name, val in list(flow.request.headers.items()):
        if placeholder in val:
            flow.request.headers[name] = val.replace(placeholder, value)
            modified = True
    for k, v in list(flow.request.query.items()):
        if placeholder in v:
            flow.request.query[k] = v.replace(placeholder, value)
            modified = True
    return modified


def _inject_generic_body(flow: http.HTTPFlow, record: dict, placeholder: str) -> bool:
    """Replace placeholder in request body bytes (body phase only)."""
    value = record.get("value", "")
    if not value:
        return False
    if flow.request.content and placeholder.encode() in flow.request.content:
        flow.request.content = flow.request.content.replace(
            placeholder.encode(), value.encode()
        )
        return True
    return False


# Keep the original name as an alias so existing unit tests that import
# _inject_generic directly continue to work without modification.
def _inject_generic(flow: http.HTTPFlow, record: dict, placeholder: str) -> bool:
    """Replace placeholder anywhere in headers, query string, and body."""
    modified = _inject_generic_headers(flow, record, placeholder)
    modified |= _inject_generic_body(flow, record, placeholder)
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


def _inject_api_key_headers(flow: http.HTTPFlow, record: dict, placeholder: str) -> bool:
    """Inject at the configured location (header or query_param) per InjectionSpec."""
    value = record.get("value", "")
    if not value:
        return False
    injection = record.get("injection") or {}
    location = injection.get("location", "header")
    modified = False

    if location == "query_param":
        for k, v in list(flow.request.query.items()):
            if placeholder in v:
                flow.request.query[k] = v.replace(placeholder, value)
                modified = True
    else:
        prefix = injection.get("prefix", "Bearer")
        formatted = f"{prefix} {value}".strip() if prefix else value
        for name, val in list(flow.request.headers.items()):
            if placeholder in val:
                flow.request.headers[name] = formatted
                modified = True
    return modified


# Keep original name as alias for tests that import _inject_api_key directly.
_inject_api_key = _inject_api_key_headers

_INJECT_HEADERS_DISPATCH: dict = {
    "generic":    _inject_generic_headers,
    "api_key":    _inject_api_key_headers,
    "bearer":     _inject_bearer,
    "basic_auth": _inject_basic_auth,
    "oauth2":     _inject_bearer,   # OAuth2 access token injects same as bearer
    "ssh":        lambda _f, _r, _p: False,
}

_INJECT_BODY_DISPATCH: dict = {
    "generic": _inject_generic_body,
    # other types are header-only; absent here is intentional
}

# Legacy alias — keeps _inject_credentials shim and any external callers working.
_INJECT_DISPATCH: dict = {
    "generic":    _inject_generic,
    "api_key":    _inject_api_key_headers,
    "bearer":     _inject_bearer,
    "basic_auth": _inject_basic_auth,
    "oauth2":     _inject_bearer,
    "ssh":        lambda _f, _r, _p: False,
}


def _host_matches_targets(host: str, target_hosts: list) -> bool:
    """Return True if host matches any target. Empty list means unconstrained."""
    if not target_hosts:
        return True
    return any(host == th or host.endswith("." + th) for th in target_hosts)


# ---------------------------------------------------------------------------
# Scrub helpers — defense-in-depth for all types
# ---------------------------------------------------------------------------

def _is_binary_response(flow: http.HTTPFlow) -> bool:
    if not flow.response:
        return True
    ct = flow.response.headers.get("content-type", "")
    return any(ct.startswith(bt) for bt in _BINARY_CONTENT_TYPES)


def _is_sse_response(flow: http.HTTPFlow) -> bool:
    if not flow.response:
        return False
    ct = flow.response.headers.get("content-type", "")
    return ct.startswith("text/event-stream")


def _get_content_encoding(headers) -> str:
    return headers.get("content-encoding", "").strip().lower()


def _is_encoded_response(flow: http.HTTPFlow) -> bool:
    if not flow.response:
        return False
    return _get_content_encoding(flow.response.headers) not in ("", "identity")


def _is_encoded_request(flow: http.HTTPFlow) -> bool:
    return _get_content_encoding(flow.request.headers) not in ("", "identity")


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
# Streaming chunk-filter helpers (issue #1400)
# ---------------------------------------------------------------------------

def _make_chunk_filter(pairs: list[tuple[bytes, bytes]]):
    """Return a stateful per-chunk replacement callable for mitmproxy stream hooks.

    The callable accepts bytes chunks from mitmproxy's stream interface.
    The end-of-stream sentinel is b"" (per mitmproxy proxy/layers/http/__init__.py).
    Pairs are sorted longest-needle-first so greedy bytes.replace doesn't leave
    prefix remnants when two needles overlap (e.g. b"AB" shadowed by b"ABC").

    Performance note: uses N bytes.replace() calls per emit (one per pair).
    For typical 1-10 secrets this is dominated by network I/O. Aho-Corasick
    is the deferred optimisation if profiling reveals a hot path.
    """
    if not pairs:
        raise ValueError("pairs must be non-empty")
    # Longest needle first — prevents prefix-needle from eating bytes that
    # should match a longer needle.
    pairs = sorted(pairs, key=lambda p: len(p[0]), reverse=True)
    max_needle_len = max(len(needle) for needle, _ in pairs)
    overlap = max(0, max_needle_len - 1)
    carry = bytearray()

    def _filter_chunk(chunk: bytes):
        if chunk == b"":
            # End-of-stream: flush whatever remains in the carry buffer.
            result = bytes(carry)
            carry.clear()
            for needle, replacement in pairs:
                result = result.replace(needle, replacement)
            return result
        # Apply replacements to the full buf (carry + chunk) before splitting.
        # This ensures needles that span the carry/chunk boundary are caught.
        buf = bytes(carry) + chunk
        for needle, replacement in pairs:
            buf = buf.replace(needle, replacement)
        split_at = len(buf) - overlap
        if split_at > 0:
            carry[:] = buf[split_at:]
            return buf[:split_at]
        carry[:] = buf
        return []

    return _filter_chunk


def _make_unbuffered_chunk_filter(pairs: list[tuple[bytes, bytes]]):
    """Per-chunk replacement with NO carry buffer — for SSE/streamed responses.

    Cross-chunk-boundary needle detection is sacrificed for liveness;
    per-chunk single-shot scrubbing still applies as a defense-in-depth.
    Accepted residual risk: a secret that straddles two SSE chunk boundaries
    will not be redacted. SSE chunks are typically full text lines, so this
    boundary is bounded by the mitmproxy emit size, not arbitrary splits.
    """
    if not pairs:
        raise ValueError("pairs must be non-empty")
    pairs = sorted(pairs, key=lambda p: len(p[0]), reverse=True)

    def _filter_chunk(chunk: bytes) -> bytes:
        if chunk == b"":
            return b""
        buf = chunk
        for needle, replacement in pairs:
            buf = buf.replace(needle, replacement)
        return buf

    return _filter_chunk


def _build_request_pairs(records: dict, host: str) -> list[tuple[bytes, bytes]]:
    """Build (placeholder_bytes, value_bytes) pairs for outbound body injection.

    Only record types present in _INJECT_BODY_DISPATCH are eligible — the same
    gate that governs the buffered body path in request(). Currently "generic" only;
    adding a new body-injectable type to _INJECT_BODY_DISPATCH automatically
    includes it here.
    """
    result = []
    for ph, record in records.items():
        if not _host_matches_targets(host, record.get("target_hosts") or []):
            continue
        if record.get("type", "generic") not in _INJECT_BODY_DISPATCH:
            continue
        value = record.get("value", "")
        if not value:
            continue
        result.append((ph.encode(), value.encode()))
    return result


def _build_response_pairs(records: dict, host: str) -> list[tuple[bytes, bytes]]:
    """Build (value_bytes, placeholder_bytes) pairs for inbound body scrub.

    All record types are eligible — defense-in-depth: any secret value that
    reaches a response body (regardless of type) is replaced with its placeholder.
    """
    result = []
    for ph, record in records.items():
        if not _host_matches_targets(host, record.get("target_hosts") or []):
            continue
        value = record.get("value", "")
        if not value:
            continue
        result.append((value.encode(), ph.encode()))
    return result


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
        self._routing: dict = {"hostname_rewrites": {}, "virtual_key": None}
        self.allowed_domains: set[str] = set()
        self.logger = logging.getLogger("proxy.addon")
        self._log_file = None
        self._socks5_log_file = None
        if Path(LOG_DIR).is_dir():
            log_path = Path(LOG_DIR) / "access.log"
            self._log_file = open(log_path, "a", buffering=1)  # line-buffered  # noqa: SIM115
            socks5_path = Path(LOG_DIR) / SOCKS5_LOG_FILENAME
            self._socks5_log_file = open(socks5_path, "a", buffering=1)  # noqa: SIM115

    def load(self, loader) -> None:
        """Read session config files. Secret fetch happens in running()."""
        try:
            self._session_token = Path(SESSION_TOKEN_PATH).read_text().strip()
            self._session_id = Path(SESSION_ID_PATH).read_text().strip()
        except FileNotFoundError as exc:
            ctx.log.error(f"[proxy] Session files not found: {exc}. No credentials loaded.")
            return

        data = json.loads(Path(ALLOWLIST_PATH).read_text())
        self.allowed_domains = set(data.get("domains", []))
        ctx.log.info(f"[proxy] Loaded {len(self.allowed_domains)} allowed domains")

    async def running(self) -> None:
        """Fetch secrets and routing config after proxy is fully started (async-capable hook)."""
        if not self._session_id:
            return  # load() didn't complete successfully
        try:
            self._records = await self._fetch_resolve()
            self._refresh_locks = {ph: asyncio.Lock() for ph in self._records}
            ctx.log.info(
                f"[proxy] Loaded {len(self._records)} secret(s) for session {self._session_id}"
            )
        except Exception as exc:
            ctx.log.error(f"[proxy] Failed to fetch secrets for session {self._session_id}: {exc}")

        try:
            self._routing = await self._fetch_routing()
            if self._routing["hostname_rewrites"]:
                ctx.log.info(
                    f"[proxy] Routing enabled: {self._routing['hostname_rewrites']}"
                )
        except Exception as exc:
            ctx.log.warn(
                f"[proxy] Failed to fetch routing for session {self._session_id}: {exc}"
            )
            self._routing = {"hostname_rewrites": {}, "virtual_key": None}

    async def requestheaders(self, flow: http.HTTPFlow) -> None:
        """Fires after client headers arrive, before upstream forwarding.

        Performs LiteLLM hostname rewrite (Phase 3), allowlist enforcement,
        OAuth2 refresh, and all header/query injection. Body injection is
        deferred to request() where content is available (or no-ops when streaming).
        """
        # ── LiteLLM hostname rewrite (Phase 3) ────────────────────────────────
        # Applied before the allowlist check so the rewritten host
        # (cc-webui.internal) is what gets allowlisted — it is already present
        # in allowlist.json.
        rewrites = self._routing["hostname_rewrites"]
        vkey = self._routing.get("virtual_key")
        src_host = flow.request.pretty_host
        if src_host in rewrites and vkey:
            target = rewrites[src_host]              # e.g. "cc-webui.internal:4000"
            host_part, _, port_str = target.partition(":")
            port = int(port_str) if port_str else 80
            flow.request.host = host_part
            flow.request.port = port
            flow.request.scheme = "http"             # LiteLLM is plain HTTP on the host
            flow.request.headers["x-api-key"] = vkey
            flow.request.headers["Authorization"] = f"Bearer {vkey}"
            flow.metadata["routed_via_litellm"] = True
            ctx.log.info(f"[proxy] Routed {src_host} -> {target}")

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
            target_hosts = record.get("target_hosts") or []
            if not _host_matches_targets(host, target_hosts):
                continue
            # Sentinel guard: when the request was rewritten to LiteLLM, skip
            # secret injection so Anthropic-targeted secrets don't overwrite the
            # virtual key that was just installed by the rewrite block above.
            if flow.metadata.get("routed_via_litellm"):
                continue
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

            inject_fn = _INJECT_HEADERS_DISPATCH.get(secret_type, _inject_generic_headers)
            if inject_fn(flow, record, ph):
                credential_used = record.get("name")

        flow.metadata["credential_used"] = credential_used
        if credential_used:
            ctx.log.info(
                f"[proxy] ALLOW {client_ip} -> {host}{flow.request.path} [cred:{credential_used}]"
            )
        else:
            ctx.log.info(f"[proxy] ALLOW {client_ip} -> {host}{flow.request.path}")

        # Install per-chunk body-injection filter for streamed requests (#1400).
        # Header-side substitution above already covers headers/query; this covers
        # bodies that mitmproxy streams (flow.request.content will be None in
        # request() under stream_large_bodies).
        request_pairs = _build_request_pairs(self._records, host)
        if request_pairs and not _is_encoded_request(flow):
            flow.request.stream = _make_chunk_filter(request_pairs)

    async def request(self, flow: http.HTTPFlow) -> None:
        """Body-only injection phase. Allowlist and OAuth refresh already handled in requestheaders()."""
        if flow.metadata.get("denied"):
            return
        host = flow.request.pretty_host
        for ph, record in self._records.items():
            if not _host_matches_targets(host, record.get("target_hosts") or []):
                continue
            inject_fn = _INJECT_BODY_DISPATCH.get(record.get("type", "generic"))
            if inject_fn is None:
                continue
            inject_fn(flow, record, ph)

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        """Install per-chunk scrub filter for streamed responses (#1400).

        Fires when response headers arrive, before body streaming begins.
        Skips installation for:
          - denied flows (set in requestheaders())
          - binary responses (Content-Type in _BINARY_CONTENT_TYPES)
          - flows with no records targeting this host
        """
        if flow.metadata.get("denied"):
            return
        if _is_binary_response(flow):
            return
        if _is_encoded_response(flow):
            return
        response_pairs = _build_response_pairs(
            self._records, flow.request.pretty_host
        )
        if not response_pairs:
            return
        # Issue #1425: SSE responses must not buffer carry bytes — use unbuffered filter.
        if _is_sse_response(flow):
            flow.response.stream = _make_unbuffered_chunk_filter(response_pairs)
        else:
            flow.response.stream = _make_chunk_filter(response_pairs)

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

    async def _fetch_json(self, path: str) -> dict:
        """GET {WEBUI_BASE_URL}{path} with session Bearer auth. Returns parsed JSON."""
        url = f"{WEBUI_BASE_URL}{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {self._session_token}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()

    async def _fetch_resolve(self) -> dict[str, dict]:
        """GET /api/sessions/{id}/secrets/resolve → {placeholder: record_dict}."""
        data = await self._fetch_json(f"/api/sessions/{self._session_id}/secrets/resolve")
        return {
            secret["placeholder"]: secret
            for secret in data.get("secrets", [])
            if secret.get("placeholder")
        }

    async def _fetch_routing(self) -> dict:
        """GET /api/sessions/{id}/routing → {hostname_rewrites, virtual_key}."""
        return await self._fetch_json(f"/api/sessions/{self._session_id}/routing")

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
        if "*" in self.allowed_domains:
            return True
        return any(
            host == domain or host.endswith("." + domain)
            for domain in self.allowed_domains
        )

    def _inject_credentials(self, flow: http.HTTPFlow) -> str | None:
        """Compatibility shim for tests — dispatches through both header and body maps."""
        for ph, record in self._records.items():
            secret_type = record.get("type", "generic")
            header_fn = _INJECT_HEADERS_DISPATCH.get(secret_type, _inject_generic_headers)
            body_fn = _INJECT_BODY_DISPATCH.get(secret_type)
            modified = header_fn(flow, record, ph)
            if body_fn:
                modified |= body_fn(flow, record, ph)
            if modified:
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
