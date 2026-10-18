"""
OAuth 2.1 flow manager for MCP servers (issue #813).

Uses mcp library utilities for all OAuth protocol logic:
- PKCEParameters.generate() for PKCE (mcp.client.auth.oauth2)
- Discovery utils for protected resource + authorization server metadata
- Dynamic Client Registration (DCR) via mcp.client.auth.utils
- Token exchange via handle_token_response_scopes

Token storage uses Fernet symmetric encryption:
- Key file:          {data_dir}/oauth_key.bin
- Per-server token:  {data_dir}/oauth_tokens/{server_id}.enc
- Per-server client: {data_dir}/oauth_clients/{server_id}.enc
"""

import json
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from cryptography.fernet import Fernet
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_client_info_from_metadata_url,
    create_client_registration_request,
    create_oauth_metadata_request,
    handle_protected_resource_response,
    handle_registration_response,
    handle_token_response_scopes,
    is_valid_client_metadata_url,
    should_use_client_metadata_url,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
)
from mcp.shared.auth_utils import calculate_token_expiry
from pydantic import ValidationError

from shared.logging_config import get_logger

# Dual-logger pattern (see shared/logging_config.py's module docstring): `logger` is for
# conditions that should always be visible (error.log + console) with no flag required;
# `debug_logger` is opt-in verbose tracing, gated behind --debug-oauth (issue #1867 —
# previously oauth logging used only a bare, unregistered module logger and was
# completely invisible regardless of --debug-all).
logger = logging.getLogger(__name__)
debug_logger = get_logger('oauth', category='OAUTH')

# Issue #1871: how long an unfinished flow (MCP or standalone) may sit in `_pending`
# before a lazy sweep discards it. Existing behavior (no TTL at all) is preserved for
# the lifetime of a normal flow — this only reclaims abandoned ones.
_PENDING_TTL_SECONDS = 600

# Issue #2240: AS metadata endpoint fields that may be served as paths relative
# to the server's own origin instead of absolute URLs (e.g. Home Assistant).
# RFC 8414 requires absolute AnyHttpUrl values, so the SDK's own
# OAuthMetadata.model_validate() rejects these outright; _normalize_oauth_metadata_document
# resolves them against the discovery URL's origin before validation is attempted.
_RELATIVE_ENDPOINT_FIELDS = (
    "authorization_endpoint",
    "token_endpoint",
    "registration_endpoint",
    "revocation_endpoint",
)


