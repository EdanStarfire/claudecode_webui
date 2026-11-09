"""RelayRecord: a registered external-messaging workspace (issue #2103).

Only vault secret NAMES are stored; token values live in the credential vault
and are never part of this record or any API response.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class RelayRecord:
    relay_id: str
    name: str
    adapter_type: str = "slack"
    workspace_id: str | None = None
    workspace_name: str | None = None
    user_token_secret: str | None = None  # vault secret NAME
    app_token_secret: str | None = None  # vault secret NAME
    oauth_client_id: str | None = None
    oauth_client_secret_secret: str | None = None  # vault secret NAME
    owner_user_id: str | None = None
    self_dm_channel_id: str | None = None  # owner's self-DM (D…); not secret
    inbound_enabled: bool = True  # receive owner self-DM instructions (Socket Mode)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        now = _now()
        self.created_at = self.created_at or now
        self.updated_at = self.updated_at or now

    def to_dict(self) -> dict[str, Any]:
        return {
            "relay_id": self.relay_id,
            "name": self.name,
            "adapter_type": self.adapter_type,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "user_token_secret": self.user_token_secret,
            "app_token_secret": self.app_token_secret,
            "oauth_client_id": self.oauth_client_id,
            "oauth_client_secret_secret": self.oauth_client_secret_secret,
            "owner_user_id": self.owner_user_id,
            "self_dm_channel_id": self.self_dm_channel_id,
            "inbound_enabled": self.inbound_enabled,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def to_public_dict(self) -> dict[str, Any]:
        """API shape: secret names are replaced by presence booleans."""
        data = self.to_dict()
        for key in ("user_token_secret", "app_token_secret", "oauth_client_secret_secret"):
            data.pop(key)
        data["has_user_token"] = bool(self.user_token_secret)
        data["has_app_token"] = bool(self.app_token_secret)
        data["has_oauth_client_secret"] = bool(self.oauth_client_secret_secret)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelayRecord:
        return cls(
            relay_id=data["relay_id"],
            name=data["name"],
            adapter_type=data.get("adapter_type", "slack"),
            workspace_id=data.get("workspace_id"),
            workspace_name=data.get("workspace_name"),
            user_token_secret=data.get("user_token_secret"),
            app_token_secret=data.get("app_token_secret"),
            oauth_client_id=data.get("oauth_client_id"),
            oauth_client_secret_secret=data.get("oauth_client_secret_secret"),
            owner_user_id=data.get("owner_user_id"),
            self_dm_channel_id=data.get("self_dm_channel_id"),
            inbound_enabled=data.get("inbound_enabled", True) is not False,
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None,
        )
