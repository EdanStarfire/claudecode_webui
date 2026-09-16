"""
Tests for timestamp_injection module (issue #1779).

Covers disabled/every_message/once_per_day frequency logic, timezone-correct
day-boundary comparison, and invalid-timezone fallback — all driven via an
explicit `now_utc` rather than real wall-clock time.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from backend.timestamp_injection import (
    format_injection_prefix,
    inject_timestamp,
    is_slash_command,
    local_date_str,
    maybe_inject_timestamp,
    resolve_timezone,
)


class TestResolveTimezone:
    def test_valid_iana_name(self):
        tz = resolve_timezone("America/New_York")
        assert tz.key == "America/New_York"

    def test_invalid_name_falls_back_to_utc(self):
        tz = resolve_timezone("Not/A_Real_Zone")
        assert tz.key == "UTC"

    def test_none_falls_back_to_utc(self):
        tz = resolve_timezone(None)
        assert tz.key == "UTC"

    def test_empty_string_falls_back_to_utc(self):
        tz = resolve_timezone("")
        assert tz.key == "UTC"


class TestFormatInjectionPrefix:
    def test_prefix_contains_date_time_and_zone(self):
        now = datetime(2026, 8, 18, 14, 32, tzinfo=UTC)
        prefix = format_injection_prefix(now, "UTC")
        assert prefix == "[Current time: 2026-08-18 14:32 UTC]"

    def test_prefix_uses_local_time_for_named_zone(self):
        now = datetime(2026, 8, 18, 18, 32, tzinfo=UTC)
        prefix = format_injection_prefix(now, "America/New_York")
        # EDT is UTC-4 in August
        assert prefix == "[Current time: 2026-08-18 14:32 America/New_York]"

    def test_invalid_zone_displays_utc(self):
        now = datetime(2026, 8, 18, 14, 32, tzinfo=UTC)
        prefix = format_injection_prefix(now, "Bogus/Zone")
        assert prefix == "[Current time: 2026-08-18 14:32 UTC]"


class TestInjectTimestamp:
    def test_prepends_prefix_with_blank_line(self):
        now = datetime(2026, 8, 18, 14, 32, tzinfo=UTC)
        result = inject_timestamp("Hello there", now, "UTC")
        assert result == "[Current time: 2026-08-18 14:32 UTC]\n\nHello there"


class TestLocalDateStr:
    def test_utc_date(self):
        now = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        assert local_date_str(now, "UTC") == "2026-08-18"

    def test_day_boundary_crosses_backward_in_negative_offset_zone(self):
        # 02:00 UTC is still the previous day in America/New_York (UTC-4 in August)
        now = datetime(2026, 8, 18, 2, 0, tzinfo=UTC)
        assert local_date_str(now, "America/New_York") == "2026-08-17"

    def test_day_boundary_crosses_forward_in_positive_offset_zone(self):
        # 23:00 UTC is already the next day in Asia/Tokyo (UTC+9)
        now = datetime(2026, 8, 18, 23, 0, tzinfo=UTC)
        assert local_date_str(now, "Asia/Tokyo") == "2026-08-19"


class TestIsSlashCommand:
    def test_bare_slash_command(self):
        assert is_slash_command("/usage") is True

    def test_leading_whitespace_slash_command(self):
        assert is_slash_command("  /usage") is True

    def test_plain_message_is_not_a_command(self):
        assert is_slash_command("hello there") is False

    def test_empty_string_is_not_a_command(self):
        assert is_slash_command("") is False


class TestMaybeInjectTimestamp:
    def test_disabled_passes_through_unchanged(self):
        now = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        content, new_date = maybe_inject_timestamp(
            "hi", enabled=False, frequency="every_message", tz_name="UTC",
            last_injection_date=None, now_utc=now,
        )
        assert content == "hi"
        assert new_date is None

    def test_every_message_always_injects(self):
        now = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        content, new_date = maybe_inject_timestamp(
            "hi", enabled=True, frequency="every_message", tz_name="UTC",
            last_injection_date="2026-08-18", now_utc=now,
        )
        assert content.startswith("[Current time:")
        assert content.endswith("hi")
        # every_message never tracks a date — nothing to persist
        assert new_date is None

    def test_once_per_day_first_message_injects(self):
        now = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        content, new_date = maybe_inject_timestamp(
            "hi", enabled=True, frequency="once_per_day", tz_name="UTC",
            last_injection_date=None, now_utc=now,
        )
        assert content.startswith("[Current time:")
        assert new_date == "2026-08-18"

    def test_once_per_day_same_day_second_message_passes_through(self):
        now = datetime(2026, 8, 18, 18, 0, tzinfo=UTC)
        content, new_date = maybe_inject_timestamp(
            "hi again", enabled=True, frequency="once_per_day", tz_name="UTC",
            last_injection_date="2026-08-18", now_utc=now,
        )
        assert content == "hi again"
        assert new_date is None

    def test_once_per_day_next_day_injects_again(self):
        now = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
        content, new_date = maybe_inject_timestamp(
            "morning", enabled=True, frequency="once_per_day", tz_name="UTC",
            last_injection_date="2026-08-18", now_utc=now,
        )
        assert content.startswith("[Current time:")
        assert new_date == "2026-08-19"

    def test_invalid_timezone_falls_back_without_raising(self):
        now = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        content, new_date = maybe_inject_timestamp(
            "hi", enabled=True, frequency="every_message", tz_name="Not/Real",
            last_injection_date=None, now_utc=now,
        )
        assert "UTC" in content
        assert new_date is None

    def test_slash_command_bypasses_injection_every_message(self):
        now = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        content, new_date = maybe_inject_timestamp(
            "/usage", enabled=True, frequency="every_message", tz_name="UTC",
            last_injection_date=None, now_utc=now,
        )
        assert content == "/usage"
        assert new_date is None

    def test_slash_command_bypasses_injection_once_per_day_without_consuming_day(self):
        now = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        content, new_date = maybe_inject_timestamp(
            "/compact", enabled=True, frequency="once_per_day", tz_name="UTC",
            last_injection_date=None, now_utc=now,
        )
        assert content == "/compact"
        # A skipped slash command must not report a date to persist — otherwise
        # it would silently consume once_per_day's daily injection slot.
        assert new_date is None

    def test_regular_message_after_skipped_slash_command_still_injects_same_day(self):
        now = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)
        slash_content, slash_date = maybe_inject_timestamp(
            "/clear", enabled=True, frequency="once_per_day", tz_name="UTC",
            last_injection_date=None, now_utc=now,
        )
        assert slash_content == "/clear"
        assert slash_date is None

        # Caller never persisted a date from the skipped slash command, so
        # last_injection_date is still None for the next real message today.
        later = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)
        content, new_date = maybe_inject_timestamp(
            "real message", enabled=True, frequency="once_per_day", tz_name="UTC",
            last_injection_date=slash_date, now_utc=later,
        )
        assert content.startswith("[Current time:")
        assert content.endswith("real message")
        assert new_date == "2026-08-18"


def test_zoneinfo_available_for_all_offsets_used_in_tests():
    # Sanity check the test environment has tzdata installed for the zones exercised above.
    for name in ("America/New_York", "Asia/Tokyo", "UTC"):
        ZoneInfo(name)
