import json
import logging
from pathlib import Path
from mitmproxy import http, ctx

ALLOWLIST_PATH = "/etc/proxy/allowlist.json"


class DomainFilter:
    def __init__(self):
        self.allowed_domains: set[str] = set()
        self.logger = logging.getLogger("proxy.filter")

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


addons = [DomainFilter()]
