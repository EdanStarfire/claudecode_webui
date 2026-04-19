"""
Credential Vault — secure CRUD for named proxy credentials.

Stores credential metadata and secret values as separate files:
  data/credentials/{slug}.json    — metadata (mode 0o600), never contains the secret
  data/credentials/{slug}.secret  — secret value (mode 0o600)

Issue #1053: Domain allowlist and credential management UI for proxy mode.
"""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from .slug_utils import slugify

logger = logging.getLogger(__name__)


class CredentialVault:
    """Secure CRUD for named proxy credentials.

    Credential values are stored in separate .secret files and never returned
    via list_credentials() or create_credential() — only the internal
    resolve_credentials() method reads them (for sidecar assembly at session start).
    """

    def __init__(self, data_dir: Path) -> None:
        self._creds_dir = data_dir / "credentials"
        self._creds_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Public API (safe — no secrets in return values)
    # -------------------------------------------------------------------------

    async def list_credentials(self) -> list[dict]:
        """Return metadata for all credentials. Never includes secret values."""
        results = []
        for meta_path in sorted(self._creds_dir.glob("*.json")):
            try:
                data = json.loads(meta_path.read_text())
                results.append(self._strip_secret(data))
            except Exception:
                logger.exception(f"Failed to read credential metadata from {meta_path}")
        return results

    async def create_credential(
        self,
        name: str,
        host_pattern: str,
        header_name: str,
        value_format: str,
        real_value: str,
        delivery: dict,
    ) -> dict:
        """Create a new credential. Returns metadata only (no value).

        Raises:
            ValueError: If a credential with this name already exists.
        """
        slug = slugify(name)
        meta_path = self._creds_dir / f"{slug}.json"
        secret_path = self._creds_dir / f"{slug}.secret"

        if meta_path.exists():
            raise ValueError(f"Credential '{name}' already exists (slug: {slug})")

        now = datetime.now(UTC).isoformat()
        metadata = {
            "name": name,
            "host_pattern": host_pattern,
            "header_name": header_name,
            "value_format": value_format,
            "delivery": delivery,
            "created_at": now,
            "updated_at": now,
        }

        # Write secret first so that if anything fails, there's no orphaned metadata
        secret_path.write_text(real_value)
        os.chmod(secret_path, 0o600)

        meta_path.write_text(json.dumps(metadata, indent=2))
        os.chmod(meta_path, 0o600)

        logger.info(f"Created credential '{name}' (slug={slug})")
        return self._strip_secret(metadata)

    async def delete_credential(self, name: str) -> bool:
        """Delete both metadata and secret files. Returns True if deleted, False if not found."""
        slug = slugify(name)
        meta_path = self._creds_dir / f"{slug}.json"
        secret_path = self._creds_dir / f"{slug}.secret"

        if not meta_path.exists():
            return False

        meta_path.unlink(missing_ok=True)
        secret_path.unlink(missing_ok=True)
        logger.info(f"Deleted credential '{name}' (slug={slug})")
        return True

    # -------------------------------------------------------------------------
    # Internal API (reads secrets — never expose via HTTP)
    # -------------------------------------------------------------------------

    async def get_credential_value(self, name: str) -> str | None:
        """Read the secret value for a named credential. Internal use only."""
        slug = slugify(name)
        secret_path = self._creds_dir / f"{slug}.secret"
        if not secret_path.exists():
            return None
        return secret_path.read_text()

    async def resolve_credentials(self, names: list[str]) -> list[dict]:
        """Resolve a list of credential names to full objects (including values).

        Used internally during sidecar assembly at session start.
        Returns full credential dicts with 'real_value' field populated.
        Unknown names are logged and skipped.
        """
        results = []
        for name in names:
            slug = slugify(name)
            meta_path = self._creds_dir / f"{slug}.json"
            if not meta_path.exists():
                logger.warning(f"Credential '{name}' not found in vault (slug={slug}), skipping")
                continue
            try:
                metadata = json.loads(meta_path.read_text())
                real_value = await self.get_credential_value(name)
                if real_value is None:
                    logger.warning(f"Secret file missing for credential '{name}', skipping")
                    continue
                full = dict(metadata)
                full["real_value"] = real_value
                results.append(full)
            except Exception:
                logger.exception(f"Failed to resolve credential '{name}'")
        return results

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _strip_secret(data: dict) -> dict:
        """Return a copy of data with 'real_value' removed (defensive)."""
        return {k: v for k, v in data.items() if k != "real_value"}
