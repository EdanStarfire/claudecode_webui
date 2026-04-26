"""
Secrets Vault — secure CRUD for named secrets.

Stores secret metadata as JSON files:
  data/credentials/{name}.json    — metadata (mode 0o600), never contains the value

Secret values are stored in the OS keyring (or CryptFileKeyring fallback)
under service="cc_webui", username=name. Old plaintext .secret files are
orphaned (no migration); operators may delete them manually.

Issue #827: Host-level secrets storage via keyring (replaces issue #1053 plaintext storage).
"""

import json
import logging
import os
from pathlib import Path

from .models.secret_record import SecretRecord, validate_secret_name
from .secrets_keyring import delete_secret_value, get_secret_value, set_secret_value

logger = logging.getLogger(__name__)


# Backward-compat alias: the old class was CredentialVault
CredentialVault = None  # set at end of module


class SecretsVault:
    """Secure CRUD for named secrets using OS keyring for value storage.

    Secret values are stored in the keyring and never written to disk.
    Metadata JSON files contain all fields except the value.
    The old plaintext .secret files are ignored by this class.
    """

    def __init__(self, data_dir: Path) -> None:
        self._creds_dir = data_dir / "credentials"
        self._creds_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Public API (safe — no secrets in return values)
    # -------------------------------------------------------------------------

    async def list_secrets(self) -> list[dict]:
        """Return metadata for all secrets. Never includes secret values."""
        results = []
        for meta_path in sorted(self._creds_dir.glob("*.json")):
            try:
                data = json.loads(meta_path.read_text())
                # Skip files that look like old-format credentials (no 'type' field)
                # They will be picked up once re-created via the new API.
                if "name" not in data:
                    continue
                results.append(self._strip_value(data))
            except Exception:
                logger.exception(f"Failed to read secret metadata from {meta_path}")
        return results

    async def get_secret(self, name: str) -> dict | None:
        """Return metadata for a single secret. Returns None if not found."""
        meta_path = self._creds_dir / f"{name}.json"
        if not meta_path.exists():
            return None
        try:
            data = json.loads(meta_path.read_text())
            return self._strip_value(data)
        except Exception:
            logger.exception(f"Failed to read secret metadata for '{name}'")
            return None

    async def create_secret(self, record: SecretRecord, value: str) -> dict:
        """Create a new secret. Returns metadata only (no value).

        Raises:
            ValueError: If a secret with this name already exists, or validation fails.
        """
        validate_secret_name(record.name)
        record.validate()

        meta_path = self._creds_dir / f"{record.name}.json"
        if meta_path.exists():
            raise ValueError(f"Secret '{record.name}' already exists")

        # Store value in keyring first; if that fails, don't create the metadata
        set_secret_value(record.name, value)

        metadata = record.to_dict()
        meta_path.write_text(json.dumps(metadata, indent=2))
        os.chmod(meta_path, 0o600)

        logger.info(f"Created secret '{record.name}' (type={record.type})")
        return self._strip_value(metadata)

    async def update_secret(
        self,
        name: str,
        record: SecretRecord,
        value: str | None = None,
    ) -> dict | None:
        """Update secret metadata and optionally the value.

        Returns updated metadata, or None if secret not found.
        Raises ValueError on validation failure.
        """
        meta_path = self._creds_dir / f"{name}.json"
        if not meta_path.exists():
            return None

        record.validate()

        if value is not None:
            set_secret_value(name, value)

        metadata = record.to_dict()
        meta_path.write_text(json.dumps(metadata, indent=2))
        os.chmod(meta_path, 0o600)

        logger.info(f"Updated secret '{name}'")
        return self._strip_value(metadata)

    async def delete_secret(self, name: str) -> bool:
        """Delete secret metadata and keyring entry. Returns True if deleted, False if not found."""
        meta_path = self._creds_dir / f"{name}.json"
        if not meta_path.exists():
            return False

        meta_path.unlink(missing_ok=True)
        delete_secret_value(name)
        logger.info(f"Deleted secret '{name}'")
        return True

    # -------------------------------------------------------------------------
    # Internal API (reads values — never expose via HTTP)
    # -------------------------------------------------------------------------

    async def resolve_secrets_for_assignment(self, names: list[str]) -> list[dict]:
        """Resolve a list of secret names to full objects including values.

        Used internally to build sidecar credential bundles at session start.
        Returned dicts include a 'value' key with the plaintext secret.
        Unknown names are logged and skipped.
        """
        results = []
        for name in names:
            meta_path = self._creds_dir / f"{name}.json"
            if not meta_path.exists():
                logger.warning(f"Secret '{name}' not found in vault, skipping")
                continue
            try:
                metadata = json.loads(meta_path.read_text())
                value = get_secret_value(name)
                if value is None:
                    logger.warning(f"Keyring value missing for secret '{name}', skipping")
                    continue
                full = dict(metadata)
                full["value"] = value
                results.append(full)
            except Exception:
                logger.exception(f"Failed to resolve secret '{name}'")
        return results

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _strip_value(data: dict) -> dict:
        """Return a copy of data with value fields removed (defensive)."""
        return {k: v for k, v in data.items() if k not in ("value", "real_value")}


# Backward-compat alias: old code imported CredentialVault
CredentialVault = SecretsVault
