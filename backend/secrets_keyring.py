"""
Keyring backend configuration for secrets storage.

Wraps the `keyring` library with a consistent interface and provides
automatic fallback to an encrypted file (CryptFileKeyring) when the
native OS keyring is unavailable.

Service name: "cc_webui"
Username: secret slug (name)

Issue #827: Host-level secrets storage via keyring.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_SERVICE_NAME = "cc_webui"
_active_backend_name: str = "uninitialized"
_backend_warning: str | None = None


def configure_keyring(service_name: str = _SERVICE_NAME) -> str:
    """Configure keyring backend at application startup.

    Probes the native OS keyring (SecretService / Keychain / Credential Manager).
    Falls back to CryptFileKeyring on headless Linux if native backend is unavailable.
    Uses CC_WEBUI_KEYRING_PASSWORD environment variable to unlock the cryptfile backend.

    Returns the active backend class name for logging/display.
    """
    global _active_backend_name, _backend_warning

    import keyring
    import keyring.backends

    # Probe native backend with a round-trip write/read/delete
    try:
        backend = keyring.get_keyring()
        if getattr(backend, "priority", 0) < 1.0:
            raise RuntimeError(f"native backend priority too low: {getattr(backend, 'priority', 0)}")
        # Round-trip probe to confirm the backend actually works
        _probe_key = "__cc_webui_probe__"
        keyring.set_password(service_name, _probe_key, "ok")
        result = keyring.get_password(service_name, _probe_key)
        if result != "ok":
            raise RuntimeError("keyring probe read-back mismatch")
        keyring.delete_password(service_name, _probe_key)
        _active_backend_name = type(backend).__name__
        logger.info(f"Using native keyring backend: {_active_backend_name}")
        return _active_backend_name
    except Exception as e:
        logger.warning(f"Native keyring backend unavailable ({e}), falling back to CryptFileKeyring")

    # Fall back to encrypted file
    try:
        from keyrings.cryptfile.cryptfile import CryptFileKeyring

        cf = CryptFileKeyring()
        cf.file_path = str(Path("~/.config/cc_webui/keyring.enc").expanduser())
        password = os.environ.get("CC_WEBUI_KEYRING_PASSWORD")
        if password is None:
            _backend_warning = (
                "Keyring using encrypted file backend but CC_WEBUI_KEYRING_PASSWORD is not set. "
                "Secrets will not be readable until the environment variable is configured and "
                "the server is restarted."
            )
            logger.warning(_backend_warning)
        else:
            cf.keyring_key = password

        import keyring as _kr
        _kr.set_keyring(cf)
        _active_backend_name = "CryptFileKeyring"
        logger.info("Using CryptFileKeyring fallback backend")
    except Exception as exc:
        _active_backend_name = "FailedFallback"
        _backend_warning = f"All keyring backends failed: {exc}. Secret storage will not work."
        logger.error(_backend_warning)

    return _active_backend_name


def set_secret_value(name: str, value: str, service_name: str = _SERVICE_NAME) -> None:
    """Store a secret value in the keyring under (service_name, name)."""
    import keyring
    keyring.set_password(service_name, name, value)


def get_secret_value(name: str, service_name: str = _SERVICE_NAME) -> str | None:
    """Retrieve a secret value from the keyring. Returns None if not found."""
    import keyring
    try:
        return keyring.get_password(service_name, name)
    except Exception as e:
        logger.warning(f"Failed to retrieve secret '{name}' from keyring: {e}")
        return None


def delete_secret_value(name: str, service_name: str = _SERVICE_NAME) -> bool:
    """Delete a secret from the keyring. Returns True if deleted, False if not found."""
    import keyring
    import keyring.errors
    try:
        keyring.delete_password(service_name, name)
        return True
    except keyring.errors.PasswordDeleteError:
        return False
    except Exception as e:
        logger.warning(f"Failed to delete secret '{name}' from keyring: {e}")
        return False


def get_active_backend_name() -> str:
    """Return the name of the currently active keyring backend."""
    return _active_backend_name


def get_backend_status() -> dict:
    """Return backend status info for display in the UI."""
    return {
        "backend": _active_backend_name,
        "warning": _backend_warning,
        "is_cryptfile": _active_backend_name == "CryptFileKeyring",
        "password_set": os.environ.get("CC_WEBUI_KEYRING_PASSWORD") is not None,
    }
