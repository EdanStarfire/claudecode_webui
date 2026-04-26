"""Shared scrub helpers for secret type handlers (issue #1134)."""

from __future__ import annotations

import re
from typing import Any


def _decode_body(content: bytes | None) -> str | None:
    """Decode response body bytes to str; return None on failure or if empty."""
    if not content:
        return None
    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return None


def scrub_everywhere(flow: Any, record: dict, placeholder: str) -> bool:
    """Replace every occurrence of the raw secret value with `placeholder`
    in the response headers and body.

    Returns True if any substitution was made.
    Fast-path: skip body scan when content-type is binary.
    """
    real_value = record.get("value", "")
    if not real_value or flow.response is None:
        return False

    # Skip binary content types to avoid corrupting non-text responses.
    ct = (flow.response.headers.get("content-type", "") if hasattr(flow.response.headers, "get")
          else "")
    if any(ct.startswith(b) for b in ("image/", "audio/", "video/", "application/octet-stream")):
        return False

    modified = False

    # Response headers
    for name, val in list(flow.response.headers.items()):
        if real_value in val:
            flow.response.headers[name] = val.replace(real_value, placeholder)
            modified = True

    # Response body
    body = _decode_body(flow.response.content)
    if body and real_value in body:
        flow.response.content = body.replace(real_value, placeholder).encode("utf-8")
        modified = True

    return modified


def capture_from_response(flow: Any, record: dict) -> str | None:
    """Apply scrub.matcher_jsonpath or scrub.matcher_regex to the response body
    to capture a new token value.

    Returns the captured string if matched, else None.
    Only checks when flow.request.path contains scrub.url_path.
    """
    scrub = record.get("scrub")
    if not scrub:
        return None
    url_path = scrub.get("url_path")
    if url_path and url_path not in (getattr(flow.request, "path", "") or ""):
        return None

    if not scrub.get("update_on_change"):
        return None

    body = _decode_body(getattr(flow.response, "content", None))
    if not body:
        return None

    matcher_jsonpath = scrub.get("matcher_jsonpath")
    matcher_regex = scrub.get("matcher_regex")

    if matcher_jsonpath:
        try:
            import json

            import jsonpath_ng  # type: ignore[import-untyped]
            expr = jsonpath_ng.parse(matcher_jsonpath)
            data = json.loads(body)
            matches = [m.value for m in expr.find(data)]
            if matches and isinstance(matches[0], str):
                return matches[0]
        except Exception:
            return None

    if matcher_regex:
        m = re.search(matcher_regex, body)
        if m:
            return m.group(1) if m.lastindex else m.group(0)

    return None
