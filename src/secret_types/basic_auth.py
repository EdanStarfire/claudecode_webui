"""BasicAuthHandler — Authorization: Basic injection (issue #1134).

Username is plaintext metadata (record['username']); password is the keyring value.
"""

from __future__ import annotations

import base64
from typing import Any

from ._scrub import scrub_everywhere
from .base import SecretTypeHandler


class BasicAuthHandler(SecretTypeHandler):
    """Inject Authorization: Basic base64(username:password) when placeholder found
    in any request header.  Scrub raw password value from response.
    """

    def inject(self, flow: Any, record: dict, placeholder: str) -> bool:
        password = record.get("value", "")
        username = record.get("username") or ""
        if not password:
            return False

        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        formatted = f"Basic {credentials}"

        modified = False
        for name, hdr_val in list(flow.request.headers.items()):
            if placeholder in hdr_val:
                flow.request.headers[name] = hdr_val.replace(placeholder, formatted)
                modified = True
        return modified

    def scrub(self, flow: Any, record: dict, placeholder: str) -> tuple[bool, str | None]:
        # Scrub the raw password (the keyring value) — not the encoded Basic string.
        return scrub_everywhere(flow, record, placeholder), None
