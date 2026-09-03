"""ApiKeyHandler — configurable injection location (header or query_param) (issue #1134)."""

from __future__ import annotations

from typing import Any

from ._scrub import scrub_everywhere
from .base import SecretTypeHandler


class ApiKeyHandler(SecretTypeHandler):
    """Inject based on injection.location:
      - "header": replace placeholder in request headers; format as
        "{prefix} {value}" (or just "{value}" when prefix is empty).
      - "query_param": replace placeholder in query string param named param_name.
    Scrub raw value from response everywhere.
    """

    def inject(self, flow: Any, record: dict, placeholder: str) -> bool:
        value = record.get("value", "")
        if not value:
            return False

        injection = record.get("injection") or {}
        location = injection.get("location", "header")

        if location == "query_param":
            return self._inject_query(flow, record, placeholder, value, injection)
        return self._inject_header(flow, record, placeholder, value, injection)

    def _inject_header(
        self, flow: Any, record: dict, placeholder: str, value: str, injection: dict
    ) -> bool:
        prefix = injection.get("prefix", "Bearer")
        formatted = f"{prefix} {value}" if prefix else value
        modified = False
        for name, hdr_val in list(flow.request.headers.items()):
            if placeholder in hdr_val:
                flow.request.headers[name] = hdr_val.replace(placeholder, formatted)
                modified = True
        return modified

    def _inject_query(
        self, flow: Any, record: dict, placeholder: str, value: str, injection: dict
    ) -> bool:
        param_name = injection.get("param_name")
        if not param_name:
            return False
        modified = False
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
        return modified

    def scrub(self, flow: Any, record: dict, placeholder: str) -> tuple[bool, str | None]:
        return scrub_everywhere(flow, record, placeholder), None
