"""BearerHandler — Authorization: Bearer injection (issue #1134)."""

from __future__ import annotations

from typing import Any

from ._scrub import scrub_everywhere
from .base import SecretTypeHandler


class BearerHandler(SecretTypeHandler):
    """Inject by scanning request headers for placeholder and replacing with
    the real bearer token.  The formatted value is `Bearer <token>`.
    Scrub everywhere on response.
    """

    def inject(self, flow: Any, record: dict, placeholder: str) -> bool:
        value = record.get("value", "")
        if not value:
            return False

        modified = False
        for name, hdr_val in list(flow.request.headers.items()):
            if placeholder in hdr_val:
                # Replace the entire header value so the agent does not need to
                # know the "Bearer " prefix — it just passes the placeholder.
                flow.request.headers[name] = f"Bearer {value}"
                modified = True
        return modified

    def scrub(self, flow: Any, record: dict, placeholder: str) -> tuple[bool, str | None]:
        return scrub_everywhere(flow, record, placeholder), None
