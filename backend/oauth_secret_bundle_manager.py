"""OAuthSecretBundleManager: OAuth 2.1 token/secret-bundle management.

Extracted from ApplicationService (issue #2189 AC5, router-boundary PR B).
Houses MCP-config-backed OAuth flows (complete_flow/initiate_flow/disconnect/
get_status), import-as-secret bundling, and the standalone (no-MCP-config)
guided-authorization flow — all of which share the same vault secret-bundle
creation/replacement helpers.

Method names are kept verbatim from their pre-extraction ApplicationService
names (including the now-redundant ``oauth_``/``standalone_oauth_`` prefixes)
rather than renamed for elegance — this is a pure logic move, and minimizing
risk on security-sensitive surface area outweighs naming cleanliness here.
"""
from __future__ import annotations

import time

from .application_service import NoRefreshTokenError

__all__ = ["NoRefreshTokenError", "OAuthSecretBundleManager"]


class OAuthSecretBundleManager:
    """OAuth 2.1 flows + vault secret-bundle management (issue #813, #1387, #1871)."""

    def __init__(self, credential_vault, oauth_manager, mcp_config_manager, vault_refresh_manager) -> None:
        self.credential_vault = credential_vault
        self.oauth_manager = oauth_manager
        self.mcp_config_manager = mcp_config_manager
        self.vault_refresh_manager = vault_refresh_manager

    async def oauth_complete_flow(self, state: str, code: str) -> str:
        return await self.oauth_manager.complete_flow(state, code)

    async def oauth_initiate_flow(
        self,
        config_id: str,
        server_url: str,
        redirect_uri: str,
        client_name: str,
        client_metadata_url: str | None = None,
    ) -> str | None:
        from .mcp.secret_resolver import resolve_secret_refs_in_str

        config = await self.mcp_config_manager.get_config(config_id)
        if not config:
            return None
        client_secret = None
        if config.oauth_client_secret:
            client_secret = await resolve_secret_refs_in_str(
                config.oauth_client_secret, self.credential_vault
            )
        # Issue #2231: the hosted client-metadata document always advertises the
        # default /oauth/callback path — a config with its own custom callback
        # path/port would get a mismatched document, so CIMD is withheld entirely
        # and start_flow() falls back to DCR (or its distinct CIMD error).
        effective_client_metadata_url = client_metadata_url
        if config.oauth_custom_callback_path or config.oauth_custom_callback_port:
            effective_client_metadata_url = None
        return await self.oauth_manager.start_flow(
            server_id=config_id,
            server_url=server_url,
            redirect_uri=redirect_uri,
            client_name=client_name,
            pre_registered_client_id=config.oauth_client_id or None,
            client_secret=client_secret,
            client_metadata_url=effective_client_metadata_url,
        )

    async def oauth_disconnect(self, config_id: str) -> bool:
        config = await self.mcp_config_manager.get_config(config_id)
        if not config:
            return False
        await self.oauth_manager.disconnect(config_id)
        return True

    async def oauth_get_status(self, config_id: str) -> dict | None:
        config = await self.mcp_config_manager.get_config(config_id)
        if not config:
            return None
        token = await self.oauth_manager.get_stored_token(config_id)
        if token is None:
            return {"status": "unauthenticated", "expires_at": None, "has_refresh_token": False}

        store = self.oauth_manager.get_token_store(config_id)
        expiry = await store.get_token_expiry()
        now = time.time()
        # Issue #976: Distinguish expiring_soon (within 5 min) from fully authenticated
        if expiry is not None and expiry < now:
            status = "expired"
        elif expiry is not None and expiry < (now + 300):
            status = "expiring_soon"
        else:
            status = "authenticated"
        return {
            "status": status,
            "expires_at": expiry,
            "has_refresh_token": bool(token.refresh_token),
        }

    async def import_oauth_as_secret(
        self, config_id: str, base_name: str, replace: bool = False
    ) -> dict:
        """Import stored OAuth 2.1 tokens as proxy-injectable vault secrets.

        Creates up to 3 vault secrets (primary oauth2, refresh, client_secret) and
        updates the MCP config's Authorization header to ${secret:<base_name>}.

        When replace=True (Reconnect flow): updates existing records in-place after
        verifying sibling ownership. Skips the 409 collision pre-check.

        Raises:
            ValueError: 400 — invalid base_name, STDIO config, or missing token_url.
            LookupError: 404 — config not found or no stored tokens.
            KeyError: 409 — vault name collision (replace=False) or sibling ownership
                           guard failure (replace=True).
        """
        import re
        from datetime import UTC, datetime
        from urllib.parse import urlparse

        from .models.secret_record import RefreshSpec, ScrubSpec

        base_name_re = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
        if not base_name_re.match(base_name):
            raise ValueError(
                "base_name must match ^[a-z0-9][a-z0-9_-]{0,62}$"
            )

        config = await self.mcp_config_manager.get_config(config_id)
        if not config:
            raise LookupError("404: MCP configuration not found")

        if not config.url:
            raise ValueError("Cannot import OAuth tokens from STDIO MCP server")

        store = self.oauth_manager.get_token_store(config_id)
        tokens = await store.get_tokens()
        if tokens is None:
            raise LookupError("404: No OAuth tokens stored for this MCP server")

        expiry_ts = await store.get_token_expiry()
        client_info = await store.get_client_info()
        token_url = store.get_token_endpoint()
        if not token_url:
            raise ValueError("OAuth token endpoint not recorded; re-authenticate to refresh it")

        vault = self.credential_vault

        # Determine sibling secret names
        refresh_token_name = f"{base_name}_refresh" if tokens.refresh_token else None
        client_secret_value = client_info.client_secret if client_info else None
        client_secret_name = f"{base_name}_client_secret" if client_secret_value else None
        client_secret_reused = False
        if client_secret_value:
            from .mcp.secret_resolver import find_secret_ref_names

            ref_names = find_secret_ref_names(config.oauth_client_secret)
            if ref_names and len(ref_names) == 1 and (
                config.oauth_client_secret.strip() == f"${{secret:{ref_names[0]}}}"
            ):
                existing_secret = await vault.get_secret(ref_names[0])
                if existing_secret is None:
                    raise LookupError(
                        f"404: Referenced client secret '{ref_names[0]}' no longer exists in vault"
                    )
                client_secret_name = ref_names[0]
                client_secret_reused = True

        now = datetime.now(UTC)
        # Issue #1871: .hostname (not .netloc) — target_hosts validation rejects a
        # port component, so an MCP server URL with an explicit non-standard port
        # would otherwise fail SecretRecord.validate() below with a confusing error.
        host = urlparse(config.url).hostname or urlparse(config.url).netloc
        token_path = urlparse(token_url).path
        client_id = (client_info.client_id if client_info else None) or config.oauth_client_id or ""
        expires_at_dt = datetime.fromtimestamp(expiry_ts, tz=UTC) if expiry_ts else None

        scrub = ScrubSpec(
            url_path=token_path,
            matcher_jsonpath="$.access_token",
            update_on_change=True,
        )

        refresh_spec = None
        if tokens.refresh_token and refresh_token_name:
            refresh_spec = RefreshSpec(
                token_url=token_url,
                client_id=client_id,
                refresh_token_secret_name=refresh_token_name,
                client_secret_secret_name=client_secret_name,
                expires_at=expires_at_dt,
                buffer_seconds=300,
            )

        expires_at_iso = expires_at_dt.isoformat() if expires_at_dt else None

        if replace:
            return await self._replace_oauth_secret_bundle(
                vault=vault,
                base_name=base_name,
                refresh_token_name=refresh_token_name,
                client_secret_name=client_secret_name,
                tokens=tokens,
                client_secret_value=client_secret_value,
                refresh_spec=refresh_spec,
                scrub=scrub,
                host=host,
                now=now,
                expires_at_iso=expires_at_iso,
            )

        # Pre-check vault collisions (replace=False only). A reused reference's
        # existing presence in the vault is expected, not a collision.
        collision_candidates = [base_name, refresh_token_name]
        if not client_secret_reused:
            collision_candidates.append(client_secret_name)
        for name in filter(None, collision_candidates):
            existing = await vault.get_secret(name)
            if existing is not None:
                raise KeyError(f"409: Secret '{name}' already exists; choose a different base_name")

        created = await self._create_oauth_secret_bundle(
            vault=vault,
            base_name=base_name,
            refresh_token_name=refresh_token_name,
            client_secret_name=client_secret_name,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            client_secret_value=client_secret_value,
            refresh_spec=refresh_spec,
            scrub=scrub,
            host=host,
            now=now,
            client_secret_reused=client_secret_reused,
        )

        # Update MCP config headers — rollback all secrets if this fails
        try:
            new_headers = {**(config.headers or {}), "Authorization": f"${{secret:{base_name}}}"}
            await self.mcp_config_manager.update_config(config_id, headers=new_headers)
        except Exception:
            for n in created:
                await vault.delete_secret(n)
            raise

        # Issue #1387: schedule background refresh for the new oauth2 secret
        self.vault_refresh_manager.schedule_secret(base_name)

        return {
            "secrets_created": created,
            "header_injected": f"Authorization: ${{secret:{base_name}}}",
            "expires_at": expires_at_iso,
            "auto_refresh_enabled": refresh_spec is not None,
        }

    async def _create_oauth_secret_bundle(
        self,
        *,
        vault,
        base_name: str,
        refresh_token_name: str | None,
        client_secret_name: str | None,
        access_token: str,
        refresh_token: str | None,
        client_secret_value: str | None,
        refresh_spec,
        scrub,
        host: str,
        now,
        client_secret_reused: bool = False,
    ) -> list[str]:
        """Create up to 3 vault secrets (refresh-token sibling, client-secret sibling,
        primary oauth2 record) with rollback-on-failure. Returns the list of created
        secret names, siblings first then primary — the order the caller needs to roll
        back in if a later step (e.g. updating an MCP config header) also fails.

        Extracted from import_oauth_as_secret() (issue #1871) so the standalone
        vault-secret guided-authorization flow can create the same shape of bundle
        without going through any MCP config at all.

        When `client_secret_reused` is True, `client_secret_name` refers to a secret
        that already exists (a `${secret:NAME}` reference in `config.oauth_client_secret`,
        issue #2005) — its value is overwritten in place if it drifted from the freshly
        observed one, but it is never appended to `created`: this method's own rollback
        deletes everything in `created` on failure, and a secret this call didn't create
        must never be deleted by that rollback.
        """
        from .models.secret_record import SecretRecord, SecretType

        created: list[str] = []
        try:
            if refresh_token_name and refresh_token:
                refresh_record = SecretRecord(
                    name=refresh_token_name,
                    type=SecretType.GENERIC,
                    target_hosts=[host],
                    created_at=now,
                    updated_at=now,
                )
                await vault.create_secret(refresh_record, refresh_token)
                created.append(refresh_token_name)

            if client_secret_name and client_secret_value:
                if client_secret_reused:
                    resolved = await vault.resolve_secrets_for_assignment([client_secret_name])
                    current_value = resolved[0]["value"] if resolved else None
                    if current_value != client_secret_value:
                        existing_meta = await vault.get_secret(client_secret_name)
                        if existing_meta is None:
                            # Deleted concurrently between the earlier existence
                            # check and here — fail closed rather than crash on
                            # SecretRecord.from_dict(None) (Decision 1).
                            raise LookupError(
                                f"404: Referenced client secret '{client_secret_name}' "
                                "no longer exists in vault"
                            )
                        cs_record = SecretRecord.from_dict(
                            {**existing_meta, "updated_at": now.isoformat()}
                        )
                        await vault.update_secret(client_secret_name, cs_record, client_secret_value)
                else:
                    cs_record = SecretRecord(
                        name=client_secret_name,
                        type=SecretType.GENERIC,
                        target_hosts=[host],
                        created_at=now,
                        updated_at=now,
                    )
                    await vault.create_secret(cs_record, client_secret_value)
                    created.append(client_secret_name)

            primary_record = SecretRecord(
                name=base_name,
                type=SecretType.OAUTH2,
                target_hosts=[host],
                scrub=scrub,
                refresh=refresh_spec,
                created_at=now,
                updated_at=now,
            )
            await vault.create_secret(primary_record, access_token)
            created.append(base_name)
        except Exception:
            for n in created:
                await vault.delete_secret(n)
            raise
        return created

    async def _replace_oauth_secret_bundle(
        self,
        *,
        vault,
        base_name: str,
        refresh_token_name: str | None,
        client_secret_name: str | None,
        tokens,
        client_secret_value: str | None,
        refresh_spec,
        scrub,
        host: str,
        now,
        expires_at_iso: str | None,
    ) -> dict:
        """Replace existing vault oauth2 secret bundle in-place (Reconnect flow).

        Enforces sibling ownership guard: refuses to replace if the existing primary
        record's refresh metadata doesn't point to the expected sibling names.
        """
        from .models.secret_record import SecretRecord

        # Verify primary record exists
        primary_meta = await vault.get_secret(base_name)
        if primary_meta is None:
            raise LookupError(f"404: Primary secret '{base_name}' not found for replace")

        # Sibling ownership guard
        existing_refresh = primary_meta.get("refresh") or {}
        if refresh_token_name:
            expected = existing_refresh.get("refresh_token_secret_name")
            if expected and expected != refresh_token_name:
                raise KeyError(
                    f"409: Sibling ownership guard: refresh sibling '{expected}' "
                    f"doesn't match expected '{refresh_token_name}'"
                )
        if client_secret_name:
            expected_cs = existing_refresh.get("client_secret_secret_name")
            if expected_cs and expected_cs != client_secret_name:
                raise KeyError(
                    f"409: Sibling ownership guard: client_secret sibling '{expected_cs}' "
                    f"doesn't match expected '{client_secret_name}'"
                )

        updated: list[str] = []

        # Update refresh token sibling value
        if refresh_token_name and tokens.refresh_token:
            rt_meta = await vault.get_secret(refresh_token_name)
            if rt_meta:
                rt_record = SecretRecord.from_dict({**rt_meta, "updated_at": now.isoformat()})
                await vault.update_secret(refresh_token_name, rt_record, tokens.refresh_token)
                updated.append(refresh_token_name)

        # Update client_secret sibling value
        if client_secret_name and client_secret_value:
            cs_meta = await vault.get_secret(client_secret_name)
            if cs_meta:
                cs_record = SecretRecord.from_dict({**cs_meta, "updated_at": now.isoformat()})
                await vault.update_secret(client_secret_name, cs_record, client_secret_value)
                updated.append(client_secret_name)

        # Update primary record: new access_token value + reset refresh metadata
        primary_record = SecretRecord.from_dict({**primary_meta, "updated_at": now.isoformat()})
        if primary_record.refresh and refresh_spec:
            # Issue #1871: standalone Reconnect leaves token_url/client_id/
            # authorization_endpoint editable (not locked to their original values),
            # so refresh these from the fresh refresh_spec too, not just expires_at.
            # No-op for MCP's reconnect path, which always recomputes identical values.
            primary_record.refresh.token_url = refresh_spec.token_url
            primary_record.refresh.client_id = refresh_spec.client_id
            primary_record.refresh.client_secret_secret_name = refresh_spec.client_secret_secret_name
            primary_record.refresh.authorization_endpoint = refresh_spec.authorization_endpoint
            primary_record.refresh.expires_at = refresh_spec.expires_at
            primary_record.refresh.last_refresh_at = None
            primary_record.refresh.last_refresh_status = None
            primary_record.refresh.last_refresh_error = None
        elif refresh_spec:
            primary_record.refresh = refresh_spec
        await vault.update_secret(base_name, primary_record, tokens.access_token)
        updated.append(base_name)

        # Reschedule background refresh with fresh expiry
        self.vault_refresh_manager.schedule_secret(base_name)

        return {
            "secrets_updated": updated,
            "header_injected": f"Authorization: ${{secret:{base_name}}}",
            "expires_at": expires_at_iso,
            "auto_refresh_enabled": refresh_spec is not None,
        }

    # =========================================================================
    # Standalone OAuth2 vault secrets — guided authorization flow (issue #1871)
    #
    # Unlike import_oauth_as_secret() above (which reads tokens already obtained via
    # an MCP server's OAuth connection), these methods run their own OAuth flow from
    # scratch against caller-supplied endpoints, with no MCP config involved at all.
    # Tokens are exchanged via OAuthFlowManager.start_flow_with_endpoints()/
    # complete_flow(persist=False) — persist=False is load-bearing: it means
    # FernetTokenStore (MCP-exclusive) is never touched, and tokens land only in the
    # vault/keyring via the same _create_oauth_secret_bundle()/_replace_oauth_secret_
    # bundle() helpers import_oauth_as_secret() uses.
    # =========================================================================

    async def standalone_oauth_initiate(
        self,
        base_name: str,
        authorization_endpoint: str,
        token_endpoint: str,
        client_id: str,
        redirect_uri: str,
        client_secret_secret_name: str | None = None,
        scopes: list[str] | None = None,
        custom_callback_path: str | None = None,
        custom_callback_port: int | None = None,
    ) -> dict:
        """Start a guided-authorization flow for a brand-new standalone oauth2 secret.

        Nothing is written to the vault here — only on standalone_oauth_complete().
        Pre-checks base_name (+ its derived refresh-token sibling name) don't already
        collide with an existing secret, so the user gets an immediate error instead
        of completing a whole provider consent flow only to fail at the end.

        Raises:
            ValueError: 400 — invalid base_name.
            KeyError: 409 — base_name or its refresh sibling already exists.
            LookupError: 404 — client_secret_secret_name doesn't resolve to a secret.
        """
        import re

        base_name_re = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
        if not base_name_re.match(base_name):
            raise ValueError("base_name must match ^[a-z0-9][a-z0-9_-]{0,62}$")

        vault = self.credential_vault
        refresh_token_name = f"{base_name}_refresh"
        for name in (base_name, refresh_token_name):
            if await vault.get_secret(name) is not None:
                raise KeyError(f"409: Secret '{name}' already exists; choose a different base_name")

        client_secret_value = None
        if client_secret_secret_name:
            client_secret_value = await self._resolve_sibling_secret_value(client_secret_secret_name)

        context = {
            "flow_type": "standalone_secret",
            "base_name": base_name,
            "client_secret_secret_name": client_secret_secret_name,
            "authorization_endpoint": authorization_endpoint,
            "token_endpoint": token_endpoint,
            "client_id": client_id,
        }
        auth_url, state = await self.oauth_manager.start_flow_with_endpoints(
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            client_id=client_id,
            redirect_uri=redirect_uri,
            client_secret=client_secret_value,
            scopes=scopes,
            persist=False,
            context=context,
        )
        return {
            "auth_url": auth_url,
            "flow_id": state,
            "custom_callback_path": custom_callback_path,
            "custom_callback_port": custom_callback_port,
        }

    async def standalone_oauth_reconnect_initiate(
        self,
        secret_name: str,
        authorization_endpoint: str,
        token_endpoint: str,
        client_id: str,
        redirect_uri: str,
        client_secret_secret_name: str | None = None,
        scopes: list[str] | None = None,
        custom_callback_path: str | None = None,
        custom_callback_port: int | None = None,
    ) -> dict:
        """Start Reconnect for an existing guided-flow oauth2 secret.

        Only secrets that were themselves created via the guided flow (i.e. have
        refresh.authorization_endpoint set) are eligible — a manually paste-in
        secret can never gain a Reconnect action; delete-and-recreate is the only
        route onto the guided flow (locked in during mockup review).

        Raises:
            LookupError: 404 — secret_name not found, or client_secret_secret_name
                doesn't resolve to a secret.
            ValueError: 400 — secret is not oauth2, or was not created via the
                guided flow.
        """
        vault = self.credential_vault
        existing = await vault.get_secret(secret_name)
        if existing is None:
            raise LookupError(f"404: Secret '{secret_name}' not found")
        if existing.get("type") != "oauth2":
            raise ValueError(f"Secret '{secret_name}' is not an oauth2 secret")
        if not (existing.get("refresh") or {}).get("authorization_endpoint"):
            raise ValueError(
                f"Secret '{secret_name}' was not created via the guided authorization "
                "flow and cannot be reconnected; delete and recreate it instead"
            )

        client_secret_value = None
        if client_secret_secret_name:
            client_secret_value = await self._resolve_sibling_secret_value(client_secret_secret_name)

        context = {
            "flow_type": "standalone_reconnect",
            "base_name": secret_name,
            "client_secret_secret_name": client_secret_secret_name,
            "authorization_endpoint": authorization_endpoint,
            "token_endpoint": token_endpoint,
            "client_id": client_id,
        }
        auth_url, state = await self.oauth_manager.start_flow_with_endpoints(
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            client_id=client_id,
            redirect_uri=redirect_uri,
            client_secret=client_secret_value,
            scopes=scopes,
            persist=False,
            context=context,
        )
        return {
            "auth_url": auth_url,
            "flow_id": state,
            "custom_callback_path": custom_callback_path,
            "custom_callback_port": custom_callback_port,
        }

    async def standalone_oauth_complete(self, state: str, code: str) -> dict:
        """Complete a standalone guided-authorization flow (new secret or Reconnect).

        Exchanges the code for tokens with persist=False (never touches
        FernetTokenStore), then builds/replaces the vault secret bundle via the same
        helpers import_oauth_as_secret() uses.

        Raises:
            NoRefreshTokenError: the provider didn't issue a refresh_token — no
                secret is created/modified in this case.
            KeyError: 409 — (new-secret path only) base_name or its refresh sibling
                was created by something else during the flow's lifetime.
            LookupError: 404 — (reconnect path only) the target secret vanished
                during the flow's lifetime.
        """
        from datetime import UTC, datetime
        from urllib.parse import urlparse

        from .models.secret_record import RefreshSpec, ScrubSpec

        token, context = await self.oauth_manager.complete_flow(
            state, code, persist=False
        )

        if not token.refresh_token:
            raise NoRefreshTokenError(
                "Authorization succeeded, but the provider did not issue a refresh "
                "token. A secret without a refresh token cannot support proactive "
                "refresh, so nothing was saved. Some providers only issue a refresh "
                "token when offline access is explicitly requested — try adding an "
                "offline-access scope, or check the provider's settings, then retry."
            )

        flow_type = context.get("flow_type")
        base_name = context["base_name"]
        client_secret_secret_name = context.get("client_secret_secret_name")
        token_endpoint = context["token_endpoint"]
        authorization_endpoint = context.get("authorization_endpoint")
        client_id = context["client_id"]

        vault = self.credential_vault
        now = datetime.now(UTC)
        # .hostname (not .netloc): target_hosts validation rejects a port component,
        # and guided-flow providers commonly run on a non-standard port (self-hosted,
        # enterprise, local dev) — found via manual E2E testing against a local stub.
        host = urlparse(token_endpoint).hostname or urlparse(token_endpoint).netloc
        token_path = urlparse(token_endpoint).path
        refresh_token_name = f"{base_name}_refresh"

        scrub = ScrubSpec(
            url_path=token_path,
            matcher_jsonpath="$.access_token",
            update_on_change=True,
        )

        expires_at_dt = None
        if token.expires_in:
            from mcp.shared.auth_utils import calculate_token_expiry
            expiry_ts = calculate_token_expiry(token.expires_in)
            if expiry_ts:
                expires_at_dt = datetime.fromtimestamp(expiry_ts, tz=UTC)
        expires_at_iso = expires_at_dt.isoformat() if expires_at_dt else None

        refresh_spec = RefreshSpec(
            token_url=token_endpoint,
            client_id=client_id,
            refresh_token_secret_name=refresh_token_name,
            client_secret_secret_name=client_secret_secret_name,
            authorization_endpoint=authorization_endpoint,
            expires_at=expires_at_dt,
            buffer_seconds=300,
        )

        # Note: client_secret_name/client_secret_value are always None below — unlike
        # import_oauth_as_secret()'s MCP path (which creates a *fresh* client-secret
        # sibling from FernetTokenStore's client_info), a standalone flow's client
        # secret is always a *reference* to an already-existing vault secret (created
        # separately via the frontend's inline "+ New secret" affordance, per the
        # mockup's locked-in decision #1). Neither creating nor overwriting someone
        # else's already-existing secret value is ever correct here — only
        # refresh_spec.client_secret_secret_name records the reference.
        if flow_type == "standalone_reconnect":
            result = await self._replace_oauth_secret_bundle(
                vault=vault,
                base_name=base_name,
                refresh_token_name=refresh_token_name,
                client_secret_name=None,
                tokens=token,
                client_secret_value=None,
                refresh_spec=refresh_spec,
                scrub=scrub,
                host=host,
                now=now,
                expires_at_iso=expires_at_iso,
            )
        else:
            for name in (base_name, refresh_token_name):
                if await vault.get_secret(name) is not None:
                    raise KeyError(
                        f"409: Secret '{name}' already exists; choose a different base_name"
                    )
            created = await self._create_oauth_secret_bundle(
                vault=vault,
                base_name=base_name,
                refresh_token_name=refresh_token_name,
                client_secret_name=None,
                access_token=token.access_token,
                refresh_token=token.refresh_token,
                client_secret_value=None,
                refresh_spec=refresh_spec,
                scrub=scrub,
                host=host,
                now=now,
            )
            self.vault_refresh_manager.schedule_secret(base_name)
            result = {
                "secrets_created": created,
                "expires_at": expires_at_iso,
                "auto_refresh_enabled": True,
            }

        result["flow_id"] = state
        result["base_name"] = base_name
        return result

    async def standalone_oauth_cancel(self, flow_id: str) -> bool:
        """Cancel a pending standalone OAuth flow (user closed the panel before the
        popup completed). Pops the pending flow if present — the caller is
        responsible for tearing down any transient callback listener/route, since
        that's route-wiring state this service layer has no access to.

        Returns True if a pending flow was found and cancelled, False if it had
        already completed or already expired.
        """
        context = await self.oauth_manager.cancel_pending(flow_id)
        return context is not None

    async def _resolve_sibling_secret_value(self, secret_name: str) -> str:
        """Resolve a plain (non-${secret:}-syntax) sibling secret reference to its
        plaintext value — the same lookup-by-name pattern client_secret_secret_name
        and refresh_token_secret_name already use elsewhere in this file.

        Raises LookupError if the name doesn't resolve to an existing secret.
        """
        from .secrets_keyring import get_secret_value

        vault = self.credential_vault
        if await vault.get_secret(secret_name) is None:
            raise LookupError(f"404: Secret '{secret_name}' not found")
        value = get_secret_value(secret_name)
        if value is None:
            raise LookupError(f"404: Secret '{secret_name}' not found")
        return value
