"""RelayManager: file-based CRUD for relay assets plus vault-backed tokens (issue #2103).

Storage: data/relays/{slug}.json (RelayRecord; only vault secret NAMES). Token
values live in the credential vault under ``relay-{relay_id}-user-token`` /
``-app-token`` / ``-oauth-client-secret`` and are read in-process only, via
``get_token()``. One relay per Backend (the storage shape allows N later).
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from .credential_vault import SecretsVault
from .models.relay import RelayRecord
from .models.secret_record import SecretRecord, SecretType
from .relay.adapter import RelayAdapter, RelayError
from .relay.slack_adapter import SlackAdapter
from .relay.slack_scopes import required_scopes
from .relay.validation import RELAY_SECRET_PREFIX
from .secrets_keyring import get_secret_value
from .slug_utils import slugify

logger = logging.getLogger(__name__)

USER_TOKEN_PREFIX = "xoxp-"
APP_TOKEN_PREFIX = "xapp-"

SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"
# OAuth requests exactly the scopes of the Slack app manifest (relay/slack_scopes.py).
SLACK_USER_SCOPES = tuple(required_scopes())
OAUTH_FLOW_TYPE = "relay_oauth"
_OAUTH_TTL_SECONDS = 600

OAUTH_HTTPS_LIMITATION = (
    "Slack requires an https redirect URL for non-localhost hosts. OAuth works only with a "
    "localhost callback or an https-fronted Backend; otherwise paste the tokens instead."
)


class RelayConflictError(Exception):
    """A relay already exists (one per Backend)."""


class RelayNotFoundError(KeyError):
    pass


class RelayTokenError(ValueError):
    """Credential rejected (missing secret, prefix, in use, or provider verification).

    The message names secrets by NAME only and never contains a value.
    """


AdapterFactory = Callable[[Callable[[], Any]], RelayAdapter]


def _secret_name(relay_id: str, kind: str) -> str:
    return f"{RELAY_SECRET_PREFIX}{relay_id}-{kind}"


class RelayManager:
    def __init__(
        self,
        data_dir: Path,
        vault: SecretsVault,
        adapter_factory: AdapterFactory | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._dir = data_dir / "relays"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._vault = vault
        self._http_transport = http_transport
        self._adapter_factory: AdapterFactory = adapter_factory or (
            lambda token_provider: SlackAdapter(token_provider, transport=self._http_transport)
        )
        self.relays: dict[str, RelayRecord] = {}
        # state -> {relay_id, redirect_uri, created_at}; in-memory like OAuthFlowManager._pending
        self._oauth_pending: dict[str, dict] = {}
        self._oauth_results: dict[str, dict] = {}

    # --------------------------------------------------------------- storage

    async def load(self) -> None:
        self.relays.clear()
        for path in sorted(self._dir.glob("*.json")):
            try:
                record = RelayRecord.from_dict(json.loads(path.read_text()))
                self.relays[record.relay_id] = record
            except Exception:
                logger.exception("Failed to load relay file %s", path.name)

    def public_dict(self, record: RelayRecord) -> dict[str, Any]:
        """API shape: referenced secret NAMES plus presence booleans that reflect whether the
        referenced vault secret currently exists (deleted/renamed secret => has_* False)."""
        data = record.to_public_dict()
        for field, kind in (("user_token_secret", "user"), ("app_token_secret", "app")):
            name = getattr(record, field)
            data[field] = name
            data[f"{kind}_secret_missing"] = bool(name) and not self._vault.secret_exists(name)
            data[f"has_{kind}_token"] = self.has_token(record.relay_id, kind)
        return data

    def _path(self, record: RelayRecord) -> Path:
        return self._dir / f"{slugify(record.name) or record.relay_id}.json"

    def _save(self, record: RelayRecord) -> None:
        record.updated_at = datetime.now(UTC)
        self._path(record).write_text(json.dumps(record.to_dict(), indent=2))

    # ------------------------------------------------------------------ CRUD

    async def list_relays(self) -> list[RelayRecord]:
        return list(self.relays.values())

    async def get_relay(self, relay_id: str) -> RelayRecord | None:
        return self.relays.get(relay_id)

    def _require(self, relay_id: str) -> RelayRecord:
        record = self.relays.get(relay_id)
        if record is None:
            raise RelayNotFoundError(relay_id)
        return record

    async def create_relay(
        self,
        name: str,
        adapter_type: str = "slack",
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
    ) -> RelayRecord:
        if not name or not name.strip():
            raise ValueError("Relay name cannot be empty")
        if adapter_type != "slack":
            raise ValueError("Unsupported adapter_type; only 'slack' is available")
        if self.relays:
            raise RelayConflictError("A relay already exists; only one relay per Backend is supported")
        record = RelayRecord(
            relay_id=str(uuid.uuid4()),
            name=name.strip(),
            adapter_type=adapter_type,
            oauth_client_id=(oauth_client_id or None),
        )
        if oauth_client_secret:
            record.oauth_client_secret_secret = await self._store_secret(
                record.relay_id, "oauth-client-secret", oauth_client_secret
            )
        self._save(record)
        self.relays[record.relay_id] = record
        return record

    async def update_relay(
        self,
        relay_id: str,
        name: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
    ) -> RelayRecord:
        record = self._require(relay_id)
        old_path = self._path(record)
        if name is not None:
            if not name.strip():
                raise ValueError("Relay name cannot be empty")
            record.name = name.strip()
        if oauth_client_id is not None:
            record.oauth_client_id = oauth_client_id or None
        if oauth_client_secret:
            record.oauth_client_secret_secret = await self._store_secret(
                relay_id, "oauth-client-secret", oauth_client_secret
            )
        new_path = self._path(record)
        if old_path != new_path:
            old_path.unlink(missing_ok=True)
        self._save(record)
        return record

    async def delete_relay(self, relay_id: str) -> bool:
        record = self.relays.get(relay_id)
        if record is None:
            return False
        # Only secrets this relay created itself (OAuth flow, client secret) are deleted; secrets the
        # owner created and merely referenced stay in the vault.
        owned_prefix = f"{RELAY_SECRET_PREFIX}{relay_id}-"
        for name in (record.user_token_secret, record.app_token_secret, record.oauth_client_secret_secret):
            if name and name.startswith(owned_prefix):
                await self._vault.delete_secret(name)
        self._path(record).unlink(missing_ok=True)
        del self.relays[relay_id]
        return True

    # --------------------------------------------------------------- secrets

    async def _store_secret(self, relay_id: str, kind: str, value: str) -> str:
        """Create-or-replace a vault secret; returns its NAME."""
        name = _secret_name(relay_id, kind)
        now = datetime.now(UTC)
        record = SecretRecord(
            name=name, type=SecretType.GENERIC, target_hosts=[], created_at=now, updated_at=now
        )
        if await self._vault.get_secret(name) is None:
            await self._vault.create_secret(record, value)
        else:
            await self._vault.update_secret(name, record, value)
        return name

    def get_token(self, relay_id: str, kind: str = "user") -> str:
        """In-process token read (the only reader). kind: 'user' | 'app'. Raises RelayError."""
        record = self.relays.get(relay_id)
        secret = None
        if record is not None:
            secret = record.user_token_secret if kind == "user" else record.app_token_secret
        value = get_secret_value(secret) if secret else None
        if not value:
            raise RelayError("not_authed", "token")
        return value

    def has_token(self, relay_id: str, kind: str = "user") -> bool:
        record = self.relays.get(relay_id)
        name = record and (record.user_token_secret if kind == "user" else record.app_token_secret)
        return bool(name) and self._vault.secret_exists(name)

    def adapter_for(self, relay_id: str) -> RelayAdapter:
        """Adapter that fetches the stored user token on every call."""
        self._require(relay_id)

        async def _token() -> str:
            # keyring access can block (D-Bus / encrypted file): keep it off the event loop
            return await asyncio.to_thread(self.get_token, relay_id, "user")

        return self._adapter_factory(_token)

    async def set_secret_refs(
        self,
        relay_id: str,
        user_token_secret: str | None = None,
        app_token_secret: str | None = None,
    ) -> RelayRecord:
        """Reference existing vault secrets as the relay's credentials (either alone).

        Each secret must exist and its value (read in-process, never returned) must carry the
        expected prefix. A secret that is also assigned to sessions is allowed (the API reports
        where it is shared so the UI can warn). The user token is verified with ``auth.test`` (records owner/workspace).
        Only the secret NAMES are stored; a failure leaves the record untouched.
        """
        record = self._require(relay_id)
        if user_token_secret is None and app_token_secret is None:
            raise ValueError("Provide user_token_secret and/or app_token_secret")
        values: dict[str, str] = {}
        for label, name, prefix in (
            ("user_token_secret", user_token_secret, USER_TOKEN_PREFIX),
            ("app_token_secret", app_token_secret, APP_TOKEN_PREFIX),
        ):
            if name is None:
                continue
            name = name.strip()
            if not name or not self._vault.secret_exists(name):
                raise RelayTokenError(f"{label}: secret '{name}' was not found in the vault")
            value = await asyncio.to_thread(get_secret_value, name)
            if not value:
                raise RelayTokenError(f"{label}: secret '{name}' has no value")
            if not value.startswith(prefix):
                raise RelayTokenError(f"{label}: the value of secret '{name}' must start with '{prefix}'")
            values[label] = value

        info = await self._verify_user_token(values["user_token_secret"]) if "user_token_secret" in values else None
        if info is not None:
            record.owner_user_id = info.user_id or None
            record.workspace_id = info.team_id or None
            record.workspace_name = info.team
            record.user_token_secret = user_token_secret.strip()
        if "app_token_secret" in values:
            record.app_token_secret = app_token_secret.strip()
        self._save(record)
        return record

    async def _verify_user_token(self, user_token: str):
        async def _candidate() -> str:
            return user_token

        try:
            return await self._adapter_factory(_candidate).auth_test()
        except RelayError as e:
            raise RelayTokenError(f"Slack rejected the user token ({e.code})") from None

    async def test_relay(self, relay_id: str) -> dict[str, Any]:
        """auth.test with the stored user token."""
        record = self._require(relay_id)
        try:
            info = await self.adapter_for(relay_id).auth_test()
        except RelayError as e:
            return {"ok": False, "error": e.code}
        record.owner_user_id = info.user_id or record.owner_user_id
        record.workspace_id = info.team_id or record.workspace_id
        record.workspace_name = info.team or record.workspace_name
        self._save(record)
        return {
            "ok": True,
            "workspace_id": info.team_id,
            "workspace_name": info.team,
            "user_id": info.user_id,
            "user": info.user,
        }

    # ----------------------------------------------------------------- OAuth

    def _sweep_oauth(self) -> None:
        while len(self._oauth_results) > 20:  # results are only polled briefly after a flow
            del self._oauth_results[next(iter(self._oauth_results))]
        cutoff = time.time() - _OAUTH_TTL_SECONDS
        for state in [s for s, p in self._oauth_pending.items() if p["created_at"] < cutoff]:
            del self._oauth_pending[state]

    async def oauth_initiate(self, relay_id: str, redirect_uri: str) -> dict[str, str]:
        """Build the Slack user-token authorize URL (no PKCE, ``user_scope`` not ``scope``)."""
        record = self._require(relay_id)
        if not record.oauth_client_id or not record.oauth_client_secret_secret:
            raise ValueError("oauth_client_id and oauth_client_secret must be set on the relay first")
        self._sweep_oauth()
        state = secrets.token_urlsafe(32)
        self._oauth_pending[state] = {
            "relay_id": relay_id, "redirect_uri": redirect_uri, "created_at": time.time(),
        }
        params = {
            "client_id": record.oauth_client_id,
            "user_scope": ",".join(SLACK_USER_SCOPES),
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return {"auth_url": f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}", "state": state}

    def is_relay_oauth_state(self, state: str) -> bool:
        return state in self._oauth_pending

    def oauth_status(self, state: str) -> dict[str, Any]:
        if state in self._oauth_results:
            return self._oauth_results[state]
        if state in self._oauth_pending:
            return {"status": "pending"}
        return {"status": "unknown"}

    async def oauth_complete(self, state: str, code: str) -> RelayRecord:
        """Exchange the code via oauth.v2.access and store ``authed_user.access_token``."""
        pending = self._oauth_pending.pop(state, None)
        if pending is None:
            raise ValueError("No pending relay OAuth flow for this state")
        try:
            record = self._require(pending["relay_id"])
            client_secret = await asyncio.to_thread(
                get_secret_value, record.oauth_client_secret_secret or ""
            )
            if not client_secret or not record.oauth_client_id:
                raise ValueError("Relay OAuth client credentials are missing")
            try:
                async with httpx.AsyncClient(transport=self._http_transport, timeout=30.0) as http:
                    resp = await http.post(
                        SLACK_OAUTH_ACCESS_URL,
                        data={
                            "client_id": record.oauth_client_id,
                            "client_secret": client_secret,
                            "code": code,
                            "redirect_uri": pending["redirect_uri"],
                        },
                    )
                data = resp.json()
            except (httpx.HTTPError, ValueError):
                raise ValueError("OAuth token exchange failed (network or invalid response)") from None
            if not data.get("ok"):
                raise ValueError(f"OAuth token exchange failed: {data.get('error', 'unknown_error')}")
            token = (data.get("authed_user") or {}).get("access_token")
            if not token or not token.startswith(USER_TOKEN_PREFIX):
                raise ValueError("Slack did not return a user token (check the user_scope configuration)")
            info = await self._verify_user_token(token)
            # The token becomes a normal (relay-prefixed) vault secret, referenced by name.
            record.user_token_secret = await self._store_secret(record.relay_id, "user-token", token)
            record.owner_user_id = info.user_id or None
            record.workspace_id = info.team_id or None
            record.workspace_name = info.team
            self._save(record)
        except Exception as e:
            self._oauth_results[state] = {"status": "error", "error": str(e)}
            raise
        self._oauth_results[state] = {"status": "complete", "relay_id": record.relay_id}
        return record

    def oauth_denied(self, state: str, error: str) -> None:
        # Only a state we issued is recorded: callbacks carry attacker-controllable values.
        if self._oauth_pending.pop(state, None) is not None:
            self._oauth_results[state] = {"status": "error", "error": error}
