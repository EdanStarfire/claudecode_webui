"""
Shared slug utilities.

Provides filesystem-safe slug conversion used by profile_manager,
template_manager, mcp_config_manager, and session_manager.
"""

import re


def slugify(name: str) -> str:
    """Convert a display name to a filesystem-safe underscore slug.

    "Coding Minion" -> "coding_minion"
    "Code Expert" -> "code_expert"
    """
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


def slugify_name(name: str) -> str:
    """Convert a display name to a hyphen slug for MCP tool compatibility.

    "Database Optimizer" -> "database-optimizer"
    "frontend-dev" -> "frontend-dev"  (already slugified, no change)
    """
    slug = name.lower()
    slug = slug.replace("_", "-").replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug


def slugify_secret(name: str) -> str:
    """Convert a secret name to a filesystem-safe slug preserving both '-' and '_'.

    Idempotent on names matching [a-z0-9_-] (the validate_secret_name character set).
    "github-token"     -> "github-token"  (unchanged)
    "claudecode_token" -> "claudecode_token"  (unchanged)
    "GITHUB-TOKEN"     -> "github-token"
    "My Secret!"       -> "my_secret_"
    ""                 -> ""
    """
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9_-]", "_", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = re.sub(r"_{2,}", "_", slug)
    slug = slug.strip("_-")
    return slug
