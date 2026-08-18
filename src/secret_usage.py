"""secret_usage — Issue #1772

Pure computation of per-secret usage counts across sessions, templates,
profiles, MCP server configs, and OAuth2 refresh dependencies. No I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .mcp.secret_resolver import find_secret_ref_names
from .slug_utils import slugify_secret

if TYPE_CHECKING:
    from .mcp_config_manager import McpServerConfig
    from .models.config_profile import ConfigProfile
    from .session_manager import SessionInfo
    from .template_manager import MinionTemplate

_MCP_TEXT_FIELDS = ("command", "url")
_MCP_MAPPING_FIELDS = ("env", "headers")
_MCP_LIST_FIELDS = ("args",)


def _empty_usage() -> dict[str, Any]:
    return {"sessions": 0, "templates": 0, "profiles": 0, "mcp_servers": 0, "oauth2_dependents": []}


def compute_secret_usage(
    secrets: list[dict],
    sessions: list[SessionInfo],
    templates: list[MinionTemplate],
    profiles: list[ConfigProfile],
    mcp_configs: list[McpServerConfig],
) -> dict[str, dict]:
    """Returns {slugified_secret_name: {sessions, templates, profiles, mcp_servers,
    oauth2_dependents, total}}.
    """
    usage = {slugify_secret(s["name"]): _empty_usage() for s in secrets}

    for session in sessions:
        names = {slugify_secret(n) for n in session.config.get("assigned_secrets") or []}
        for slug in names:
            entry = usage.get(slug)
            if entry is not None:
                entry["sessions"] += 1

    for template in templates:
        names = {slugify_secret(n) for n in template.config.get("assigned_secrets") or []}
        for slug in names:
            entry = usage.get(slug)
            if entry is not None:
                entry["templates"] += 1

    for profile in profiles:
        if profile.area != "isolation":
            continue
        names = {slugify_secret(n) for n in profile.config.get("assigned_secrets") or []}
        for slug in names:
            entry = usage.get(slug)
            if entry is not None:
                entry["profiles"] += 1

    for mcp_config in mcp_configs:
        names: set[str] = set()
        for field_name in _MCP_TEXT_FIELDS:
            names.update(find_secret_ref_names(getattr(mcp_config, field_name, None)))
        for field_name in _MCP_MAPPING_FIELDS:
            mapping = getattr(mcp_config, field_name, None) or {}
            for value in mapping.values():
                names.update(find_secret_ref_names(value))
        for field_name in _MCP_LIST_FIELDS:
            for value in getattr(mcp_config, field_name, None) or []:
                names.update(find_secret_ref_names(value))
        for name in names:
            entry = usage.get(slugify_secret(name))
            if entry is not None:
                entry["mcp_servers"] += 1

    for secret in secrets:
        if secret.get("type") != "oauth2":
            continue
        refresh = secret.get("refresh") or {}
        dep_names = {
            refresh.get("refresh_token_secret_name"),
            refresh.get("client_secret_secret_name"),
        }
        for dep_name in dep_names:
            if not dep_name:
                continue
            entry = usage.get(slugify_secret(dep_name))
            if entry is not None:
                entry["oauth2_dependents"].append(secret["name"])

    for entry in usage.values():
        entry["total"] = (
            entry["sessions"]
            + entry["templates"]
            + entry["profiles"]
            + entry["mcp_servers"]
            + len(entry["oauth2_dependents"])
        )

    return usage
