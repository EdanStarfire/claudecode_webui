"""GenericHandler — placeholder-anywhere injection and response scrubbing (issue #1134)."""

from __future__ import annotations

from typing import Any

from ._scrub import scrub_everywhere
from .base import SecretTypeHandler


class GenericHandler(SecretTypeHandler):
    """Inject by substituting placeholder with raw value anywhere it appears
    in request headers, query params, or body.  Scrub raw value from response.
    """

    def inject(self, flow: Any, record: dict, placeholder: str) -> bool:
        value = record.get("value", "")
        if not value:
            return False

        modified = False

        # Headers
        for name, hdr_val in list(flow.request.headers.items()):
            if placeholder in hdr_val:
                flow.request.headers[name] = hdr_val.replace(placeholder, value)
                modified = True

        # Query params
        try:
            q_pairs = list(flow.request.query.items())
            new_pairs = []
            q_changed = False
            for k, v in q_pairs:
                if placeholder in v:
                    new_pairs.append((k, v.replace(placeholder, value)))
                    q_changed = True
                else:
                    new_pairs.append((k, v))
            if q_changed:
                flow.request.query = new_pairs
                modified = True
        except Exception:
            pass

        # Request body
        try:
            content = flow.request.content
            if content:
                body = content.decode("utf-8", errors="replace")
                if placeholder in body:
                    flow.request.content = body.replace(placeholder, value).encode("utf-8")
                    modified = True
        except Exception:
            pass

        return modified

    def scrub(self, flow: Any, record: dict, placeholder: str) -> tuple[bool, str | None]:
        return scrub_everywhere(flow, record, placeholder), None
