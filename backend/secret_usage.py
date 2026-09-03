"""
secret_usage — Issue #1772

Computes per-secret usage counts across sessions, templates, profiles, MCP
server configs, and OAuth2 refresh dependencies. Pure, synchronous, no I/O —
callers are responsible for fetching the input collections.
"""

from __future__ import annotations

from typing import Any

from .mcp.secret_resolver import find_secret_ref_names
from .slug_utils import slugify_secret


def empty_usage() -> dict[str, Any]:
    """Return a fresh zeroed usage breakdown."""
    return {
        "sessions": 0,
        "templates": 0,
        "profiles": 0,
        "mcp_servers": 0,
        "oauth2_dependents": [],
        "total": 0,
    }


def _assigned_secret_names(value: object) -> list[str]:
    """Normalize `assigned_secrets` into a list of names.

    Profile/template config is stored as a raw dict with no per-key type
    enforcement, and this field is known to sometimes hold a comma-separated
    string rather than a list (see `_LIST_FIELDS`/`_coerce_list` in
    config_resolution.py) — without this, a string value would be iterated
    character by character.
    """
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return list(value)


def _count_assigned_secrets(usage: dict[str, dict], items: list, bucket: str) -> None:
    for item in items:
        for name in _assigned_secret_names(item.config.get("assigned_secrets")):
            slug = slugify_secret(name)
            if slug in usage:
                usage[slug][bucket] += 1


def compute_secret_usage(
    secrets: list[dict],
    sessions: list,
    templates: list,
    profiles: list,
    mcp_configs: list,
) -> dict[str, dict]:
    """Compute a per-secret-name usage breakdown.

    `secrets` is the vault metadata list (dicts with "name"/"type"/"refresh").
    `sessions`/`templates`/`profiles` are objects exposing a `.config` dict
    (profiles are expected to already be filtered to the area that carries
    `assigned_secrets`). `mcp_configs` are objects exposing `.env`, `.headers`,
    `.url`, `.command`, `.args`.

    Returns {slugify_secret(name): {sessions, templates, profiles, mcp_servers,
    oauth2_dependents, total}}.
    """
    usage = {slugify_secret(s["name"]): empty_usage() for s in secrets}

    _count_assigned_secrets(usage, sessions, "sessions")
    _count_assigned_secrets(usage, templates, "templates")
    _count_assigned_secrets(usage, profiles, "profiles")

    for mcp_config in mcp_configs:
        ref_names = set()
        for value in mcp_config.env.values():
            ref_names.update(find_secret_ref_names(value))
        for value in mcp_config.headers.values():
            ref_names.update(find_secret_ref_names(value))
        ref_names.update(find_secret_ref_names(mcp_config.url))
        ref_names.update(find_secret_ref_names(mcp_config.command))
        for arg in mcp_config.args:
            ref_names.update(find_secret_ref_names(arg))
        for name in ref_names:
            slug = slugify_secret(name)
            if slug in usage:
                usage[slug]["mcp_servers"] += 1

    for secret in secrets:
        if secret.get("type") != "oauth2":
            continue
        refresh = secret.get("refresh") or {}
        dep_names = {
            refresh.get("refresh_token_secret_name"),
            refresh.get("client_secret_secret_name"),
        }
        dep_names.discard(None)
        for dep_name in dep_names:
            slug = slugify_secret(dep_name)
            if slug in usage:
                usage[slug]["oauth2_dependents"].append(secret["name"])

    for entry in usage.values():
        entry["total"] = (
            entry["sessions"]
            + entry["templates"]
            + entry["profiles"]
            + entry["mcp_servers"]
            + len(entry["oauth2_dependents"])
        )

    return usage
