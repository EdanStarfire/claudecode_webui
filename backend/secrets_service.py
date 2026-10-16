"""SecretsService: Secrets Vault CRUD + session-scoped secret resolution.

Extracted from ApplicationService (issue #2189 AC5, router-boundary PR B).
Houses everything behind the secrets-vault surface (issue #827 — replaces
issue #1053 CredentialVault) that previously lived directly on
ApplicationService: CRUD, OAuth2 refresh, and the proxy sidecar's
session-scoped resolve/update-back operations.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from .secret_usage import compute_secret_usage, empty_usage
from .slug_utils import slugify_secret


def _compute_secret_health(refresh: dict | None) -> str:
    """Issue #1387: Derive token health state from refresh metadata.

    Returns one of: "valid", "expiring_soon", "expired", "refresh_failed".
    """
    if not refresh:
        return "valid"

    if refresh.get("last_refresh_error"):
        return "refresh_failed"

    expires_at_str = refresh.get("expires_at")
    if not expires_at_str:
        return "valid"

    try:
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        buffer = int(refresh.get("buffer_seconds", 300))
        now = datetime.now(tz=UTC)
        if now >= expires_at:
            return "expired"
        if now >= expires_at - timedelta(seconds=buffer):
            return "expiring_soon"
        return "valid"
    except Exception:
        return "valid"


class SecretsService:
    """Secrets Vault CRUD (issue #827 — replaces issue #1053 CredentialVault)."""

    def __init__(
        self,
        credential_vault,
        vault_refresh_manager,
        session_manager,
        template_manager,
        profile_manager,
        mcp_config_manager,
    ) -> None:
        self.credential_vault = credential_vault
        self.vault_refresh_manager = vault_refresh_manager
        self.session_manager = session_manager
        self.template_manager = template_manager
        self.profile_manager = profile_manager
        self.mcp_config_manager = mcp_config_manager

    async def list_secrets(self) -> dict:
        """List all secret metadata. Never includes secret values."""
        secrets = await self.credential_vault.list_secrets()
        # Issue #1387: enrich OAUTH2 secrets with a computed health field
        for s in secrets:
            if s.get("type") == "oauth2":
                s["health"] = _compute_secret_health(s.get("refresh"))
        # Issue #1772: enrich each secret with a usage breakdown
        sessions, templates, profiles, mcp_configs = await asyncio.gather(
            self.session_manager.list_sessions(),
            self.template_manager.list_templates(),
            self.profile_manager.list_profiles(area="isolation"),
            self.mcp_config_manager.list_configs(),
        )
        usage_map = compute_secret_usage(secrets, sessions, templates, profiles, mcp_configs)
        for s in secrets:
            s["usage"] = usage_map.get(slugify_secret(s["name"]), empty_usage())
        return {"secrets": secrets}

    async def create_secret(
        self,
        name: str,
        secret_type: str,
        target_hosts: list[str],
        value: str,
        inject_env: str | None = None,
        inject_file: dict | None = None,
        scrub: dict | None = None,
        username: str | None = None,
        injection: dict | None = None,
        refresh: dict | None = None,
    ) -> dict:
        """Create a new secret. Returns metadata only (value not in response)."""
        from datetime import UTC, datetime

        from .models.secret_record import (
            InjectFileSpec,
            InjectionSpec,
            RefreshSpec,
            ScrubSpec,
            SecretRecord,
            SecretType,
        )

        now = datetime.now(UTC)
        inj_file = InjectFileSpec.from_dict(inject_file) if inject_file else None
        scrub_spec = ScrubSpec.from_dict(scrub) if scrub else None
        injection_spec = InjectionSpec.from_dict(injection) if injection else None
        refresh_spec = RefreshSpec.from_dict(refresh) if refresh else None
        record = SecretRecord(
            name=name,
            type=SecretType(secret_type),
            target_hosts=target_hosts,
            inject_env=inject_env,
            inject_file=inj_file,
            scrub=scrub_spec,
            username=username,
            injection=injection_spec,
            refresh=refresh_spec,
            created_at=now,
            updated_at=now,
        )
        return await self.credential_vault.create_secret(record, value)

    async def update_secret(
        self,
        name: str,
        secret_type: str | None = None,
        target_hosts: list[str] | None = None,
        value: str | None = None,
        inject_env: str | None = None,
        inject_file: dict | None = None,
        scrub: dict | None = None,
        username: str | None = None,
        injection: dict | None = None,
        refresh: dict | None = None,
    ) -> dict | None:
        """Update secret metadata and/or value. Returns updated metadata or None if not found."""
        from datetime import UTC, datetime

        from .models.secret_record import (
            InjectFileSpec,
            InjectionSpec,
            RefreshSpec,
            ScrubSpec,
            SecretRecord,
            SecretType,
        )

        existing = await self.credential_vault.get_secret(name)
        if existing is None:
            return None

        now = datetime.now(UTC)
        inj_file = None
        if inject_file is not None:
            inj_file = InjectFileSpec.from_dict(inject_file)
        elif existing.get("inject_file"):
            inj_file = InjectFileSpec.from_dict(existing["inject_file"])

        scrub_spec = None
        if scrub is not None:
            scrub_spec = ScrubSpec.from_dict(scrub)
        elif existing.get("scrub"):
            scrub_spec = ScrubSpec.from_dict(existing["scrub"])

        injection_spec = None
        if injection is not None:
            injection_spec = InjectionSpec.from_dict(injection)
        elif existing.get("injection"):
            injection_spec = InjectionSpec.from_dict(existing["injection"])

        refresh_spec = None
        if refresh is not None:
            refresh_spec = RefreshSpec.from_dict(refresh)
        elif existing.get("refresh"):
            refresh_spec = RefreshSpec.from_dict(existing["refresh"])

        record = SecretRecord(
            name=existing.get("name", name),
            type=SecretType(secret_type or existing.get("type", "generic")),
            target_hosts=target_hosts if target_hosts is not None else existing.get("target_hosts", []),
            inject_env=inject_env if inject_env is not None else existing.get("inject_env"),
            inject_file=inj_file,
            scrub=scrub_spec,
            username=username if username is not None else existing.get("username"),
            injection=injection_spec,
            refresh=refresh_spec,
            created_at=datetime.fromisoformat(existing["created_at"]),
            updated_at=now,
        )
        return await self.credential_vault.update_secret(name, record, value)

    async def delete_secret(self, name: str) -> bool:
        """Delete a secret by name."""
        return await self.credential_vault.delete_secret(name)

    async def refresh_secret(self, name: str) -> dict | None:
        """Manually trigger an OAuth2 token refresh for an oauth2-typed secret.

        Fetches the current refresh_token and client_secret sibling values from the
        keyring, POSTs to token_url, writes the new access_token (and rotated
        refresh_token if present) back to the keyring, and updates expires_at.
        Returns updated metadata, or None if the secret does not exist / is not oauth2.
        Raises RuntimeError on refresh failure.

        Issue #1387: Acquires the VaultRefreshManager per-secret lock to prevent
        concurrent refresh races with the background task.
        """
        # Issue #1387: acquire per-secret lock to deduplicate concurrent refreshes
        mgr = self.vault_refresh_manager
        if mgr:
            lock = mgr.get_lock(name)
            async with lock:
                return await self._refresh_secret_impl(name)
        return await self._refresh_secret_impl(name)

    async def _refresh_secret_impl(self, name: str) -> dict | None:
        """Core refresh logic — caller must hold per-secret lock if dedup is needed."""
        from .models.secret_record import SecretType
        from .secret_types.oauth2 import OAuth2Handler
        from .secrets_keyring import get_secret_value, set_secret_value

        meta = await self.credential_vault.get_secret(name)
        if meta is None or meta.get("type") != SecretType.OAUTH2.value:
            return None

        record = dict(meta)
        record["value"] = get_secret_value(name) or ""

        async def _get_sibling(sibling_name: str) -> str:
            return get_secret_value(sibling_name) or ""

        handler = OAuth2Handler()
        updates = await handler.refresh(record, _get_sibling)

        new_value = updates.get("value")
        if new_value:
            set_secret_value(name, new_value)

        new_refresh_token = updates.get("refresh", {}).get("_new_refresh_token")
        new_refresh_name = (record.get("refresh") or {}).get("refresh_token_secret_name")
        if new_refresh_token and new_refresh_name:
            set_secret_value(new_refresh_name, new_refresh_token)

        refresh_updates = updates.get("refresh") or {}
        refresh_updates.pop("_new_refresh_token", None)

        return await self.update_secret(
            name=name,
            refresh=refresh_updates or None,
            value=None,
        )

    def _resolve_writeable_secret_names(self, session) -> list[str]:
        """Names the proxy can both resolve and write back for this session.

        Source order (deduped, preserves first occurrence):
          1. session.config["assigned_secrets"] — direct session-level overrides
          2. session.secret_placeholders.values() — template/profile-derived names captured at start_session()
        """
        names: list[str] = list(session.config.get("assigned_secrets") or [])
        for name in (session.secret_placeholders or {}).values():
            if name not in names:
                names.append(name)
        return names

    async def resolve_secrets_for_session(self, session_id: str) -> dict:
        """Return resolved secrets (including values) for a session's assigned_secrets list.

        Issue #1134: attaches placeholder from session.secret_placeholders; also
        walks transitive references (refresh_token_secret_name, client_secret_secret_name)
        so the proxy has everything it needs for OAuth2 refresh without extra requests.
        """
        session = await self.session_manager.get_session_info(session_id)
        if session is None:
            return {"secrets": []}

        assigned = self._resolve_writeable_secret_names(session)

        # Collect transitive names (sibling refresh-token and client-secret records).
        all_names: list[str] = list(assigned)
        for secret in await self.credential_vault.list_secrets():
            if secret.get("name") in assigned:
                refresh = secret.get("refresh") or {}
                for key in ("refresh_token_secret_name", "client_secret_secret_name"):
                    sibling = refresh.get(key)
                    if sibling and sibling not in all_names:
                        all_names.append(sibling)

        secrets = await self.credential_vault.resolve_secrets_for_assignment(all_names)

        # Attach placeholder from session map (may be empty for sessions started before #1134).
        placeholder_map = session.secret_placeholders or {}
        reverse_map = {v: k for k, v in placeholder_map.items()}
        for s in secrets:
            name = s.get("name", "")
            s["placeholder"] = reverse_map.get(name)

        return {"secrets": secrets}

    async def update_secret_for_session(
        self, session_id: str, secret_name: str, **kwargs
    ) -> dict | None:
        """Update a secret, scoped to names in session.assigned_secrets (+ transitive siblings).

        Used by the proxy sidecar to write back refreshed token values.
        Returns None if session not found; raises PermissionError if name is out-of-scope.
        """
        session = await self.session_manager.get_session_info(session_id)
        if session is None:
            return None
        assigned = self._resolve_writeable_secret_names(session)
        allowed: set[str] = set(assigned)
        # Also allow transitive sibling records referenced by assigned refresh specs.
        for secret in await self.credential_vault.list_secrets():
            if secret.get("name") in assigned:
                refresh = secret.get("refresh") or {}
                for key in ("refresh_token_secret_name", "client_secret_secret_name"):
                    sibling = refresh.get(key)
                    if sibling:
                        allowed.add(sibling)
        if secret_name not in allowed:
            raise PermissionError(
                f"Secret '{secret_name}' is not in session {session_id}'s assigned_secrets"
            )
        return await self.update_secret(name=secret_name, **kwargs)

    def schedule_secret_refresh(self, name: str) -> None:
        self.vault_refresh_manager.schedule_secret(name)

    def unschedule_secret_refresh(self, name: str) -> None:
        self.vault_refresh_manager.unschedule_secret(name)
