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

import logging
import secrets
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_client_registration_request,
    create_oauth_metadata_request,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    handle_registration_response,
    handle_token_response_scopes,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from mcp.shared.auth_utils import calculate_token_expiry

logger = logging.getLogger(__name__)


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
        self._tokens_dir.mkdir(parents=True, exist_ok=True)
        self._clients_dir.mkdir(parents=True, exist_ok=True)
        self._expiry_dir.mkdir(parents=True, exist_ok=True)
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
            logger.warning("Failed to decrypt token for MCP server %s", self._server_id)
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
            logger.warning("Failed to decrypt client info for MCP server %s", self._server_id)
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Encrypt and persist OAuthClientInformationFull for this server."""
        client_file = self._clients_dir / f"{self._server_id}.enc"
        encrypted = self._fernet.encrypt(client_info.model_dump_json().encode())
        client_file.write_bytes(encrypted)

    async def clear(self) -> None:
        """Delete all stored data (tokens + client info + expiry) for this server."""
        for f in (
            self._tokens_dir / f"{self._server_id}.enc",
            self._clients_dir / f"{self._server_id}.enc",
            self._expiry_dir / f"{self._server_id}.expiry",
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
        # code_verifier, redirect_uri}
        self._pending: dict[str, dict] = {}

    def get_token_store(self, server_id: str) -> FernetTokenStore:
        """Return a FernetTokenStore for the given server_id."""
        return FernetTokenStore(self._data_dir, server_id)

    async def get_stored_token(self, server_id: str) -> OAuthToken | None:
        """Return the persisted OAuthToken for a server, or None if not authenticated."""
        return await self.get_token_store(server_id).get_tokens()

    async def start_flow(
        self,
        server_id: str,
        server_url: str,
        redirect_uri: str,
        client_name: str = "Claude Code WebUI",
    ) -> str:
        """Initiate an OAuth 2.1 authorization code flow.

        Steps performed:
          1. Protected resource metadata discovery (RFC 9728)
          2. Authorization server metadata discovery (RFC 8414)
          3. Dynamic Client Registration (RFC 7591) — reuses stored client if present
          4. PKCE parameter generation
          5. Authorization URL construction
          6. Pending state storage (includes token_endpoint + client_id for complete_flow)

        Returns the authorization URL the browser should navigate to.
        """
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
                except Exception:
                    continue

            # --- Step 2: Authorization server metadata ---
            oauth_metadata = None
            for url in build_oauth_authorization_server_metadata_discovery_urls(
                auth_server_url, server_url
            ):
                req = create_oauth_metadata_request(url)
                try:
                    resp = await http.send(req)
                    ok, meta = await handle_auth_metadata_response(resp)
                    if meta:
                        oauth_metadata = meta
                        break
                    if not ok:
                        break
                except Exception:
                    continue

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

            # --- Step 3: Dynamic Client Registration ---
            store = self.get_token_store(server_id)
            client_info = await store.get_client_info()

            if not client_info:
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
                    logger.info("DCR succeeded for MCP server %s", server_id)
                except Exception as exc:
                    logger.warning(
                        "DCR failed for MCP server %s (%s), using redirect_uri as client_id",
                        server_id,
                        exc,
                    )
                    # Public client fallback: use redirect_uri as client_id
                    client_info = OAuthClientInformationFull(
                        client_id=redirect_uri,
                        redirect_uris=[redirect_uri],  # type: ignore[arg-type]
                        token_endpoint_auth_method="none",
                    )

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
            auth_url = f"{auth_endpoint}?{urlencode(params)}"

            # --- Step 6: Persist pending state ---
            self._pending[state] = {
                "server_id": server_id,
                "token_endpoint": token_endpoint,
                "client_id": client_id,
                "code_verifier": pkce.code_verifier,
                "redirect_uri": redirect_uri,
                "requested_scopes": requested_scopes,
            }

            logger.info(
                "OAuth flow started for MCP server %s (state=%s…)", server_id, state[:8]
            )
            return auth_url

    async def complete_flow(self, state: str, code: str) -> str:
        """Complete an OAuth flow by exchanging the authorization code for tokens.

        Reads token_endpoint and client_id from the pending state stored by
        start_flow() — these values are never recomputed here.

        Returns server_id on success; raises ValueError on failure.
        """
        pending = self._pending.pop(state, None)
        if not pending:
            raise ValueError(f"No pending OAuth flow for state={state!r}")

        server_id: str = pending["server_id"]
        token_endpoint: str = pending["token_endpoint"]
        client_id: str = pending["client_id"]
        code_verifier: str = pending["code_verifier"]
        redirect_uri: str = pending["redirect_uri"]
        requested_scopes: list[str] | None = pending.get("requested_scopes")

        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        if requested_scopes:
            token_data["scope"] = " ".join(requested_scopes)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        async with httpx.AsyncClient() as http:
            resp = await http.post(token_endpoint, data=token_data, headers=headers)
            if resp.status_code != 200:
                body = await resp.aread()
                raise ValueError(
                    f"Token exchange failed ({resp.status_code}): {body.decode()}"
                )
            token = await handle_token_response_scopes(resp)

        store = self.get_token_store(server_id)
        await store.set_tokens(token)
        logger.info("OAuth flow complete for MCP server %s", server_id)
        return server_id

    async def disconnect(self, server_id: str) -> None:
        """Clear stored tokens and client info for a server."""
        await self.get_token_store(server_id).clear()
        logger.info("OAuth disconnected for MCP server %s", server_id)
