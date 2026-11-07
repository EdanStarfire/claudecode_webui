"""Config-field validation for the relay attachment (issue #2103).

Called at every tier's write path (profile, template, session) so a bad value is
rejected where it is entered rather than at session start.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .footer import validate_footer_template
from .policy import validate_channel_policies
from .safety import validate_handle

RELAY_CONFIG_FIELDS = frozenset({
    "relay_id", "relay_footer_enabled", "relay_footer_template",
    "relay_channel_policies", "relay_handle",
})

# Vault secret names with this prefix belong to RelayManager and are never assignable.
RELAY_SECRET_PREFIX = "relay-"


# Names of vault secrets the relay REFERENCES as credentials (user token, app token, OAuth client
# secret). Process-wide because the validators below are plain pydantic validators with no access
# to RelayManager; RelayManager keeps it in sync (one relay per Backend).
_relay_referenced_secrets: frozenset[str] = frozenset()


def _norm(name: str) -> str:
    return name.strip().lower()


def set_relay_referenced_secrets(names: Iterable[str | None]) -> None:
    global _relay_referenced_secrets
    _relay_referenced_secrets = frozenset(_norm(n) for n in names if n)


def is_relay_reserved_secret(name: str) -> bool:
    """True for relay-created secrets (reserved prefix) and any secret a relay references."""
    n = _norm(name)
    return n.startswith(RELAY_SECRET_PREFIX) or n in _relay_referenced_secrets


def validate_assigned_secrets(names: Iterable[str] | None) -> None:
    """Reject relay-reserved or relay-referenced vault secrets in an ``assigned_secrets`` list."""
    for name in names or []:
        if not isinstance(name, str):
            continue
        if _norm(name) in _relay_referenced_secrets:
            raise ValueError(
                f"Secret '{name}' is used by the relay as a credential and cannot be assigned to a "
                "session, template or profile"
            )
        if _norm(name).startswith(RELAY_SECRET_PREFIX):
            raise ValueError(
                f"Secret '{name}' is reserved for the relay and cannot be assigned to a session"
            )


def validate_relay_config(config: Mapping | None, tier: str) -> None:
    """Validate relay fields and ``assigned_secrets`` in a config dict. Raises ValueError.

    *tier* is ``profile``, ``template`` or ``session``; ``relay_handle`` is session-only.
    """
    if not config:
        return
    validate_assigned_secrets(config.get("assigned_secrets"))
    if config.get("relay_id") is not None and not isinstance(config["relay_id"], str):
        raise ValueError("relay_id must be a string")
    if config.get("relay_footer_enabled") is not None and not isinstance(
        config["relay_footer_enabled"], bool
    ):
        raise ValueError("relay_footer_enabled must be a boolean")
    validate_footer_template(config.get("relay_footer_template"))
    validate_channel_policies(config.get("relay_channel_policies"))
    if config.get("relay_handle") is not None:
        if tier != "session":
            raise ValueError("relay_handle can only be set on a session")
        validate_handle(config["relay_handle"])
