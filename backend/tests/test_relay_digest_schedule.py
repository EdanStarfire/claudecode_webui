"""Digest schedule parsing, normalization and next-run computation (issue #2103 stage 3c-i). Pure."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.relay.inbound import schedule as sched


def utc(y, mo, d, h=0, mi=0) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp()


def parse(raw, tz="Europe/Paris", now=1_800_000_000.0):
    return sched.parse_schedule(raw, owner_tz=tz, now=now)


def daily(times, days=None, tz="UTC"):
    return {"type": "daily", "times": times, "days": days or list(range(7)), "tz": tz,
            "tz_source": "explicit"}


class TestParsing:
    def test_daily_normalized(self):
        s = parse({"daily": {"times": ["17:00", "09:00"], "days": ["Fri", "mon", 2]}})
        assert s == {"type": "daily", "times": ["09:00", "17:00"], "days": [0, 2, 4],
                     "tz": "Europe/Paris", "tz_source": "slack"}

    def test_defaults_every_day_and_explicit_timezone(self):
        s = parse({"daily": {"times": ["09:00"], "timezone": "America/New_York"}})
        assert s["days"] == list(range(7)) and s["tz"] == "America/New_York"
        assert s["tz_source"] == "explicit"

    def test_utc_fallback_when_slack_tz_unknown_or_bad(self):
        assert parse({"daily": {"times": ["09:00"]}}, tz=None)["tz_source"] == "utc_fallback"
        bad = parse({"daily": {"times": ["09:00"]}}, tz="Not/AZone")
        assert (bad["tz"], bad["tz_source"]) == ("UTC", "utc_fallback")

    def test_interval(self):
        assert parse({"every_minutes": 120}, now=5.0) == {"type": "interval", "minutes": 120, "anchor": 5.0}

    @pytest.mark.parametrize("raw", [
        None, {}, [], "daily", {"daily": {"times": ["09:00"]}, "every_minutes": 60},
        {"every_minutes": 29}, {"every_minutes": 1441}, {"every_minutes": "60"}, {"every_minutes": True},
        {"every_minutes": 60.5}, {"cron": "* * * * *"},
        {"daily": {}}, {"daily": {"times": []}}, {"daily": {"times": ["9:00"]}},
        {"daily": {"times": ["24:00"]}}, {"daily": {"times": ["09:00", "09:00"]}},
        {"daily": {"times": ["01:00", "02:00", "03:00", "04:00", "05:00"]}},
        {"daily": {"times": ["09:00"], "days": []}}, {"daily": {"times": ["09:00"], "days": ["xyz"]}},
        {"daily": {"times": ["09:00"], "days": [7]}}, {"daily": {"times": ["09:00"], "days": [True]}},
        {"daily": {"times": ["09:00"], "timezone": "Mars/Base"}},
        {"daily": {"times": ["09:00"], "timezone": "../etc"}},
        {"daily": {"times": ["09:00"], "extra": 1}},
    ])
    def test_invalid(self, raw):
        with pytest.raises(sched.ScheduleError) as e:
            parse(raw)
        assert e.value.code == "invalid_schedule" and e.value.message

    def test_min_interval_message(self):
        with pytest.raises(sched.ScheduleError, match="at least 30"):
            parse({"every_minutes": 5})

    def test_identity_ignores_anchor_and_tz_source(self):
        a = parse({"every_minutes": 60}, now=1.0)
        b = parse({"every_minutes": 60}, now=999.0)
        assert sched.identity(a) == sched.identity(b)
        c = parse({"daily": {"times": ["09:00"], "timezone": "Europe/Paris"}})
        d = parse({"daily": {"times": ["09:00"]}})  # slack tz = Europe/Paris
        assert sched.identity(c) == sched.identity(d)
        assert sched.identity(c) != sched.identity(parse({"daily": {"times": ["10:00"]}}))
        assert sched.identity(None) is None


class TestDescribe:
    def test_words_with_tz_source(self):
        s = parse({"daily": {"times": ["09:00", "17:00"], "days": [0, 1, 2, 3, 4]}})
        assert sched.describe(s) == "daily at 09:00 and 17:00, Mon–Fri, Europe/Paris (your Slack timezone)"
        fb = parse({"daily": {"times": ["09:00"]}}, tz=None)
        assert sched.describe(fb) == "daily at 09:00, UTC (Slack did not report your timezone)"
        ex = parse({"daily": {"times": ["09:00"], "days": ["mon", "wed"], "timezone": "Asia/Kolkata"}})
        assert sched.describe(ex) == "daily at 09:00, Mon, Wed, Asia/Kolkata"
        assert sched.describe(parse({"every_minutes": 120})) == "every 2 hours"
        assert sched.describe(parse({"every_minutes": 90})) == "every 90 minutes"
        assert sched.describe(parse({"every_minutes": 60})) == "every 1 hour"

    def test_describe_raw_never_raises(self):
        assert sched.describe_raw("junk") == "a schedule"
        assert "your Slack timezone" in sched.describe_raw({"daily": {"times": ["09:00"]}})

    def test_tz_info(self):
        assert sched.tz_info(parse({"daily": {"times": ["09:00"]}})) == {
            "timezone": "Europe/Paris", "timezone_source": "slack"}
        assert sched.tz_info(parse({"every_minutes": 60})) is None


class TestNextRun:
    def test_daily_days_filter_and_multiple_times(self):
        s = daily(["09:00", "17:00"], days=[0, 1, 2, 3, 4])  # weekdays, UTC
        fri_noon = utc(2026, 11, 13, 12)  # a Friday
        assert sched.next_run_after(s, fri_noon) == utc(2026, 11, 13, 17)
        assert sched.next_run_after(s, utc(2026, 11, 13, 17)) == utc(2026, 11, 16, 9)  # strictly after; weekend skipped
        assert sched.next_run_after(s, utc(2026, 11, 13, 8)) == utc(2026, 11, 13, 9)

    def test_strictly_after_property(self):
        s = daily(["00:00", "12:00"])
        for k in range(0, 200):
            after = utc(2026, 1, 1) + k * 3607.0
            nxt = sched.next_run_after(s, after)
            assert nxt > after and nxt - after <= 12 * 3600

    def test_interval_anchor(self):
        s = {"type": "interval", "minutes": 120, "anchor": 1000.0}
        assert sched.next_run_after(s, 1000.0) == 1000.0 + 7200
        assert sched.next_run_after(s, 1000.0 + 7199) == 1000.0 + 7200
        assert sched.next_run_after(s, 1000.0 + 7200) == 1000.0 + 14400
        assert sched.next_run_after(s, 500.0) == 1000.0  # clock before the anchor

    def test_paris_spring_forward_gap_runs_at_first_valid_instant(self):
        s = daily(["02:30"], tz="Europe/Paris")
        # 2026-03-29 02:30 does not exist (02:00 -> 03:00): runs at 03:00 local = 01:00Z
        assert sched.next_run_after(s, utc(2026, 3, 28, 12)) == utc(2026, 3, 29, 1, 0)
        # the day after is a normal 02:30 CEST = 00:30Z
        assert sched.next_run_after(s, utc(2026, 3, 29, 1, 0)) == utc(2026, 3, 30, 0, 30)

    def test_paris_fall_back_runs_once_at_first_occurrence(self):
        s = daily(["02:30"], tz="Europe/Paris")
        first = sched.next_run_after(s, utc(2026, 10, 24, 12))
        assert first == utc(2026, 10, 25, 0, 30)  # 02:30 CEST (first occurrence)
        # the repeated 02:30 CET (01:30Z) is NOT a second run: the next one is the following day
        assert sched.next_run_after(s, first) == utc(2026, 10, 26, 1, 30)

    def test_new_york_transitions(self):
        s = daily(["02:30"], tz="America/New_York")
        assert sched.next_run_after(s, utc(2026, 3, 7, 12)) == utc(2026, 3, 8, 7, 0)  # gap -> 03:00 EDT
        s2 = daily(["01:30"], tz="America/New_York")
        first = sched.next_run_after(s2, utc(2026, 10, 31, 12))
        assert first == utc(2026, 11, 1, 5, 30)  # first 01:30 (EDT)
        assert sched.next_run_after(s2, first) == utc(2026, 11, 2, 6, 30)  # 01:30 EST next day

    def test_half_hour_zone(self):
        s = daily(["09:00"], tz="Asia/Kolkata")
        assert sched.next_run_after(s, utc(2026, 11, 11, 0)) == utc(2026, 11, 11, 3, 30)

    def test_utc_fallback_zone(self):
        s = parse({"daily": {"times": ["09:00"]}}, tz=None)
        assert sched.next_run_after(s, utc(2026, 11, 11, 10)) == utc(2026, 11, 12, 9)

    def test_local_iso(self):
        s = daily(["09:00"], tz="Europe/Paris")
        assert sched.local_iso(s, utc(2026, 11, 12, 8)) == "2026-11-12T09:00:00+01:00"


class TestJitter:
    def test_bounds_and_determinism(self):
        values = {sched.jitter(f"id-{i}") for i in range(300)}
        assert all(0 <= v <= 90 for v in values) and len(values) > 20
        assert sched.jitter("abc") == sched.jitter("abc")

    def test_slot_is_never_earlier_than_schedule(self):
        s = daily(["09:00"])
        after = utc(2026, 11, 11)
        assert sched.next_run_with_jitter(s, "m1", after) >= sched.next_run_after(s, after)
