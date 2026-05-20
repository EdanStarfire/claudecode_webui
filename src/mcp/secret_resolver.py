"""
secret_resolver — Issue #1484

Resolves ${secret:NAME} placeholders in MCP server config fields by reading
plaintext values directly from the vault. Used by SharedMcpConnectionManager
when opening upstream connections (the shared host-side path has no Docker
proxy sidecar, so substitution happens inline).
"""

import re

_SECRET_REF_RE = re.compile(r"\$\{secret:([^}]+)\}")


class SharedSecretResolutionError(ValueError):
    """Raised when a ${secret:...} reference cannot be resolved against the vault."""


async def resolve_secret_refs_in_str(value: str, vault) -> str:
    """Replace every ${secret:NAME} in `value` with the plaintext from the vault.

    All secret names are resolved in a single vault call. Raises
    SharedSecretResolutionError if any referenced name is missing.
    """
    if not value or "${secret:" not in value:
        return value
    # Collect unique names preserving insertion order, then batch-resolve once.
    names = list(dict.fromkeys(m.group(1).strip() for m in _SECRET_REF_RE.finditer(value)))
    resolved = await vault.resolve_secrets_for_assignment(names)
    resolved_map = {r["name"]: r["value"] for r in resolved}
    out = value
    for match in _SECRET_REF_RE.finditer(value):
        name = match.group(1).strip()
        if name not in resolved_map:
            raise SharedSecretResolutionError(
                f"Secret '{name}' referenced by a shared MCP config is "
                f"not present in the vault."
            )
        out = out.replace(match.group(0), resolved_map[name])
    return out


async def resolve_secret_refs_in_mapping(
    mapping: dict[str, str] | None, vault
) -> dict[str, str] | None:
    if not mapping:
        return mapping
    return {k: await resolve_secret_refs_in_str(v, vault) for k, v in mapping.items()}


async def resolve_secret_refs_in_list(
    items: list[str] | None, vault
) -> list[str] | None:
    if not items:
        return items
    return [await resolve_secret_refs_in_str(v, vault) for v in items]
