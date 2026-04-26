"""
Unit tests for SshKeyHandler (issue #1052).

Tests cover:
- validate() accepts ed25519, rsa, ecdsa keys
- validate() rejects passphrase-protected keys
- validate() rejects malformed PEM
- validate() returns correct fingerprint (SHA256:...)
- materialize() writes file with mode 0o600
"""

import base64
import os
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.secret_types.ssh_key import SshKeyHandler, SshKeyValidation, SshKeyValidationError

# ---------------------------------------------------------------------------
# Fixture: generate test keys with ssh-keygen
# ---------------------------------------------------------------------------

def _generate_key(key_type: str, extra_args: list[str] | None = None) -> str:
    """Generate an unencrypted OpenSSH private key and return its PEM content."""
    with tempfile.TemporaryDirectory() as td:
        key_path = Path(td) / "test_key"
        cmd = ["ssh-keygen", "-t", key_type, "-N", "", "-f", str(key_path), "-q"]
        if extra_args:
            cmd.extend(extra_args)
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            pytest.skip(f"ssh-keygen failed for {key_type}: {result.stderr.decode()}")
        return key_path.read_text()


def _generate_ed25519_key() -> str:
    return _generate_key("ed25519")


def _generate_rsa_key() -> str:
    return _generate_key("rsa", ["-b", "2048"])


def _generate_ecdsa_key() -> str:
    return _generate_key("ecdsa", ["-b", "256"])


def _generate_passphrase_key() -> str:
    """Generate a passphrase-protected ed25519 key."""
    with tempfile.TemporaryDirectory() as td:
        key_path = Path(td) / "test_key"
        result = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "secret123", "-f", str(key_path), "-q"],
            capture_output=True,
        )
        if result.returncode != 0:
            pytest.skip(f"ssh-keygen failed: {result.stderr.decode()}")
        return key_path.read_text()


# ---------------------------------------------------------------------------
# Tests: validate()
# ---------------------------------------------------------------------------


class TestSshKeyValidate:
    def test_validate_ed25519_returns_validation(self):
        pem = _generate_ed25519_key()
        result = SshKeyHandler.validate(pem)
        assert isinstance(result, SshKeyValidation)
        assert result.public_key_openssh.startswith("ssh-ed25519 ")
        assert result.fingerprint_sha256.startswith("SHA256:")
        assert result.key_type == "ssh-ed25519"

    def test_validate_rsa_returns_validation(self):
        pem = _generate_rsa_key()
        result = SshKeyHandler.validate(pem)
        assert isinstance(result, SshKeyValidation)
        assert result.public_key_openssh.startswith("ssh-rsa ")
        assert result.fingerprint_sha256.startswith("SHA256:")
        assert result.key_type == "ssh-rsa"

    def test_validate_ecdsa_returns_validation(self):
        pem = _generate_ecdsa_key()
        result = SshKeyHandler.validate(pem)
        assert isinstance(result, SshKeyValidation)
        assert result.public_key_openssh.startswith("ecdsa-sha2-nistp256 ")
        assert result.fingerprint_sha256.startswith("SHA256:")
        assert result.key_type == "ecdsa-sha2-nistp256"

    def test_validate_passphrase_protected_raises(self):
        pem = _generate_passphrase_key()
        with pytest.raises(SshKeyValidationError, match="[Pp]assphrase"):
            SshKeyHandler.validate(pem)

    def test_validate_malformed_pem_raises(self):
        with pytest.raises(SshKeyValidationError):
            SshKeyHandler.validate("not a valid PEM key at all")

    def test_validate_empty_string_raises(self):
        with pytest.raises(SshKeyValidationError):
            SshKeyHandler.validate("")

    def test_validate_fingerprint_format(self):
        """Fingerprint must be SHA256:<base64> without trailing '='."""
        pem = _generate_ed25519_key()
        result = SshKeyHandler.validate(pem)
        _, encoded = result.fingerprint_sha256.split(":", 1)
        # base64 without padding: length must be a multiple of 4 after re-padding
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(padded)
        assert len(decoded) == 32  # SHA256 digest is 32 bytes

    def test_validate_fingerprint_matches_ssh_keygen(self):
        """Fingerprint computed by validate() must match ssh-keygen -lf output."""
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "id_ed25519"
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path), "-q"],
                check=True, capture_output=True,
            )
            pem = key_path.read_text()
            result = SshKeyHandler.validate(pem)

            # ssh-keygen -lf prints: "<bits> SHA256:<fp> comment (type)"
            proc = subprocess.run(
                ["ssh-keygen", "-lf", str(key_path)],
                capture_output=True, text=True,
            )
            keygen_output = proc.stdout.strip()
            assert result.fingerprint_sha256 in keygen_output, (
                f"Fingerprint mismatch: {result.fingerprint_sha256!r} not in {keygen_output!r}"
            )


# ---------------------------------------------------------------------------
# Tests: materialize()
# ---------------------------------------------------------------------------


class TestSshKeyMaterialize:
    def test_materialize_creates_file(self, tmp_path):
        pem = _generate_ed25519_key()
        result_path = SshKeyHandler.materialize(pem, tmp_path, name="ed25519")
        assert result_path == tmp_path / "id_ed25519"
        assert result_path.exists()

    def test_materialize_file_permissions(self, tmp_path):
        pem = _generate_ed25519_key()
        result_path = SshKeyHandler.materialize(pem, tmp_path)
        file_stat = os.stat(result_path)
        permissions = stat.S_IMODE(file_stat.st_mode)
        assert permissions == 0o600, f"Expected 0o600, got {oct(permissions)}"

    def test_materialize_content_matches(self, tmp_path):
        pem = _generate_ed25519_key()
        result_path = SshKeyHandler.materialize(pem, tmp_path, name="ed25519")
        written = result_path.read_text()
        # Content must end with newline
        assert written.endswith("\n")
        assert written.strip() == pem.strip()

    def test_materialize_default_name(self, tmp_path):
        pem = _generate_ed25519_key()
        result_path = SshKeyHandler.materialize(pem, tmp_path)
        assert result_path.name == "id_ed25519"

    def test_materialize_custom_name(self, tmp_path):
        pem = _generate_rsa_key()
        result_path = SshKeyHandler.materialize(pem, tmp_path, name="rsa")
        assert result_path.name == "id_rsa"


# ---------------------------------------------------------------------------
# Tests: inject/scrub no-ops
# ---------------------------------------------------------------------------


class TestSshKeyNoOps:
    def test_inject_returns_false(self):
        handler = SshKeyHandler()
        assert handler.inject(object(), {}, "placeholder") is False

    def test_scrub_returns_false_none(self):
        handler = SshKeyHandler()
        modified, captured = handler.scrub(object(), {}, "placeholder")
        assert modified is False
        assert captured is None
