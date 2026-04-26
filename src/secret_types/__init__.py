"""Secret-type handler registry (issue #1134).

Usage:
    from src.secret_types import get_handler
    handler = get_handler("oauth2")
    handler.inject(flow, record, placeholder)
"""

from __future__ import annotations

from .api_key import ApiKeyHandler
from .base import SecretTypeHandler
from .basic_auth import BasicAuthHandler
from .bearer import BearerHandler
from .generic import GenericHandler
from .oauth2 import OAuth2Handler
from .ssh_key import SshKeyHandler

_HANDLERS: dict[str, SecretTypeHandler] = {
    "generic": GenericHandler(),
    "api_key": ApiKeyHandler(),
    "bearer": BearerHandler(),
    "basic_auth": BasicAuthHandler(),
    "oauth2": OAuth2Handler(),
    "ssh": GenericHandler(),      # legacy type — kept for backward compat with old records
    "ssh_key": SshKeyHandler(),   # issue #1052: SSH key via tmpfs bind-mount + SOCKS5 tunnel
}


def get_handler(secret_type: str) -> SecretTypeHandler:
    """Return the singleton handler for `secret_type`.

    Raises KeyError if the type is unknown.
    """
    handler = _HANDLERS.get(secret_type)
    if handler is None:
        raise KeyError(f"Unknown secret type: {secret_type!r}")
    return handler


__all__ = [
    "SecretTypeHandler",
    "GenericHandler",
    "ApiKeyHandler",
    "BearerHandler",
    "BasicAuthHandler",
    "OAuth2Handler",
    "SshKeyHandler",
    "get_handler",
]
