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
        self.credentials: dict[str, dict] = {}  # host_pattern → {header, value, name}
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
        """Load credentials from /etc/proxy/credentials.json (optional — no-op if absent)."""
        creds_path = Path(CREDENTIALS_PATH)
        if not creds_path.exists():
            return
        try:
            data = json.loads(creds_path.read_text())
            self.credentials = {
                entry["host_pattern"]: {
                    "header": entry["header"],
                    "value": entry["value"],
                    "name": entry["name"],
                }
                for entry in data.get("credentials", [])
            }
            ctx.log.info(f"Loaded {len(self.credentials)} credential entries")
        except Exception as exc:
            ctx.log.error(f"Failed to load credentials: {exc}")

    def _is_allowed(self, host: str) -> bool:
        # Exact match or subdomain match
        for domain in self.allowed_domains:
            if host == domain or host.endswith("." + domain):
                return True
        return False

    def _match_credential(self, host: str) -> dict | None:
        """Return credential entry whose host_pattern matches host, or None."""
        for pattern, cred in self.credentials.items():
            if host == pattern or host.endswith("." + pattern):
                return cred
        return None

    def _inject_credentials(self, flow: http.HTTPFlow) -> str | None:
        """Strip any existing credential header and inject the real value.

        Returns the credential name if injection occurred, None otherwise.
        The credential value is never logged.
        """
        cred = self._match_credential(flow.request.pretty_host)
        if cred is None:
            return None
        header = cred["header"]
        # Strip any existing header the agent may have sent (case-insensitive)
        flow.request.headers.pop(header, None)
        # Inject real value
        flow.request.headers[header] = cred["value"]
        return cred["name"]

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
            "credential_used": credential_used,
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
