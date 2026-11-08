"""Config-field validation for the relay attachment (issue #2103).

Called at every tier's write path (profile, template, session) so a bad value is
rejected where it is entered rather than at session start.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .adapters import get_spec
from .footer import validate_footer_template
from .safety import validate_handle

RELAY_CONFIG_FIELDS = frozenset({
    "relay_id", "relay_footer_enabled", "relay_footer_template",
    "relay_channel_policies", "relay_handle",
})

_relay_manager = None  # registered by the coordinator builder; resolves relay_id -> adapter


def register_relay_manager(manager) -> None:
    """Let write-path validation resolve a relay_id to its adapter's policy model."""
    global _relay_manager
    _relay_manager = manager


def validate_relay_policies(policies: object, relay_manager=None) -> dict | None:
    """Validate ``{relay_id: {target: policy}}`` (new shape only; flat input is rejected).

    Each relay's adapter policy model validates its targets and values; an unknown relay_id is
    rejected here (write path) although it is tolerated when read. Without a registered relay
    manager only the structure is checked. Raises ValueError.
    """
    if policies is None:
        return None
    shape_error = (
        "relay_channel_policies must be an object of relay_id -> {target: policy}"
    )
    if not isinstance(policies, Mapping):
        raise ValueError(shape_error)
    manager = relay_manager or _relay_manager
    for relay_id, entries in policies.items():
        if not isinstance(relay_id, str) or not isinstance(entries, Mapping):
            raise ValueError(shape_error)
        if manager is None:
            continue
        record = manager.relays.get(relay_id)
        spec = get_spec(getattr(record, "adapter_type", None))
        if spec is None:
            raise ValueError(f"Unknown relay '{relay_id}' in relay_channel_policies")
        for target, value in entries.items():
            spec.policy_model.validate_target(target)
            spec.policy_model.validate_value(value)
    return {k: dict(v) for k, v in policies.items()}


# Vault secret names with this prefix belong to RelayManager and are never assignable.
RELAY_SECRET_PREFIX = "relay-"


def is_relay_reserved_secret(name: str) -> bool:
    """True for secrets created by the relay itself (reserved ``relay-`` prefix).

    Secrets the owner created and merely REFERENCES as relay credentials are ordinary secrets: they
    may also be assigned to sessions (the UI warns about the tradeoff).
    """
    return name.strip().lower().startswith(RELAY_SECRET_PREFIX)


def validate_assigned_secrets(names: Iterable[str] | None) -> None:
    """Reject relay-created (``relay-`` prefixed) vault secrets in an ``assigned_secrets`` list."""
    for name in names or []:
        if isinstance(name, str) and is_relay_reserved_secret(name):
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
    validate_relay_policies(config.get("relay_channel_policies"))
    if config.get("relay_handle") is not None:
        if tier != "session":
            raise ValueError("relay_handle can only be set on a session")
        validate_handle(config["relay_handle"])
