import json
import logging
import os
import time
from pathlib import Path

from mitmproxy import ctx, http

ALLOWLIST_PATH = "/etc/proxy/allowlist.json"
LOG_DIR = "/var/log/proxy"


class DomainFilter:
    def __init__(self):
        self.allowed_domains: set[str] = set()
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

    def _is_allowed(self, host: str) -> bool:
        # Exact match or subdomain match
        for domain in self.allowed_domains:
            if host == domain or host.endswith("." + domain):
                return True
        return False

    def _write_access_log(self, flow: http.HTTPFlow, allowed: bool) -> None:
        if not self._log_file:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "session_id": self.session_id,
            "host": flow.request.pretty_host,
            "method": flow.request.method,
            "path": flow.request.path,
            "status": flow.response.status_code if flow.response else 0,
            "bytes": len(flow.response.content) if flow.response and flow.response.content else 0,
            "allowed": allowed,
        }
        self._log_file.write(json.dumps(entry) + "\n")

    def request(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host
        client_ip = flow.client_conn.peername[0] if flow.client_conn.peername else "unknown"

        if self._is_allowed(host):
            ctx.log.info(f"ALLOW {client_ip} -> {host}{flow.request.path}")
        else:
            ctx.log.warn(f"DENY {client_ip} -> {host}{flow.request.path}")
            flow.response = http.Response.make(
                403,
                json.dumps({"error": "domain_blocked", "domain": host}),
                {"Content-Type": "application/json"},
            )
            self._write_access_log(flow, allowed=False)

    def response(self, flow: http.HTTPFlow) -> None:
        self._write_access_log(flow, allowed=True)


addons = [DomainFilter()]
