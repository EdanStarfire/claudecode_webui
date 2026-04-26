"""
SecretRecord — data model for host-level secrets storage.

Stores metadata for a named secret. The actual secret value lives in the
OS keyring (or CryptFileKeyring fallback) under service="cc_webui", username=name.
The JSON metadata file never contains the value.

Issue #827: Host-level secrets storage via keyring.
Issue #1134: Typed secrets — proxy injection, scrubbing, OAuth refresh.
Issue #1052: ssh_key type — tmpfs key delivery, SOCKS5 tunnel.
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
    SSH = "ssh"        # legacy placeholder type (issue #1052: use ssh_key instead)
    SSH_KEY = "ssh_key"


class InjectFileFormat(str, Enum):
    YAML = "yaml"
    JSON = "json"
    TOML = "toml"
    RAW = "raw"


@dataclass
class InjectionSpec:
    """Configurable wire-side injection for api_key type."""
    location: str = "header"           # "header" | "query_param"
    header_name: str = "Authorization"
    prefix: str = "Bearer"             # prefix before value; may be empty string
    param_name: str | None = None      # required when location == "query_param"

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "header_name": self.header_name,
            "prefix": self.prefix,
            "param_name": self.param_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InjectionSpec:
        return cls(
            location=data.get("location", "header"),
            header_name=data.get("header_name", "Authorization"),
            prefix=data.get("prefix", "Bearer"),
            param_name=data.get("param_name"),
        )


@dataclass
class RefreshSpec:
    """OAuth2 refresh metadata for oauth2 access-token records."""
    token_url: str
    client_id: str
    refresh_token_secret_name: str             # ref to sibling record holding the refresh token
    client_secret_secret_name: str | None = None
    expires_at: datetime | None = None         # mutable; updated by proxy after each refresh
    buffer_seconds: int = 60                   # refresh this many seconds before expiry

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_url": self.token_url,
            "client_id": self.client_id,
            "refresh_token_secret_name": self.refresh_token_secret_name,
            "client_secret_secret_name": self.client_secret_secret_name,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "buffer_seconds": self.buffer_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RefreshSpec:
        expires_at = None
        if data.get("expires_at"):
            expires_at = datetime.fromisoformat(data["expires_at"])
        return cls(
            token_url=data["token_url"],
            client_id=data["client_id"],
            refresh_token_secret_name=data["refresh_token_secret_name"],
            client_secret_secret_name=data.get("client_secret_secret_name"),
            expires_at=expires_at,
            buffer_seconds=data.get("buffer_seconds", 60),
        )


@dataclass
class InjectFileSpec:
    """Specification for file-based secret injection."""
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
    # Issue #1134: Typed-secret fields
    username: str | None = None        # basic_auth only: plaintext username metadata
    injection: InjectionSpec | None = None   # api_key only
    refresh: RefreshSpec | None = None       # oauth2 only
    # Issue #1052: SSH key derived metadata (set by vault after key validation, never user-supplied)
    public_key_openssh: str | None = None    # ssh_key only: OpenSSH public key string
    fingerprint_sha256: str | None = None    # ssh_key only: SHA256 fingerprint (SHA256:...)
    key_type: str | None = None              # ssh_key only: key algorithm (ssh-ed25519, etc.)

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
        # Type-specific field constraints
        if self.injection is not None and self.type != SecretType.API_KEY:
            raise ValueError("injection may only be set when type == api_key")
        if self.injection is not None and self.injection.location == "query_param":
            if not self.injection.param_name:
                raise ValueError("injection.param_name is required when location == query_param")
        if self.refresh is not None and self.type != SecretType.OAUTH2:
            raise ValueError("refresh may only be set when type == oauth2")
        if self.type == SecretType.OAUTH2 and self.scrub is None:
            raise ValueError("scrub is required for oauth2 type (needed to capture refreshed token values)")
        if self.username is not None and self.type != SecretType.BASIC_AUTH:
            raise ValueError("username may only be set when type == basic_auth")
        if self.public_key_openssh is not None and self.type != SecretType.SSH_KEY:
            raise ValueError("public_key_openssh may only be set when type == ssh_key")
        if self.fingerprint_sha256 is not None and self.type != SecretType.SSH_KEY:
            raise ValueError("fingerprint_sha256 may only be set when type == ssh_key")
        if self.key_type is not None and self.type != SecretType.SSH_KEY:
            raise ValueError("key_type may only be set when type == ssh_key")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type.value if isinstance(self.type, SecretType) else self.type,
            "target_hosts": self.target_hosts,
            "inject_env": self.inject_env,
            "inject_file": self.inject_file.to_dict() if self.inject_file else None,
            "scrub": self.scrub.to_dict() if self.scrub else None,
            "username": self.username,
            "injection": self.injection.to_dict() if self.injection else None,
            "refresh": self.refresh.to_dict() if self.refresh else None,
            "public_key_openssh": self.public_key_openssh,
            "fingerprint_sha256": self.fingerprint_sha256,
            "key_type": self.key_type,
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
        injection = None
        if data.get("injection"):
            injection = InjectionSpec.from_dict(data["injection"])
        refresh = None
        if data.get("refresh"):
            refresh = RefreshSpec.from_dict(data["refresh"])
        return cls(
            name=data["name"],
            type=SecretType(data.get("type", "generic")),
            target_hosts=data.get("target_hosts", []),
            inject_env=data.get("inject_env"),
            inject_file=inject_file,
            scrub=scrub,
            username=data.get("username"),
            injection=injection,
            refresh=refresh,
            public_key_openssh=data.get("public_key_openssh"),
            fingerprint_sha256=data.get("fingerprint_sha256"),
            key_type=data.get("key_type"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )
