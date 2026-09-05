"""SSH key secret type handler (issue #1052).

SSH keys are delivered to agent containers via a tmpfs bind-mount at /run/ssh/.
The proxy inject/scrub pipeline is not involved — inject() and scrub() are no-ops.

validate() parses the PEM private key with the cryptography library to derive the
public key and fingerprint without storing sensitive material.

materialize() writes the private key bytes to a file with mode 0o600.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any

from .base import SecretTypeHandler


class SshKeyValidation:
    """Result of a successful SSH private key parse."""

    def __init__(self, public_key_openssh: str, fingerprint_sha256: str, key_type: str) -> None:
        self.public_key_openssh = public_key_openssh
        self.fingerprint_sha256 = fingerprint_sha256
        self.key_type = key_type


class SshKeyValidationError(ValueError):
    """Raised when SSH key validation fails (passphrase-protected, malformed, etc.)."""


class SshKeyHandler(SecretTypeHandler):
    """Handler for the ssh_key secret type.

    SSH key material is delivered to agent containers via a tmpfs bind-mount;
    the HTTP proxy pipeline does not inject or scrub it.
    """

    def inject(self, flow: Any, record: dict, placeholder: str) -> bool:
        return False

    def scrub(self, flow: Any, record: dict, placeholder: str) -> tuple[bool, str | None]:
        return False, None

    @staticmethod
    def validate(secret_value: str) -> SshKeyValidation:
        """Parse an OpenSSH private key PEM and return derived public-key metadata.

        Raises SshKeyValidationError if the key is passphrase-protected or malformed.
        The cryptography package is required; it ships as a transitive dependency.
        """
        try:
            from cryptography.exceptions import UnsupportedAlgorithm
            from cryptography.hazmat.primitives.serialization import (
                Encoding,
                PublicFormat,
                load_ssh_private_key,
            )
        except ImportError as exc:
            raise SshKeyValidationError(f"cryptography package required: {exc}") from exc

        try:
            private_key = load_ssh_private_key(secret_value.encode(), password=None)
        except TypeError as exc:
            raise SshKeyValidationError(
                "Passphrase-protected SSH keys are not supported. "
                "Provide an unencrypted private key."
            ) from exc
        except (ValueError, UnsupportedAlgorithm) as exc:
            raise SshKeyValidationError(f"Invalid or unsupported SSH private key: {exc}") from exc
        except Exception as exc:
            raise SshKeyValidationError(f"Failed to parse SSH private key: {exc}") from exc

        public_key = private_key.public_key()
        public_key_bytes = public_key.public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
        public_key_openssh = public_key_bytes.decode()

        # Fingerprint: SHA256 over the raw wire-format public key bytes (matches ssh-keygen -lf)
        parts = public_key_openssh.split()
        if len(parts) < 2:
            raise SshKeyValidationError("Could not extract public key data for fingerprint computation")
        raw_key_bytes = base64.b64decode(parts[1])
        digest = hashlib.sha256(raw_key_bytes).digest()
        fingerprint = "SHA256:" + base64.b64encode(digest).rstrip(b"=").decode()

        key_type = parts[0]  # e.g. "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"

        return SshKeyValidation(
            public_key_openssh=public_key_openssh,
            fingerprint_sha256=fingerprint,
            key_type=key_type,
        )

    @staticmethod
    def materialize(secret_value: str, target_dir: Path, name: str = "ed25519") -> Path:
        """Write the private key to target_dir/id_<name> with mode 0o600.

        Returns the path to the written file.
        The caller is responsible for creating target_dir before calling this method.
        """
        key_path = target_dir / f"id_{name}"
        content = secret_value if secret_value.endswith("\n") else secret_value + "\n"
        key_path.write_text(content)
        os.chmod(key_path, 0o600)
        return key_path
