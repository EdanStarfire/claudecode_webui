"""Unit tests for src/mcp/secret_resolver.py (issue #1484 §4.1b)."""

from unittest.mock import MagicMock

import pytest

from src.mcp.secret_resolver import (
    SharedSecretResolutionError,
    resolve_secret_refs_in_list,
    resolve_secret_refs_in_mapping,
    resolve_secret_refs_in_str,
)


def _vault(secret_map: dict[str, str]) -> MagicMock:
    """Return a fake vault whose resolve_secrets_for_assignment uses secret_map."""
    vault = MagicMock()

    async def resolve(names: list[str]) -> list[dict]:
        results = []
        for name in names:
            if name in secret_map:
                results.append({"name": name, "value": secret_map[name]})
        return results

    vault.resolve_secrets_for_assignment = resolve
    return vault


# ---------------------------------------------------------------------------
# resolve_secret_refs_in_str
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_str_with_no_refs_passthrough():
    vault = _vault({})
    result = await resolve_secret_refs_in_str("plain-value", vault)
    assert result == "plain-value"


@pytest.mark.asyncio
async def test_resolve_str_empty_passthrough():
    vault = _vault({})
    assert await resolve_secret_refs_in_str("", vault) == ""


@pytest.mark.asyncio
async def test_resolve_str_with_one_ref():
    vault = _vault({"MY_KEY": "sk-real"})
    result = await resolve_secret_refs_in_str("Bearer ${secret:MY_KEY}", vault)
    assert result == "Bearer sk-real"


@pytest.mark.asyncio
async def test_resolve_str_with_multiple_refs_same_value():
    vault = _vault({"TOKEN": "abc123"})
    result = await resolve_secret_refs_in_str(
        "${secret:TOKEN}-${secret:TOKEN}", vault
    )
    assert result == "abc123-abc123"


@pytest.mark.asyncio
async def test_missing_secret_raises():
    vault = _vault({})
    with pytest.raises(SharedSecretResolutionError, match="GHOST"):
        await resolve_secret_refs_in_str("${secret:GHOST}", vault)


# ---------------------------------------------------------------------------
# resolve_secret_refs_in_mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_mapping_resolves_every_value():
    vault = _vault({"K1": "v1", "K2": "v2"})
    result = await resolve_secret_refs_in_mapping(
        {"h1": "${secret:K1}", "h2": "${secret:K2}"}, vault
    )
    assert result == {"h1": "v1", "h2": "v2"}


@pytest.mark.asyncio
async def test_resolve_mapping_none_returns_none():
    vault = _vault({})
    assert await resolve_secret_refs_in_mapping(None, vault) is None


@pytest.mark.asyncio
async def test_resolve_mapping_empty_returns_empty():
    vault = _vault({})
    assert await resolve_secret_refs_in_mapping({}, vault) == {}


# ---------------------------------------------------------------------------
# resolve_secret_refs_in_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_list_resolves_every_item():
    vault = _vault({"A": "alpha", "B": "beta"})
    result = await resolve_secret_refs_in_list(
        ["${secret:A}", "no-ref", "${secret:B}"], vault
    )
    assert result == ["alpha", "no-ref", "beta"]


@pytest.mark.asyncio
async def test_resolve_list_none_returns_none():
    vault = _vault({})
    assert await resolve_secret_refs_in_list(None, vault) is None


@pytest.mark.asyncio
async def test_resolve_list_empty_returns_empty():
    vault = _vault({})
    assert await resolve_secret_refs_in_list([], vault) == []
