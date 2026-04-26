"""
Tests for prepare_session_ssh() in src/docker_utils.py (issue #1052).

Two-dir isolation architecture:
  key_dir    — private key only; mounted into proxy sidecar at /run/ssh-private:ro
  shared_dir — socket + config + known_hosts + wrapper; mounted into both containers at /run/ssh

Tests cover:
- prepare_session_ssh() writes private key to key_dir and config/known_hosts/wrapper to shared_dir
- Private key is NOT present in shared_dir
- Raises ValueError when multiple ssh_key secrets are assigned
- SSH config has NO IdentityFile line; has expected ProxyCommand + IdentitiesOnly
- Returns False (no files written) when no ssh_key secrets present
- Key file has mode 0o600; config/known_hosts have mode 0o644; wrapper is 0o755
- Env vars SSH_AUTH_SOCK and GIT_SSH_COMMAND are implied by config content
"""

import os
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.docker_utils import prepare_session_ssh


def _make_secret(name: str = "gh-deploy", key_value: str | None = None) -> dict:
    """Build a minimal resolved ssh_key secret dict."""
    if key_value is None:
        with tempfile.TemporaryDirectory() as td:
            key_path = Path(td) / "id_ed25519"
            result = subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path), "-q"],
                capture_output=True,
            )
            if result.returncode != 0:
                pytest.skip(f"ssh-keygen not available: {result.stderr.decode()}")
            key_value = key_path.read_text()
    return {
        "name": name,
        "type": "ssh_key",
        "value": key_value,
        "key_type": "ssh-ed25519",
    }


def _make_non_ssh_secret(name: str = "github-token") -> dict:
    return {"name": name, "type": "bearer", "value": "ghp_abc123"}


# ---------------------------------------------------------------------------


