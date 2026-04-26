"""
SecretRecord — data model for host-level secrets storage.

Stores metadata for a named secret. The actual secret value lives in the
OS keyring (or CryptFileKeyring fallback) under service="cc_webui", username=name.
The JSON metadata file never contains the value.

Issue #827: Host-level secrets storage via keyring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class SecretType(str, Enum):
    API_KEY = "api_key"
    BEARER = "bearer"
    BASIC_AUTH = "basic_auth"
    OAUTH2 = "oauth2"
    GENERIC = "generic"
    SSH = "ssh"


class InjectFileFormat(str, Enum):
    YAML = "yaml"
    JSON = "json"
    TOML = "toml"
    RAW = "raw"


@dataclass
class InjectFileSpec:
    """Specification for file-based secret injection (shape only — behavior is #1134)."""
    path: str                          # Absolute path inside container
    format: InjectFileFormat           # yaml | json | toml | raw
    permissions: str = "0600"         # Octal permissions string
    key_path: str | None = None        # Dot-separated path (e.g. "github.com.oauth_token")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "format": self.format.value if isinstance(self.format, InjectFileFormat) else self.format,
            "permissions": self.permissions,
            "key_path": self.key_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InjectFileSpec:
        fmt = data.get("format", "raw")
        return cls(
            path=data["path"],
            format=InjectFileFormat(fmt),
            permissions=data.get("permissions", "0600"),
            key_path=data.get("key_path"),
        )


@dataclass
class ScrubSpec:
    """Specification for response scrubbing (shape only — behavior is #1134)."""
    url_path: str | None = None
    matcher_jsonpath: str | None = None
    matcher_regex: str | None = None
    update_on_change: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "url_path": self.url_path,
            "matcher_jsonpath": self.matcher_jsonpath,
            "matcher_regex": self.matcher_regex,
            "update_on_change": self.update_on_change,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScrubSpec:
        return cls(
            url_path=data.get("url_path"),
            matcher_jsonpath=data.get("matcher_jsonpath"),
            matcher_regex=data.get("matcher_regex"),
            update_on_change=data.get("update_on_change", False),
        )


# Validation patterns (shape validation only — no runtime behavior in #827)
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_HOST_RE = re.compile(r"^[*.a-z0-9-]+$")
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_PERM_RE = re.compile(r"^0?[0-7]{3,4}$")


def validate_secret_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Secret name '{name}' is invalid. "
            "Must match ^[a-z0-9][a-z0-9_-]{{0,62}}$"
        )


def validate_target_hosts(hosts: list[str]) -> None:
    for h in hosts:
        if not _HOST_RE.match(h):
            raise ValueError(
                f"Target host '{h}' is invalid. "
                "Must match ^[*.a-z0-9-]+$"
            )


def validate_inject_env(env: str) -> None:
    if not _ENV_RE.match(env):
        raise ValueError(
            f"inject_env '{env}' is invalid. "
            "Must match ^[A-Z_][A-Z0-9_]*$"
        )


def validate_inject_file(spec: InjectFileSpec) -> None:
    if not spec.path.startswith("/"):
        raise ValueError(f"inject_file.path must be an absolute path: {spec.path!r}")
    if not _PERM_RE.match(spec.permissions):
        raise ValueError(
            f"inject_file.permissions '{spec.permissions}' is invalid. "
            "Must match ^0?[0-7]{{3,4}}$"
        )


def validate_scrub(spec: ScrubSpec) -> None:
    has_jsonpath = bool(spec.matcher_jsonpath)
    has_regex = bool(spec.matcher_regex)
    if has_jsonpath and has_regex:
        raise ValueError("scrub must specify exactly one of matcher_jsonpath or matcher_regex, not both")
    if not has_jsonpath and not has_regex:
        raise ValueError("scrub must specify exactly one of matcher_jsonpath or matcher_regex")


@dataclass
class SecretRecord:
    """Metadata for a named secret. Value is stored separately in the keyring."""
    name: str
    type: SecretType
    target_hosts: list[str]
    created_at: datetime
    updated_at: datetime
    inject_env: str | None = None
    inject_file: InjectFileSpec | None = None
    scrub: ScrubSpec | None = None

    def validate(self) -> None:
        """Raise ValueError if any field fails validation."""
        validate_secret_name(self.name)
        validate_target_hosts(self.target_hosts)
        if self.inject_env:
            validate_inject_env(self.inject_env)
        if self.inject_file:
            validate_inject_file(self.inject_file)
        if self.scrub:
            validate_scrub(self.scrub)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type.value if isinstance(self.type, SecretType) else self.type,
            "target_hosts": self.target_hosts,
            "inject_env": self.inject_env,
            "inject_file": self.inject_file.to_dict() if self.inject_file else None,
            "scrub": self.scrub.to_dict() if self.scrub else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecretRecord:
        inject_file = None
        if data.get("inject_file"):
            inject_file = InjectFileSpec.from_dict(data["inject_file"])
        scrub = None
        if data.get("scrub"):
            scrub = ScrubSpec.from_dict(data["scrub"])
        return cls(
            name=data["name"],
            type=SecretType(data.get("type", "generic")),
            target_hosts=data.get("target_hosts", []),
            inject_env=data.get("inject_env"),
            inject_file=inject_file,
            scrub=scrub,
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )
