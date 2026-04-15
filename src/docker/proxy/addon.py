import json
import logging
import os
import time
from pathlib import Path

from mitmproxy import ctx, http

ALLOWLIST_PATH = "/etc/proxy/allowlist.json"
CREDENTIALS_PATH = "/etc/proxy/credentials.json"
LOG_DIR = "/var/log/proxy"


class DomainFilter:
    def __init__(self):
        self.allowed_domains: set[str] = set()
        # Reverse lookup: placeholder value → credential entry (name, host_pattern, header, format, real_value)
        # Injection fires only when a request header contains a known placeholder — never on every host match.
        self.placeholder_map: dict[str, dict] = {}
        self.logger = logging.getLogger("proxy.filter")
        self.session_id = os.environ.get("PROXY_SESSION_ID", "unknown")
        self._log_file = None
        if Path(LOG_DIR).is_dir():
            log_path = Path(LOG_DIR) / "access.log"
            self._log_file = open(log_path, "a", buffering=1)  # line-buffered

    def load(self, loader):
        data = json.loads(Path(ALLOWLIST_PATH).read_text())
        self.allowed_domains = set(data.get("domains", []))
        ctx.log.info(f"Loaded {len(self.allowed_domains)} allowed domains")
        self._load_credentials()

    def _load_credentials(self) -> None:
        """Load credentials from /etc/proxy/credentials.json (optional — no-op if absent).

        Builds a reverse lookup keyed by placeholder value so injection only fires when
        an outgoing request header actually contains the known placeholder string.
        """
        creds_path = Path(CREDENTIALS_PATH)
        if not creds_path.exists():
            return
        try:
            data = json.loads(creds_path.read_text())
            self.placeholder_map = {}
            for entry in data.get("credentials", []):
                placeholder = entry.get("placeholder")
                injection = entry.get("injection", {})
                if not placeholder or not injection:
                    continue
                self.placeholder_map[placeholder] = {
                    "name": entry.get("name", placeholder),
                    "host_pattern": entry.get("host_pattern"),  # optional guard
                    "header": injection["header"],
                    "format": injection.get("format", "{value}"),
                    "real_value": injection["real_value"],
                }
            ctx.log.info(f"Loaded {len(self.placeholder_map)} credential placeholder(s)")
        except Exception as exc:
            ctx.log.error(f"Failed to load credentials: {exc}")

    def _is_allowed(self, host: str) -> bool:
        # Exact match or subdomain match
        for domain in self.allowed_domains:
            if host == domain or host.endswith("." + domain):
                return True
        return False

    def _host_matches_pattern(self, host: str, pattern: str | None) -> bool:
        """Return True if host matches pattern (or pattern is None — no guard)."""
        if pattern is None:
            return True
        return host == pattern or host.endswith("." + pattern)

    def _inject_credentials(self, flow: http.HTTPFlow) -> str | None:
        """Scan request headers for known placeholder values and inject real credentials.

        Injection fires ONLY when a header value contains a known placeholder string.
        Unauthenticated calls (no matching placeholder) are untouched.
        Returns the credential name if injection occurred, None otherwise.
        The real_value is never logged.
        """
        host = flow.request.pretty_host
        for placeholder, cred in self.placeholder_map.items():
            for hdr_name, hdr_value in list(flow.request.headers.items()):
                if placeholder not in hdr_value:
                    continue
                # Optional host_pattern guard: reject placeholder seen on wrong host
                if not self._host_matches_pattern(host, cred["host_pattern"]):
                    ctx.log.warn(
                        f"Placeholder '{cred['name']}' seen on unexpected host '{host}' "
                        f"(expected '{cred['host_pattern']}') — not injecting"
                    )
                    continue
                # Replace the entire header value with the formatted real credential
                injected_value = cred["format"].replace("{value}", cred["real_value"])
                flow.request.headers[hdr_name] = injected_value
                return cred["name"]
        return None

    def _write_access_log(self, flow: http.HTTPFlow, allowed: bool, credential_used: str | None = None) -> None:
        if not self._log_file:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "session_id": self.session_id,
            "scheme": flow.request.scheme,
            "host": flow.request.pretty_host,
            "port": flow.request.port,
            "method": flow.request.method,
            "path": flow.request.path,
            "status": flow.response.status_code if flow.response else 0,
            "bytes": len(flow.response.content) if flow.response and flow.response.content else 0,
            "allowed": allowed,
            "credential_used": credential_used,  # name string or null — never the real_value
        }
        self._log_file.write(json.dumps(entry) + "\n")

    def request(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host
        client_ip = flow.client_conn.peername[0] if flow.client_conn.peername else "unknown"

        if self._is_allowed(host):
            credential_name = self._inject_credentials(flow)
            if credential_name:
                ctx.log.info(f"ALLOW {client_ip} -> {host}{flow.request.path} [cred:{credential_name}]")
            else:
                ctx.log.info(f"ALLOW {client_ip} -> {host}{flow.request.path}")
            flow.metadata["credential_used"] = credential_name
        else:
            ctx.log.warn(f"DENY {client_ip} -> {host}{flow.request.path}")
            flow.response = http.Response.make(
                403,
                json.dumps({"error": "domain_blocked", "domain": host}),
                {"Content-Type": "application/json"},
            )
            flow.metadata["denied"] = True
            self._write_access_log(flow, allowed=False)

    def response(self, flow: http.HTTPFlow) -> None:
        # Skip flows already logged as denied in request() to avoid double-logging.
        if flow.metadata.get("denied"):
            return
        self._write_access_log(
            flow,
            allowed=True,
            credential_used=flow.metadata.get("credential_used"),
        )


addons = [DomainFilter()]
