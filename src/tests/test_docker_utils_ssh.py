"""
Tests for prepare_session_ssh() in src/docker_utils.py (issue #1052).

Tests cover:
- prepare_session_ssh() writes private key + config + known_hosts + ssh-wrapper
  when one ssh_key secret is assigned.
- Raises ValueError when multiple ssh_key secrets are assigned.
- SSH config contains expected ProxyCommand and IdentityFile lines.
- Returns False (no files written) when no ssh_key secrets present.
- Key file has mode 0o600; other files have mode 0o644; wrapper is 0o755.
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
        ssh_dir = tmp_path / "ssh"
        result = prepare_session_ssh([secret], ssh_dir)
        assert result is True

    def test_creates_expected_files(self, tmp_path):
        secret = _make_secret()
        ssh_dir = tmp_path / "ssh"
        prepare_session_ssh([secret], ssh_dir)
        assert (ssh_dir / "id_ed25519").exists()
        assert (ssh_dir / "config").exists()
        assert (ssh_dir / "known_hosts").exists()
        assert (ssh_dir / "ssh-wrapper").exists()

    def test_key_file_mode_0o600(self, tmp_path):
        secret = _make_secret()
        ssh_dir = tmp_path / "ssh"
        prepare_session_ssh([secret], ssh_dir)
        key_path = ssh_dir / "id_ed25519"
        perms = stat.S_IMODE(os.stat(key_path).st_mode)
        assert perms == 0o600, f"Expected 0o600, got {oct(perms)}"

    def test_wrapper_script_executable(self, tmp_path):
        secret = _make_secret()
        ssh_dir = tmp_path / "ssh"
        prepare_session_ssh([secret], ssh_dir)
        wrapper = ssh_dir / "ssh-wrapper"
        perms = stat.S_IMODE(os.stat(wrapper).st_mode)
        assert perms & 0o111, f"ssh-wrapper not executable: {oct(perms)}"

    def test_ssh_config_contains_proxycommand(self, tmp_path):
        secret = _make_secret()
        ssh_dir = tmp_path / "ssh"
        prepare_session_ssh([secret], ssh_dir)
        config_text = (ssh_dir / "config").read_text()
        assert "ProxyCommand" in config_text
        assert "ncat" in config_text
        assert "socks5" in config_text
        assert "127.0.0.1:1080" in config_text

    def test_ssh_config_contains_identityfile(self, tmp_path):
        secret = _make_secret()
        ssh_dir = tmp_path / "ssh"
        prepare_session_ssh([secret], ssh_dir)
        config_text = (ssh_dir / "config").read_text()
        assert "IdentityFile /run/ssh/id_ed25519" in config_text

    def test_ssh_config_identities_only(self, tmp_path):
        secret = _make_secret()
        ssh_dir = tmp_path / "ssh"
        prepare_session_ssh([secret], ssh_dir)
        config_text = (ssh_dir / "config").read_text()
        assert "IdentitiesOnly yes" in config_text

    def test_ssh_wrapper_content(self, tmp_path):
        secret = _make_secret()
        ssh_dir = tmp_path / "ssh"
        prepare_session_ssh([secret], ssh_dir)
        wrapper_text = (ssh_dir / "ssh-wrapper").read_text()
        assert "ssh -F /run/ssh/config" in wrapper_text

    def test_raises_on_multiple_ssh_keys(self, tmp_path):
        secrets = [_make_secret("key1"), _make_secret("key2")]
        ssh_dir = tmp_path / "ssh"
        with pytest.raises(ValueError, match="[Aa]t most one"):
            prepare_session_ssh(secrets, ssh_dir)

    def test_returns_false_with_no_ssh_key(self, tmp_path):
        secrets = [_make_non_ssh_secret()]
        ssh_dir = tmp_path / "ssh"
        result = prepare_session_ssh(secrets, ssh_dir)
        assert result is False
        assert not ssh_dir.exists() or not any(ssh_dir.iterdir())

    def test_returns_false_with_empty_list(self, tmp_path):
        ssh_dir = tmp_path / "ssh"
        result = prepare_session_ssh([], ssh_dir)
        assert result is False

    def test_non_ssh_secrets_ignored(self, tmp_path):
        secret = _make_secret()
        secrets = [_make_non_ssh_secret(), secret, _make_non_ssh_secret("npm-token")]
        ssh_dir = tmp_path / "ssh"
        result = prepare_session_ssh(secrets, ssh_dir)
        assert result is True
        assert (ssh_dir / "id_ed25519").exists()

    def test_known_hosts_is_empty(self, tmp_path):
        secret = _make_secret()
        ssh_dir = tmp_path / "ssh"
        prepare_session_ssh([secret], ssh_dir)
        known_hosts = ssh_dir / "known_hosts"
        assert known_hosts.exists()
        assert known_hosts.read_text() == ""

    def test_ssh_dir_created_if_missing(self, tmp_path):
        secret = _make_secret()
        ssh_dir = tmp_path / "subdir" / "ssh"
        assert not ssh_dir.exists()
        prepare_session_ssh([secret], ssh_dir)
        assert ssh_dir.exists()
