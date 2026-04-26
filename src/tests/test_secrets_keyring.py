"""
Tests for secrets_keyring.py — issue #827.

Covers backend status and value CRUD.
keyring is imported inside each function in secrets_keyring.py, so we patch
at the `keyring` namespace level rather than patching the module attribute.
"""

from unittest.mock import patch

# ---------------------------------------------------------------------------
# Backend status
# ---------------------------------------------------------------------------


def test_get_backend_status_returns_dict():
    """get_backend_status() returns expected keys with correct types."""
    from src.secrets_keyring import get_backend_status

    status = get_backend_status()
    assert "backend" in status
    assert "warning" in status
    assert "is_cryptfile" in status
    assert "password_set" in status


def test_get_active_backend_name_returns_string():
    """get_active_backend_name() returns a string."""
    from src.secrets_keyring import get_active_backend_name

    name = get_active_backend_name()
    assert isinstance(name, str)


# ---------------------------------------------------------------------------
# CRUD helpers — patch keyring at the library level
# ---------------------------------------------------------------------------


def test_set_secret_value_calls_keyring():
    """set_secret_value delegates to keyring.set_password."""
    with patch("keyring.set_password") as mock_set:
        from src.secrets_keyring import set_secret_value

        set_secret_value("my_key", "my_secret", service_name="test_svc")
        mock_set.assert_called_once_with("test_svc", "my_key", "my_secret")


def test_get_secret_value_returns_value():
    """get_secret_value returns the value from keyring.get_password."""
    with patch("keyring.get_password", return_value="my_secret") as mock_get:
        from src.secrets_keyring import get_secret_value

        value = get_secret_value("my_key", service_name="test_svc")
        assert value == "my_secret"
        mock_get.assert_called_once_with("test_svc", "my_key")


def test_get_secret_value_returns_none_on_error():
    """get_secret_value returns None when keyring raises."""
    with patch("keyring.get_password", side_effect=Exception("no keyring")):
        from src.secrets_keyring import get_secret_value

        value = get_secret_value("my_key")
        assert value is None


def test_delete_secret_value_returns_true_when_deleted():
    """delete_secret_value returns True on successful deletion."""
    with patch("keyring.delete_password") as mock_del, patch("keyring.get_password", return_value="x"):
        from src.secrets_keyring import delete_secret_value

        result = delete_secret_value("my_key", service_name="test_svc")
        assert result is True
        mock_del.assert_called_once_with("test_svc", "my_key")


def test_delete_secret_value_returns_false_on_password_delete_error():
    """delete_secret_value returns False when PasswordDeleteError is raised."""
    import keyring.errors

    with patch("keyring.delete_password", side_effect=keyring.errors.PasswordDeleteError("not found")):
        from src.secrets_keyring import delete_secret_value

        result = delete_secret_value("nonexistent")
        assert result is False