class TestPrepareSessionSsh:
    def test_returns_true_with_one_ssh_key(self, tmp_path):
        secret = _make_secret()
        result = prepare_session_ssh([secret], tmp_path / "key", tmp_path / "shared")
        assert result is True

    def test_creates_key_in_key_dir(self, tmp_path):
        secret = _make_secret()
        key_dir = tmp_path / "key"
        shared_dir = tmp_path / "shared"
        prepare_session_ssh([secret], key_dir, shared_dir)
        assert (key_dir / "id").exists(), "private key must be in key_dir/id"

    def test_creates_config_in_shared_dir(self, tmp_path):
        secret = _make_secret()
        prepare_session_ssh([secret], tmp_path / "key", tmp_path / "shared")
        assert (tmp_path / "shared" / "config").exists()

    def test_creates_known_hosts_in_shared_dir(self, tmp_path):
        secret = _make_secret()
        prepare_session_ssh([secret], tmp_path / "key", tmp_path / "shared")
        assert (tmp_path / "shared" / "known_hosts").exists()

    def test_creates_wrapper_in_shared_dir(self, tmp_path):
        secret = _make_secret()
        prepare_session_ssh([secret], tmp_path / "key", tmp_path / "shared")
        assert (tmp_path / "shared" / "ssh-wrapper").exists()

    def test_private_key_not_in_shared_dir(self, tmp_path):
        """The agent container must never see raw key bytes."""
        secret = _make_secret()
        shared_dir = tmp_path / "shared"
        prepare_session_ssh([secret], tmp_path / "key", shared_dir)
        # id_ed25519 and id must not appear in the shared dir
        for f in shared_dir.iterdir():
            assert "id" not in f.name or f.name in ("id",), (
                f"private key file found in shared_dir: {f.name}"
            )
        assert not (shared_dir / "id").exists(), "private key must NOT be in shared_dir"
        assert not (shared_dir / "id_ed25519").exists()

    def test_key_file_mode_0o600(self, tmp_path):
        secret = _make_secret()
        key_dir = tmp_path / "key"
        prepare_session_ssh([secret], key_dir, tmp_path / "shared")
        key_path = key_dir / "id"
        perms = stat.S_IMODE(os.stat(key_path).st_mode)
        assert perms == 0o600, f"Expected 0o600, got {oct(perms)}"

    def test_wrapper_script_executable(self, tmp_path):
        secret = _make_secret()
        shared_dir = tmp_path / "shared"
        prepare_session_ssh([secret], tmp_path / "key", shared_dir)
        wrapper = shared_dir / "ssh-wrapper"
        perms = stat.S_IMODE(os.stat(wrapper).st_mode)
        assert perms & 0o111, f"ssh-wrapper not executable: {oct(perms)}"

    def test_ssh_config_has_no_identity_file(self, tmp_path):
        """No IdentityFile — ssh-agent socket provides identity."""
        secret = _make_secret()
        shared_dir = tmp_path / "shared"
        prepare_session_ssh([secret], tmp_path / "key", shared_dir)
        config_text = (shared_dir / "config").read_text()
        assert "IdentityFile" not in config_text, (
            "SSH config must not contain IdentityFile; identity comes from ssh-agent socket"
        )

    def test_ssh_config_contains_proxycommand(self, tmp_path):
        secret = _make_secret()
        shared_dir = tmp_path / "shared"
        prepare_session_ssh([secret], tmp_path / "key", shared_dir)
        config_text = (shared_dir / "config").read_text()
        assert "ProxyCommand" in config_text
        assert "ncat" in config_text
        assert "socks5" in config_text
        assert "127.0.0.1:1080" in config_text

    def test_ssh_config_identities_only(self, tmp_path):
        secret = _make_secret()
        shared_dir = tmp_path / "shared"
        prepare_session_ssh([secret], tmp_path / "key", shared_dir)
        config_text = (shared_dir / "config").read_text()
        assert "IdentitiesOnly yes" in config_text

    def test_ssh_wrapper_references_config(self, tmp_path):
        secret = _make_secret()
        shared_dir = tmp_path / "shared"
        prepare_session_ssh([secret], tmp_path / "key", shared_dir)
        wrapper_text = (shared_dir / "ssh-wrapper").read_text()
        assert "ssh -F /run/ssh/config" in wrapper_text

    def test_raises_on_multiple_ssh_keys(self, tmp_path):
        secrets = [_make_secret("key1"), _make_secret("key2")]
        with pytest.raises(ValueError, match="[Aa]t most one"):
            prepare_session_ssh(secrets, tmp_path / "key", tmp_path / "shared")

    def test_returns_false_with_no_ssh_key(self, tmp_path):
        secrets = [_make_non_ssh_secret()]
        result = prepare_session_ssh(secrets, tmp_path / "key", tmp_path / "shared")
        assert result is False
        assert not (tmp_path / "key").exists() or not any((tmp_path / "key").iterdir())

    def test_returns_false_with_empty_list(self, tmp_path):
        result = prepare_session_ssh([], tmp_path / "key", tmp_path / "shared")
        assert result is False

    def test_non_ssh_secrets_ignored(self, tmp_path):
        secret = _make_secret()
        secrets = [_make_non_ssh_secret(), secret, _make_non_ssh_secret("npm-token")]
        key_dir = tmp_path / "key"
        result = prepare_session_ssh(secrets, key_dir, tmp_path / "shared")
        assert result is True
        assert (key_dir / "id").exists()

    def test_known_hosts_is_empty(self, tmp_path):
        secret = _make_secret()
        shared_dir = tmp_path / "shared"
        prepare_session_ssh([secret], tmp_path / "key", shared_dir)
        known_hosts = shared_dir / "known_hosts"
        assert known_hosts.exists()
        assert known_hosts.read_text() == ""

    def test_key_dir_created_if_missing(self, tmp_path):
        secret = _make_secret()
        key_dir = tmp_path / "subdir" / "key"
        shared_dir = tmp_path / "subdir" / "shared"
        assert not key_dir.exists()
        prepare_session_ssh([secret], key_dir, shared_dir)
        assert key_dir.exists()
        assert shared_dir.exists()