def _normalize_oauth_metadata_document(raw: dict, discovery_url: str) -> dict:
    """Best-effort fixups for AS metadata documents that violate RFC 8414 in two
    specific, observed ways (issue #2240): endpoint paths relative to the
    server's own origin instead of absolute URLs, and a missing `issuer`.

    Relative endpoints resolve against the discovery URL's own origin — not
    `server_url`/`auth_server_url` — since RFC 8414 path-aware discovery can
    target a different path on the same host than the resource server. A
    missing/falsy `issuer` defaults to that same origin; an `issuer` that is
    merely present but different is left untouched.
    """
    parsed = urlparse(discovery_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    normalized = dict(raw)
    for field in _RELATIVE_ENDPOINT_FIELDS:
        value = normalized.get(field)
        if isinstance(value, str) and value and not urlparse(value).netloc:
            normalized[field] = urljoin(origin, value)

    if not normalized.get("issuer"):
        normalized["issuer"] = origin

    return normalized


class FernetTokenStore:
    """Encrypted per-server token storage using Fernet symmetric encryption.

    Implements the TokenStorage protocol from mcp.client.auth.oauth2.
    """

    def __init__(self, data_dir: Path, server_id: str):
        self._server_id = server_id
        self._key_file = data_dir / "oauth_key.bin"
        self._tokens_dir = data_dir / "oauth_tokens"
        self._clients_dir = data_dir / "oauth_clients"
        self._expiry_dir = data_dir / "oauth_expiry"
        self._endpoints_dir = data_dir / "oauth_endpoints"
        self._tokens_dir.mkdir(parents=True, exist_ok=True)
        self._clients_dir.mkdir(parents=True, exist_ok=True)
        self._expiry_dir.mkdir(parents=True, exist_ok=True)
        self._endpoints_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._get_or_create_key())

    def _get_or_create_key(self) -> bytes:
        """Load existing Fernet key or generate one on first run."""
        if self._key_file.exists():
            return self._key_file.read_bytes()
        key = Fernet.generate_key()
        self._key_file.write_bytes(key)
        return key

    async def get_tokens(self) -> OAuthToken | None:
        """Return decrypted OAuthToken for this server, or None."""
        token_file = self._tokens_dir / f"{self._server_id}.enc"
        if not token_file.exists():
            return None
        try:
            encrypted = token_file.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            return OAuthToken.model_validate_json(decrypted)
        except Exception:
            logger.error("Failed to decrypt token for MCP server %s", self._server_id)
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Encrypt and persist OAuthToken for this server.

        Also records the absolute expiry timestamp using calculate_token_expiry so
        that get_token_expiry() can check staleness without knowing the issue time.
        """
        token_file = self._tokens_dir / f"{self._server_id}.enc"
        encrypted = self._fernet.encrypt(tokens.model_dump_json().encode())
        token_file.write_bytes(encrypted)
        # Record absolute expiry timestamp at storage time
        expiry = calculate_token_expiry(tokens.expires_in)
        expiry_file = self._expiry_dir / f"{self._server_id}.expiry"
        if expiry is not None:
            expiry_file.write_text(str(expiry))
        elif expiry_file.exists():
            expiry_file.unlink()

    async def get_token_expiry(self) -> float | None:
        """Return the stored absolute expiry timestamp, or None if not recorded."""
        expiry_file = self._expiry_dir / f"{self._server_id}.expiry"
        if not expiry_file.exists():
            return None
        try:
            return float(expiry_file.read_text().strip())
        except Exception:
            return None

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Return decrypted OAuthClientInformationFull for this server, or None."""
        client_file = self._clients_dir / f"{self._server_id}.enc"
        if not client_file.exists():
            return None
        try:
            encrypted = client_file.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            return OAuthClientInformationFull.model_validate_json(decrypted)
        except Exception:
            logger.error("Failed to decrypt client info for MCP server %s", self._server_id)
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Encrypt and persist OAuthClientInformationFull for this server."""
        client_file = self._clients_dir / f"{self._server_id}.enc"
        encrypted = self._fernet.encrypt(client_info.model_dump_json().encode())
        client_file.write_bytes(encrypted)

    def set_token_endpoint(self, token_endpoint: str) -> None:
        """Persist the token endpoint URL for this server (plaintext — not a secret)."""
        endpoint_file = self._endpoints_dir / f"{self._server_id}.endpoint"
        endpoint_file.write_text(token_endpoint)

    def get_token_endpoint(self) -> str | None:
        """Return the stored token endpoint URL, or None if not recorded."""
        endpoint_file = self._endpoints_dir / f"{self._server_id}.endpoint"
        if not endpoint_file.exists():
            return None
        text = endpoint_file.read_text().strip()
        return text if text else None

    async def clear(self) -> None:
        """Delete all stored data (tokens + client info + expiry + endpoint) for this server."""
        for f in (
            self._tokens_dir / f"{self._server_id}.enc",
            self._clients_dir / f"{self._server_id}.enc",
            self._expiry_dir / f"{self._server_id}.expiry",
            self._endpoints_dir / f"{self._server_id}.endpoint",
        ):
            if f.exists():
                f.unlink()


class OAuthFlowManager:
    """Manages OAuth 2.1 authorization code flows for MCP servers.

    Stateless across restarts except for in-memory pending flows. Each call to
    start_flow() generates a fresh PKCE verifier and state token, stores the
    pending context, and returns the authorization URL.  complete_flow() reads
    token_endpoint and client_id from that stored pending context — it never
    recomputes them.
    """

    def __init__(self, data_dir: Path):
        self._data_dir = data_dir
        # Keyed by OAuth `state` token: {server_id, token_endpoint, client_id,
        # code_verifier, redirect_uri, created_at, context}
        self._pending: dict[str, dict] = {}
        # Issue #2240 (AC5): last start_flow() discovery outcome per server_id, for
        # callers to surface alongside the auth URL without changing start_flow()'s
        # own return type.
        self._discovery_summaries: dict[str, dict] = {}

    def get_token_store(self, server_id: str) -> FernetTokenStore:
        """Return a FernetTokenStore for the given server_id."""
        return FernetTokenStore(self._data_dir, server_id)

    async def get_stored_token(self, server_id: str) -> OAuthToken | None:
        """Return the persisted OAuthToken for a server, or None if not authenticated."""
        return await self.get_token_store(server_id).get_tokens()

    def _sweep_expired_pending(self) -> None:
        """Issue #1871: lazily discard `_pending` entries older than the TTL.

        Called on every start_flow()/start_flow_with_endpoints() rather than via a
        background task — cheap, and abandoned flows (MCP or standalone) only need to
        be reclaimed eventually, not immediately. A standalone flow's transient
        callback listener (if any) is torn down separately by the caller when it
        detects the flow can no longer be completed; this only frees the in-memory
        bookkeeping.
        """
        now = time.time()
        expired = [
            state for state, pending in self._pending.items()
            if now - pending.get("created_at", now) > _PENDING_TTL_SECONDS
        ]
        for state in expired:
            debug_logger.info("Discarding expired pending OAuth flow (state=%s…)", state[:8])
            del self._pending[state]

    async def get_pending_context(self, state: str) -> dict | None:
        """Peek (without popping) the caller-supplied `context` dict for a pending
        flow, or None if no such pending flow exists. Used to route a callback to the
        right completion handler (MCP vs standalone) before completing it. Async for
        consistency with every other public method here, though the lookup itself is
        synchronous."""
        pending = self._pending.get(state)
        return pending.get("context") if pending else None

    async def cancel_pending(self, state: str) -> dict | None:
        """Remove a pending flow without completing it (user cancellation, or a
        provider-side denial/error that will never reach complete_flow()). Returns
        the removed context dict, or None if no such pending flow existed."""
        pending = self._pending.pop(state, None)
        return pending.get("context") if pending else None

    def get_discovery_summary(self, server_id: str) -> dict | None:
        """Return the most recent start_flow() discovery outcome for server_id, or
        None if start_flow() hasn't run (successfully) for it yet. Issue #2240
        (AC5): a side channel for callers that want to surface discovery details
        without start_flow()'s own return type changing."""
        return self._discovery_summaries.get(server_id)

    async def _fetch_as_metadata(
        self, response: httpx.Response, discovery_url: str
    ) -> tuple[bool, OAuthMetadata | None, bool]:
        """Mirrors mcp.client.auth.utils.handle_auth_metadata_response's status-code
        branching exactly — same (ok, metadata) contract, so the caller's discovery
        loop/fallback behavior is unaffected — but on the 200 branch, normalizes the
        raw body (_normalize_oauth_metadata_document) before validating, so servers
        that violate RFC 8414 with relative endpoints/no issuer (e.g. Home Assistant,
        issue #2240) still validate. Logs at WARNING, rather than silently swallowing,
        when a document still fails validation after normalization.

        The third element reports whether normalization actually changed anything in
        a 200 response's body — used only for the discovery-summary side channel
        (AC5); it has no effect on the (ok, metadata) contract.
        """
        if response.status_code == 200:
            try:
                content = await response.aread()
                raw = json.loads(content)
                normalized = _normalize_oauth_metadata_document(raw, discovery_url)
                asm = OAuthMetadata.model_validate(normalized)
                return True, asm, normalized != raw
            except (ValueError, ValidationError) as exc:
                debug_logger.warning(
                    "AS metadata at %s rejected by validation even after normalization: %s",
                    discovery_url, exc,
                )
                return True, None, False
        elif 300 <= response.status_code < 500:
            return True, None, False  # Not served at this URL - try the next candidate
        return False, None, False  # Server error or unexpected status, stop trying

    async def start_flow(
        self,
        server_id: str,
        server_url: str,
        redirect_uri: str,
        client_name: str = "Claude Code WebUI",
        pre_registered_client_id: str | None = None,
        client_secret: str | None = None,
        client_metadata_url: str | None = None,
    ) -> str:
        """Initiate an OAuth 2.1 authorization code flow.

        If pre_registered_client_id is supplied (from McpServerConfig.oauth_client_id),
        DCR is skipped entirely and the provided client_id is used directly. This is
        required for servers like Slack that do not support Dynamic Client Registration.

        If client_secret is also supplied (from McpServerConfig.oauth_client_secret,
        resolved from the vault by the caller), the pre-registered client is treated as
        confidential (token_endpoint_auth_method="client_secret_post") instead of public
        — required for servers like Google that reject public-client token exchanges.
        The authorization request also adds access_type=offline + prompt=consent in this
        case, since Google never issues a refresh_token without them.

        client_metadata_url (issue #2231): the URL of this app's own hosted OAuth
        client-ID metadata document (draft-ietf-oauth-client-id-metadata-document). Used
        as a third fallback, below pre_registered_client_id and DCR, for servers that
        advertise client_id_metadata_document_supported=True but no registration_endpoint
        (e.g. Home Assistant). Withheld (None) by the caller when unusable (a custom
        OAuth callback path/port is configured for this server) — see
        oauth_secret_bundle_manager.oauth_initiate_flow().

        Steps performed:
          1. Protected resource metadata discovery (RFC 9728)
          2. Authorization server metadata discovery (RFC 8414)
          3. Dynamic Client Registration (RFC 7591) — skipped when pre_registered_client_id
             set, or replaced by the client-ID metadata document fallback above
          4. PKCE parameter generation
          5. Authorization URL construction
          6. Pending state storage (includes token_endpoint + client_id for complete_flow)

        Returns the authorization URL the browser should navigate to.
        """
        debug_logger.debug(
            "start_flow: server_id=%s server_url=%s pre_registered_client_id=%s "
            "confidential=%s client_metadata_url=%s",
            server_id, server_url, pre_registered_client_id, bool(client_secret),
            client_metadata_url,
        )
        self._sweep_expired_pending()
        # Drop any summary from a prior successful call for this server_id up front, so a
        # retry that raises partway through (e.g. the AC4 check, or a DCR failure) leaves
        # get_discovery_summary() returning None instead of stale, no-longer-trustworthy
        # data from the earlier attempt.
        self._discovery_summaries.pop(server_id, None)
        summary: dict = {}
        async with httpx.AsyncClient() as http:
            # --- Step 1: Protected resource metadata ---
            prm = None
            auth_server_url = None
            for url in build_protected_resource_metadata_discovery_urls(None, server_url):
                req = create_oauth_metadata_request(url)
                try:
                    resp = await http.send(req)
                    prm = await handle_protected_resource_response(resp)
                    if prm:
                        if prm.authorization_servers:
                            auth_server_url = str(prm.authorization_servers[0])
                        break
                except Exception as exc:
                    debug_logger.debug("PRM discovery at %s failed: %s", url, exc)
                    continue
            summary["prm_found"] = bool(prm)
            debug_logger.info(
                "PRM discovery result: found=%s auth_server_url=%s", bool(prm), auth_server_url
            )

            # --- Step 2: Authorization server metadata ---
            oauth_metadata = None
            as_metadata_normalized = False
            as_metadata_rejected = False
            for url in build_oauth_authorization_server_metadata_discovery_urls(
                auth_server_url, server_url
            ):
                req = create_oauth_metadata_request(url)
                try:
                    resp = await http.send(req)
                    ok, meta, normalized = await self._fetch_as_metadata(resp, url)
                    if meta:
                        oauth_metadata = meta
                        as_metadata_normalized = normalized
                        break
                    if not ok:
                        debug_logger.debug("AS metadata discovery at %s rejected (not ok)", url)
                        break
                    if resp.status_code == 200:
                        as_metadata_rejected = True
                except Exception as exc:
                    debug_logger.debug("AS metadata discovery at %s failed: %s", url, exc)
                    continue
            summary["as_metadata_found"] = bool(oauth_metadata)
            summary["as_metadata_normalized"] = as_metadata_normalized
            summary["as_metadata_rejected"] = as_metadata_rejected
            debug_logger.info("AS metadata discovery result: found=%s", bool(oauth_metadata))

            # --- Derive requested scopes from discovery metadata ---
            # Prefer PRM scopes (RFC 9728) as they represent what the resource needs.
            # Fall back to AS scopes (RFC 8414). Omit if neither advertises scopes.
            requested_scopes: list[str] | None = None
            if prm and prm.scopes_supported:
                requested_scopes = prm.scopes_supported
            elif oauth_metadata and oauth_metadata.scopes_supported:
                requested_scopes = oauth_metadata.scopes_supported

            # --- Derive endpoints ---
            parsed = urlparse(server_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            if oauth_metadata and oauth_metadata.token_endpoint:
                token_endpoint = str(oauth_metadata.token_endpoint)
            else:
                token_endpoint = f"{base}/token"

            if oauth_metadata and oauth_metadata.authorization_endpoint:
                auth_endpoint = str(oauth_metadata.authorization_endpoint)
            else:
                auth_endpoint = f"{base}/authorize"

            debug_logger.debug(
                "Endpoints derived: token_endpoint=%s auth_endpoint=%s",
                token_endpoint, auth_endpoint,
            )

            # --- Step 3: Dynamic Client Registration ---
            store = self.get_token_store(server_id)
            has_registration_endpoint = bool(
                oauth_metadata and oauth_metadata.registration_endpoint
            )

            if pre_registered_client_id:
                # Issue #2240 (AC4): a pre-registered client_id is meaningless for a
                # server that only supports CIMD (no registration_endpoint at all, so
                # there's no "registration" to be pre-registered with) — fail clearly
                # instead of sending a client_id the server will never recognize.
                cimd_only = (
                    not has_registration_endpoint
                    and oauth_metadata is not None
                    and bool(oauth_metadata.client_id_metadata_document_supported)
                )
                if cimd_only and not is_valid_client_metadata_url(pre_registered_client_id):
                    raise RuntimeError(
                        f"MCP server {server_id!r} is configured with "
                        f"oauth_client_id={pre_registered_client_id!r}, but this server "
                        f"only supports the OAuth Client ID Metadata Document mechanism "
                        f"(draft-ietf-oauth-client-id-metadata-document), not a "
                        f"pre-registered client_id. Remove 'oauth_client_id' from this "
                        f"server's config to let CIMD take over automatically."
                    )
                # Pre-registered app (e.g. Slack, Google): skip DCR and use the configured
                # client_id. Servers that don't support RFC 7591 DCR must be handled this way.
                auth_method = "client_secret_post" if client_secret else "none"
                client_info = OAuthClientInformationFull(
                    client_id=pre_registered_client_id,
                    client_secret=client_secret,
                    redirect_uris=[redirect_uri],  # type: ignore[arg-type]
                    token_endpoint_auth_method=auth_method,
                )
                # Persist so refresh_token() can read client_id/secret back later — previously
                # only the DCR branch below did this, leaving refresh_token() unable to find
                # client info for any pre-registered client (issue #1867's latent bug).
                await store.set_client_info(client_info)
                summary["mechanism"] = "pre_registered"
                debug_logger.info(
                    "OAuth: using pre-registered client_id for MCP server %s (auth_method=%s)",
                    server_id, auth_method,
                )
            else:
                client_info = await store.get_client_info()
                if client_info:
                    summary["mechanism"] = "cached"

            if not client_info:
                if (
                    not has_registration_endpoint
                    and should_use_client_metadata_url(oauth_metadata, client_metadata_url)
                    and is_valid_client_metadata_url(client_metadata_url)
                ):
                    # Issue #2231: server supports CIMD but not DCR — use this app's
                    # own hosted client-metadata document as the client_id instead of
                    # attempting a blind (and for this class of server, doomed) DCR POST.
                    client_info = create_client_info_from_metadata_url(
                        client_metadata_url, redirect_uris=[redirect_uri],  # type: ignore[arg-type]
                    )
                    await store.set_client_info(client_info)
                    summary["mechanism"] = "cimd"
                    debug_logger.info(
                        "OAuth: using client-ID metadata document for MCP server %s (%s)",
                        server_id, client_metadata_url,
                    )
                else:
                    client_metadata = OAuthClientMetadata(
                        redirect_uris=[redirect_uri],  # type: ignore[arg-type]
                        client_name=client_name,
                        grant_types=["authorization_code", "refresh_token"],
                        response_types=["code"],
                        token_endpoint_auth_method="none",
                    )
                    reg_req = create_client_registration_request(
                        oauth_metadata, client_metadata, auth_endpoint
                    )
                    try:
                        reg_resp = await http.send(reg_req)
                        client_info = await handle_registration_response(reg_resp)
                        await store.set_client_info(client_info)
                        summary["mechanism"] = "dcr"
                        debug_logger.info("DCR succeeded for MCP server %s", server_id)
                    except Exception as exc:
                        debug_logger.warning(
                            "DCR failed for MCP server %s (%s); no pre-registered client_id available",
                            server_id,
                            exc,
                        )
                        if (
                            not has_registration_endpoint
                            and oauth_metadata
                            and oauth_metadata.client_id_metadata_document_supported
                        ):
                            # Distinct failure mode (AC6): this server needs CIMD, not
                            # DCR, and CIMD wasn't usable — name why specifically instead
                            # of reusing the generic "set oauth_client_id manually"
                            # message, which doesn't solve anything for this class of
                            # server.
                            if client_metadata_url is None:
                                reason = (
                                    "no usable client-ID metadata document URL was "
                                    "available (a custom OAuth callback path/port is "
                                    "configured for this server, which is incompatible "
                                    "with the shared metadata document)"
                                )
                            else:
                                reason = (
                                    "this app's own origin is not HTTPS, which the "
                                    "client-ID metadata document mechanism requires"
                                )
                            raise RuntimeError(
                                f"MCP server {server_id!r} requires the OAuth Client ID "
                                f"Metadata Document mechanism (draft-ietf-oauth-client-id-"
                                f"metadata-document) rather than Dynamic Client Registration, "
                                f"but {reason}. Remove any custom OAuth callback path/port "
                                f"for this server, and ensure this app is served over HTTPS, "
                                f"then retry."
                            ) from exc
                        raise RuntimeError(
                            f"OAuth Dynamic Client Registration failed for MCP server {server_id!r} "
                            f"and no oauth_client_id is configured. Set 'oauth_client_id' in the "
                            f"MCP server config to use a pre-registered app."
                        ) from exc

            client_id = client_info.client_id

            # --- Step 4 + 5: PKCE + authorization URL ---
            pkce = PKCEParameters.generate()
            state = secrets.token_urlsafe(32)

            params = {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": pkce.code_challenge,
                "code_challenge_method": "S256",
            }
            if requested_scopes:
                params["scope"] = " ".join(requested_scopes)
            if client_secret:
                # Google-specific but widely-supported params, required to ever receive a
                # refresh_token for a confidential client: Google only issues one when
                # access_type=offline is requested, and only re-issues it on an already-
                # granted app when prompt=consent forces the consent screen again — issue
                # #1867's "No refresh token was stored" symptom. Scoped to client_secret
                # (confidential) requests only so the public-client (Slack) path is
                # unaffected.
                params["access_type"] = "offline"
                params["prompt"] = "consent"
            auth_url = f"{auth_endpoint}?{urlencode(params)}"

            # --- Step 6: Persist pending state ---
            self._pending[state] = {
                "server_id": server_id,
                "token_endpoint": token_endpoint,
                "client_id": client_id,
                "code_verifier": pkce.code_verifier,
                "redirect_uri": redirect_uri,
                "requested_scopes": requested_scopes,
                "created_at": time.time(),
                "context": {"flow_type": "mcp"},
            }

            self._discovery_summaries[server_id] = summary
            debug_logger.info("OAuth discovery summary for %s: %s", server_id, summary)
            debug_logger.info(
                "OAuth flow started for MCP server %s (state=%s…)", server_id, state[:8]
            )
            return auth_url

    async def start_flow_with_endpoints(
        self,
        *,
        authorization_endpoint: str,
        token_endpoint: str,
        client_id: str,
        redirect_uri: str,
        client_secret: str | None = None,
        scopes: list[str] | None = None,
        persist: bool = False,
        context: dict | None = None,
    ) -> tuple[str, str]:
        """Initiate a no-discovery OAuth 2.1 authorization code flow (issue #1871).

        Unlike start_flow(), this skips RFC 9728/8414 discovery and RFC 7591 DCR
        entirely — the caller already knows authorization_endpoint, token_endpoint,
        and client_id (standalone vault-secret guided authorization has no MCP server
        URL to discover from). MCP flows are unaffected; they keep using the
        discovery-based start_flow() above.

        `context` is an opaque caller-supplied dict stashed alongside the pending flow
        and returned verbatim by complete_flow(persist=False) — used to carry
        flow-routing info (e.g. flow_type, base_name) the caller needs on completion.

        Returns (auth_url, state) — state doubles as the flow_id for standalone
        callers since it's already globally unique (secrets.token_urlsafe(32)).
        """
        self._sweep_expired_pending()
        pkce = PKCEParameters.generate()
        state = secrets.token_urlsafe(32)

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
        }
        if scopes:
            params["scope"] = " ".join(scopes)
        if client_secret:
            # Mirrors start_flow()'s confidential-client handling above — needed for
            # providers (e.g. Google) that only issue a refresh_token with these set.
            params["access_type"] = "offline"
            params["prompt"] = "consent"
        auth_url = f"{authorization_endpoint}?{urlencode(params)}"

        self._pending[state] = {
            "server_id": None,
            "token_endpoint": token_endpoint,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": pkce.code_verifier,
            "redirect_uri": redirect_uri,
            "requested_scopes": scopes,
            "created_at": time.time(),
            "persist": persist,
            "context": context or {},
        }
        debug_logger.info("Standalone OAuth flow started (state=%s…)", state[:8])
        return auth_url, state

    async def complete_flow(
        self, state: str, code: str, persist: bool = True
    ) -> str | tuple[OAuthToken, dict]:
        """Complete an OAuth flow by exchanging the authorization code for tokens.

        Reads token_endpoint and client_id from the pending state stored by
        start_flow() — these values are never recomputed here.

        `persist` (issue #1871): when True (the default — every existing MCP caller
        omits this, so their behavior is unchanged byte-for-byte), tokens are written
        to FernetTokenStore and the return value is server_id, exactly as before. When
        False (standalone vault-secret flows only), FernetTokenStore is never touched —
        the caller-supplied `client_secret` stashed at start_flow_with_endpoints() time
        is used directly instead of reading it back from a token store, and the return
        value is `(token, context)`: the raw exchanged OAuthToken plus whatever context
        dict the caller stashed at initiate time, for the caller to persist itself
        (via the vault, not FernetTokenStore — that store stays MCP-exclusive).

        Raises ValueError on failure.
        """
        debug_logger.debug(
            "complete_flow: state=%s…, %d pending flow(s) in memory",
            state[:8], len(self._pending),
        )
        pending = self._pending.pop(state, None)
        if not pending:
            # Common causes: backend process restarted between start_flow() and this
            # callback (in-memory _pending is wiped on restart), or a replayed/duplicate
            # callback for a state already consumed. Always logged (not gated behind
            # --debug-oauth) since it fully explains an otherwise-mysterious OAuth failure.
            logger.error(
                "complete_flow: no pending OAuth flow for state=%s… — either the backend "
                "restarted since start_flow() was called, or this callback was already "
                "consumed", state[:8],
            )
            raise ValueError(f"No pending OAuth flow for state={state!r}")

        token_endpoint: str = pending["token_endpoint"]
        client_id: str = pending["client_id"]
        code_verifier: str = pending["code_verifier"]
        redirect_uri: str = pending["redirect_uri"]
        requested_scopes: list[str] | None = pending.get("requested_scopes")

        store = None
        if persist:
            server_id: str | None = pending["server_id"]
            if server_id is None:
                # A standalone flow (server_id is always None — see
                # start_flow_with_endpoints()) reaching here means a caller invoked
                # complete_flow() without persist=False, bypassing
                # OAuthCallbackListenerManager's flow-type routing. Fail loudly rather
                # than silently reading/writing FernetTokenStore under a literal
                # "None" server_id — that store is MCP-exclusive.
                raise ValueError(
                    f"complete_flow(persist=True) called for a standalone flow "
                    f"(state={state[:8]}…) — this would corrupt FernetTokenStore; "
                    f"the caller must pass persist=False for non-MCP flows"
                )
            # Confidential clients (issue #1867) must include client_secret in the token
            # exchange body. start_flow() persists client_info for both the pre-registered
            # and DCR branches; DCR clients never have a secret so this is a no-op for them.
            store = self.get_token_store(server_id)
            client_info = await store.get_client_info()
            client_secret = client_info.client_secret if client_info else None
        else:
            # Standalone flows have no FernetTokenStore-backed server_id to read a
            # client_secret back from — start_flow_with_endpoints() stashed it directly.
            client_secret = pending.get("client_secret")

        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        if requested_scopes:
            token_data["scope"] = " ".join(requested_scopes)
        if client_secret:
            token_data["client_secret"] = client_secret
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        async with httpx.AsyncClient() as http:
            resp = await http.post(token_endpoint, data=token_data, headers=headers)
            if resp.status_code != 200:
                body = await resp.aread()
                raise ValueError(
                    f"Token exchange failed ({resp.status_code}): {body.decode()}"
                )
            token = await handle_token_response_scopes(resp)

        if not persist:
            debug_logger.info("Standalone OAuth flow completed (state=%s…)", state[:8])
            return token, pending.get("context") or {}

        await store.set_tokens(token)
        # Issue #976: Persist token endpoint so refresh_token() can use it later.
        store.set_token_endpoint(token_endpoint)
        debug_logger.info(
            "OAuth flow complete for MCP server %s (confidential=%s)",
            server_id, bool(client_secret),
        )
        return server_id

    async def refresh_token(self, server_id: str) -> OAuthToken | None:
        """Refresh the OAuth access token for a server using the stored refresh token.

        Implements RFC 6749 §6. If the refresh response includes a new refresh_token
        it replaces the old one; otherwise the original refresh_token is preserved.

        Returns the new OAuthToken on success, or None if refresh is not possible
        (no stored token/refresh_token, no persisted endpoint, or server rejected the
        refresh — in which case stored tokens are cleared so the user must re-authenticate).
        """
        store = self.get_token_store(server_id)

        # Read stored token
        token = await store.get_tokens()
        if token is None:
            debug_logger.debug("No stored token for MCP server %s — cannot refresh", server_id)
            return None
        if not token.refresh_token:
            debug_logger.debug("No refresh_token for MCP server %s — cannot refresh", server_id)
            return None

        # Read stored client info for client_id
        client_info = await store.get_client_info()
        if client_info is None:
            # Always visible (not gated behind --debug-oauth): this means the token can
            # never be refreshed until the user re-authenticates — a silent, otherwise
            # invisible cause of "it worked once, now it doesn't" (issue #1867).
            logger.error("No stored client info for MCP server %s — cannot refresh", server_id)
            return None

        # Retrieve persisted token endpoint
        token_endpoint = store.get_token_endpoint()
        if not token_endpoint:
            logger.error(
                "No stored token endpoint for MCP server %s — cannot refresh", server_id
            )
            return None

        refresh_data = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": client_info.client_id,
        }
        if client_info.client_secret:
            refresh_data["client_secret"] = client_info.client_secret
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(token_endpoint, data=refresh_data, headers=headers)
                if resp.status_code != 200:
                    body = await resp.aread()
                    logger.error(
                        "OAuth refresh failed for MCP server %s (%s): %s",
                        server_id,
                        resp.status_code,
                        body.decode()[:200],
                    )
                    # Refresh token is invalid/revoked — clear everything so user re-auths
                    await store.clear()
                    return None
                new_token = await handle_token_response_scopes(resp)
        except Exception as exc:
            logger.error("OAuth refresh request failed for MCP server %s: %s", server_id, exc)
            return None

        # RFC 6749 §6: refresh response may omit refresh_token — keep the old one if so
        if not new_token.refresh_token and token.refresh_token:
            new_token = OAuthToken(
                access_token=new_token.access_token,
                token_type=new_token.token_type,
                expires_in=new_token.expires_in,
                scope=new_token.scope,
                refresh_token=token.refresh_token,
            )

        await store.set_tokens(new_token)
        debug_logger.info("OAuth token refreshed for MCP server %s", server_id)
        return new_token

    async def disconnect(self, server_id: str) -> None:
        """Clear stored tokens and client info for a server."""
        await self.get_token_store(server_id).clear()
        debug_logger.info("OAuth disconnected for MCP server %s", server_id)
