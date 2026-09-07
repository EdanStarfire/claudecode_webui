"""Unit tests for SecretRecord.validate() — issue #1867 stage 2 (oauth2-secret).

Covers the oauth2-type scrub/refresh validation gap: a standalone oauth2 secret
used only for proactive background refresh (no live proxied traffic to scrub)
should not be forced to invent an unused scrub matcher just to pass validation.
"""

from datetime import UTC, datetime

import pytest

from backend.models.secret_record import (
    RefreshSpec,
    ScrubSpec,
    SecretRecord,
    SecretType,
)


def _make_refresh() -> RefreshSpec:
    return RefreshSpec(
        token_url="https://example.com/token",
        client_id="client-id",
        refresh_token_secret_name="refresh-token-secret",
    )


def _make_scrub() -> ScrubSpec:
    return ScrubSpec(matcher_regex="access_token=([^&]+)")


def _make_record(**overrides) -> SecretRecord:
    now = datetime.now(UTC)
    defaults = {
        "name": "test-oauth2-secret",
        "type": SecretType.OAUTH2,
        "target_hosts": ["example.com"],
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return SecretRecord(**defaults)


def test_oauth2_refresh_only_no_scrub_validates():
    """A refresh-only oauth2 secret (no live traffic to scrub) is valid."""
    record = _make_record(refresh=_make_refresh(), scrub=None)
    record.validate()  # must not raise


def test_oauth2_neither_scrub_nor_refresh_raises():
    """oauth2 with neither scrub nor refresh still fails — nothing keeps the token current."""
    record = _make_record(refresh=None, scrub=None)
    with pytest.raises(ValueError, match="oauth2 type requires at least one of scrub"):
        record.validate()


def test_oauth2_scrub_only_still_validates():
    """Existing behavior preserved: scrub alone (no refresh) is still valid."""
    record = _make_record(refresh=None, scrub=_make_scrub())
    record.validate()  # must not raise


def test_oauth2_both_scrub_and_refresh_validates():
    """Both configured together remains valid."""
    record = _make_record(refresh=_make_refresh(), scrub=_make_scrub())
    record.validate()  # must not raise


def test_oauth2_empty_refresh_without_scrub_raises():
    """A present-but-empty refresh spec does not satisfy the requirement.

    RefreshSpec.from_dict() does not itself validate that fields are non-empty,
    so validate() must check the fields it actually needs (token_url,
    refresh_token_secret_name) rather than just refresh-is-not-None.
    """
    empty_refresh = RefreshSpec(token_url="", client_id="", refresh_token_secret_name="")
    record = _make_record(refresh=empty_refresh, scrub=None)
    with pytest.raises(ValueError, match="oauth2 type requires at least one of scrub"):
        record.validate()
