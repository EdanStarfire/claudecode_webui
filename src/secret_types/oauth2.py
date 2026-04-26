"""OAuth2Handler — bearer injection, proactive refresh, response capture (issue #1134)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ._scrub import capture_from_response, scrub_everywhere
from .base import SecretTypeHandler


class OAuth2Handler(SecretTypeHandler):
    """Handle oauth2 access-token records.

    inject:  Authorization: Bearer <access_token>
    scrub:   replace raw access_token with placeholder everywhere in response;
             if response URL matches scrub.url_path and a matcher fires,
             capture the new value for keyring write-back.
    should_refresh: True when expires_at < now + buffer_seconds.
    refresh: POST grant_type=refresh_token to token_url; return updated fields.
    """

    def inject(self, flow: Any, record: dict, placeholder: str) -> bool:
        value = record.get("value", "")
        if not value:
            return False

        modified = False
        for name, hdr_val in list(flow.request.headers.items()):
            if placeholder in hdr_val:
                # Replace entire header value so the agent does not need to know
                # the "Bearer " prefix — it just passes the placeholder.
                flow.request.headers[name] = f"Bearer {value}"
                modified = True
        return modified

    def scrub(self, flow: Any, record: dict, placeholder: str) -> tuple[bool, str | None]:
        modified = scrub_everywhere(flow, record, placeholder)
        captured = capture_from_response(flow, record)
        return modified or (captured is not None), captured

    def should_refresh(self, record: dict) -> bool:
        refresh = record.get("refresh")
        if not refresh:
            return False
        expires_at_str = refresh.get("expires_at")
        if not expires_at_str:
            return False
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            buffer = int(refresh.get("buffer_seconds", 60))
            now = datetime.now(tz=UTC)
            return now >= expires_at - timedelta(seconds=buffer)
        except Exception:
            return False

    async def refresh(self, record: dict, get_secret_value: Any) -> dict:
        """POST grant_type=refresh_token to token_url.

        get_secret_value(name) -> str  fetches sibling secret values.

        Returns a dict of updated fields: at minimum {"value": new_access_token,
        "refresh": {..."expires_at": ..., "refresh_token_secret_name": ...}}.
        The caller is responsible for persisting these to the keyring.
        """
        import httpx

        r = record.get("refresh", {})
        token_url = r.get("token_url", "")
        client_id = r.get("client_id", "")
        refresh_token_name = r.get("refresh_token_secret_name", "")
        client_secret_name = r.get("client_secret_secret_name")

        if not token_url or not refresh_token_name:
            raise ValueError("oauth2 refresh requires token_url and refresh_token_secret_name")

        refresh_token = await get_secret_value(refresh_token_name)
        if not refresh_token:
            raise ValueError(f"refresh token secret '{refresh_token_name}' is empty or missing")

        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if client_secret_name:
            client_secret = await get_secret_value(client_secret_name)
            if client_secret:
                data["client_secret"] = client_secret

        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data=data, timeout=30)
            resp.raise_for_status()
            body = resp.json()

        new_access_token = body.get("access_token")
        if not new_access_token:
            raise ValueError(f"token_url response missing access_token: {list(body.keys())}")

        updated_refresh = dict(r)
        new_refresh_token = body.get("refresh_token")
        if new_refresh_token:
            # Caller must update the sibling record's value in keyring separately.
            updated_refresh["_new_refresh_token"] = new_refresh_token

        expires_in = body.get("expires_in")
        if expires_in:
            new_expires = datetime.now(tz=UTC) + timedelta(seconds=int(expires_in))
            updated_refresh["expires_at"] = new_expires.isoformat()

        return {"value": new_access_token, "refresh": updated_refresh}
