"""Digest schedules (issue #2103 stage 3c-i). Pure: no I/O, no clock (callers pass instants).

A schedule is one of two normalized dicts (JSON-ready, stored on the monitor record):

- ``{"type": "daily", "times": ["09:00", "17:00"], "days": [0..6], "tz": "Europe/Paris", "tz_source": ...}``
  (``days``: 0 = Monday; ``tz_source``: ``explicit`` | ``slack`` | ``utc_fallback``)
- ``{"type": "interval", "minutes": 120, "anchor": <epoch>}``

The raw (agent-supplied) form is ``{"daily": {"times": [...], "days"?: [...], "timezone"?: "..."}}`` or
``{"every_minutes": N}``: exactly one form, unknown keys rejected.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import constants as const

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)\Z")
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_LONG = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
TZ_EXPLICIT, TZ_SLACK, TZ_UTC_FALLBACK = "explicit", "slack", "utc_fallback"


class ScheduleError(Exception):
    """An unusable schedule; ``code`` is always ``invalid_schedule``."""

    code = "invalid_schedule"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def load_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError, TypeError):
        raise ScheduleError(f"Unknown timezone {str(name)[:60]!r}: use an IANA name like Europe/Paris.") from None


def _parse_day(value: Any) -> int:
    if isinstance(value, bool):
        raise ScheduleError("days must be names (mon..sun) or numbers 0-6 (0 = Monday).")
    if isinstance(value, int):
        if 0 <= value <= 6:
            return value
    elif isinstance(value, str):
        key = value.strip().lower()
        if key in DAY_NAMES:
            return DAY_NAMES.index(key)
        if key in _DAY_LONG:
            return _DAY_LONG.index(key)
    raise ScheduleError("days must be names (mon..sun) or numbers 0-6 (0 = Monday).")


def parse_schedule(
    raw: Any, *, owner_tz: str | None, now: float, min_minutes: int = const.DIGEST_MIN_INTERVAL_MINUTES,
) -> dict[str, Any]:
    """Validate and normalize *raw*. ``owner_tz`` is the owner's Slack timezone (None = unknown ->
    UTC fallback). Raises ``ScheduleError``."""
    if not isinstance(raw, dict) or not raw:
        raise ScheduleError("schedule must be an object: {\"daily\": {...}} or {\"every_minutes\": N}.")
    unknown = set(raw) - {"daily", "every_minutes"}
    if unknown:
        raise ScheduleError(f"Unknown schedule key(s): {', '.join(sorted(map(str, unknown)))[:80]}.")
    if len(raw) != 1:
        raise ScheduleError("Give exactly one schedule form: `daily` or `every_minutes`.")
    if "every_minutes" in raw:
        minutes = raw["every_minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            raise ScheduleError("every_minutes must be a whole number of minutes.")
        if minutes < min_minutes:
            raise ScheduleError(f"every_minutes must be at least {min_minutes}.")
        if minutes > 1440:
            raise ScheduleError("every_minutes must be at most 1440 (one day).")
        return {"type": "interval", "minutes": minutes, "anchor": float(now)}
    daily = raw["daily"]
    if not isinstance(daily, dict):
        raise ScheduleError("daily must be an object with `times` (and optional `days`, `timezone`).")
    unknown = set(daily) - {"times", "days", "timezone"}
    if unknown:
        raise ScheduleError(f"Unknown daily key(s): {', '.join(sorted(map(str, unknown)))[:80]}.")
    times_raw = daily.get("times")
    if not isinstance(times_raw, list) or not times_raw:
        raise ScheduleError("daily.times must be a list of 1-4 times like \"09:00\".")
    if not all(isinstance(t, str) and _TIME_RE.match(t.strip()) for t in times_raw):
        raise ScheduleError("daily.times entries must be 24-hour HH:MM strings like \"09:00\".")
    times = sorted({t.strip() for t in times_raw})
    if len(times) != len(times_raw):
        raise ScheduleError("daily.times must not repeat a time.")
    if len(times) > const.DIGEST_MAX_TIMES:
        raise ScheduleError(f"daily.times accepts at most {const.DIGEST_MAX_TIMES} times.")
    days_raw = daily.get("days")
    if days_raw is None:
        days = list(range(7))
    else:
        if not isinstance(days_raw, list) or not days_raw:
            raise ScheduleError("daily.days must be a non-empty list of days.")
        days = sorted({_parse_day(d) for d in days_raw})
    tz_name = daily.get("timezone")
    if tz_name is not None:
        if not isinstance(tz_name, str) or not tz_name.strip():
            raise ScheduleError("daily.timezone must be an IANA name like Europe/Paris.")
        tz, source = tz_name.strip(), TZ_EXPLICIT
        load_zone(tz)
    elif owner_tz:
        try:
            load_zone(owner_tz)
            tz, source = owner_tz, TZ_SLACK
        except ScheduleError:
            tz, source = "UTC", TZ_UTC_FALLBACK
    else:
        tz, source = "UTC", TZ_UTC_FALLBACK
    return {"type": "daily", "times": times, "days": days, "tz": tz, "tz_source": source}


def identity(schedule: dict[str, Any] | None) -> tuple | None:
    """What makes two schedules the same monitor: not the anchor, not where the tz came from."""
    if not schedule:
        return None
    if schedule.get("type") == "interval":
        return ("interval", schedule.get("minutes"))
    return ("daily", tuple(schedule.get("times") or ()), tuple(schedule.get("days") or ()), schedule.get("tz"))


def jitter(monitor_id: str) -> int:
    """Deterministic 0-90 s offset per monitor (spreads the Slack calls of simultaneous digests)."""
    return int(hashlib.sha256(monitor_id.encode()).hexdigest()[:8], 16) % (const.DIGEST_JITTER_MAX_SECONDS + 1)


def _local_to_utc(day: date, hh: int, mm: int, tz: ZoneInfo) -> float:
    """First valid instant for a wall-clock time: fall-back repeats use the first occurrence, a
    spring-forward gap runs at the first instant after the gap."""
    naive = datetime(day.year, day.month, day.day, hh, mm)
    first = naive.replace(tzinfo=tz, fold=0)
    utc = first.astimezone(UTC)
    if utc.astimezone(tz).replace(tzinfo=None) == naive:
        return utc.timestamp()
    # nonexistent local time: bisect for the earliest instant whose local time is >= naive
    lo = naive.replace(tzinfo=tz, fold=1).astimezone(UTC).timestamp()  # before the transition
    hi = utc.timestamp()  # after it
    if lo > hi:
        lo, hi = hi, lo
    while hi - lo > 1:
        mid = math.floor((lo + hi) / 2)
        local = datetime.fromtimestamp(mid, tz).replace(tzinfo=None)
        if local >= naive:
            hi = mid
        else:
            lo = mid
    return float(hi)


def next_run_after(schedule: dict[str, Any], after: float) -> float:
    """The first slot STRICTLY after *after* (epoch), jitter not included."""
    if schedule.get("type") == "interval":
        step = int(schedule["minutes"]) * 60
        anchor = float(schedule.get("anchor") or 0)
        k = max(0, math.floor((after - anchor) / step) + 1)
        return anchor + k * step
    tz = load_zone(schedule["tz"])
    days = set(schedule["days"])
    times = [(int(t[:2]), int(t[3:])) for t in schedule["times"]]
    start = datetime.fromtimestamp(after, tz).date() - timedelta(days=1)
    best: float | None = None
    for offset in range(0, 10):
        day = start + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        for hh, mm in times:
            candidate = _local_to_utc(day, hh, mm, tz)
            if candidate > after and (best is None or candidate < best):
                best = candidate
        if best is not None:
            break  # days ascend: the first day with a candidate holds the earliest one
    if best is None:  # unreachable for a validated schedule (<= 7 days apart)
        raise ScheduleError("No next run could be computed for this schedule.")
    return best


def next_run_with_jitter(schedule: dict[str, Any], monitor_id: str, after: float) -> float:
    return next_run_after(schedule, after) + jitter(monitor_id)


def _days_text(days: list[int]) -> str:
    if len(days) == 7:
        return ""
    names = [DAY_NAMES[d].capitalize() for d in days]
    if len(days) >= 3 and days == list(range(days[0], days[-1] + 1)):
        return f"{names[0]}–{names[-1]}"
    return ", ".join(names)


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def describe(schedule: dict[str, Any]) -> str:
    """The schedule in words, timezone and its source included."""
    if schedule.get("type") == "interval":
        minutes = int(schedule["minutes"])
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"every {hours} hour{'s' if hours != 1 else ''}"
        return f"every {minutes} minutes"
    parts = [f"daily at {_join(list(schedule['times']))}"]
    days = _days_text(list(schedule["days"]))
    if days:
        parts.append(days)
    source = schedule.get("tz_source")
    tz = schedule["tz"]
    if source == TZ_SLACK:
        parts.append(f"{tz} (your Slack timezone)")
    elif source == TZ_UTC_FALLBACK:
        parts.append(f"{tz} (Slack did not report your timezone)")
    else:
        parts.append(tz)
    return ", ".join(parts)


def describe_raw(raw: Any) -> str:
    """Best-effort wording of an unvalidated schedule (consent prompt); never raises."""
    try:
        s = parse_schedule(raw, owner_tz=None, now=0.0)
    except Exception:
        return "a schedule"
    if s["type"] == "daily" and isinstance(raw.get("daily"), dict) and not raw["daily"].get("timezone"):
        s = {**s, "tz": "your Slack timezone", "tz_source": TZ_EXPLICIT}
    return describe(s)


def tz_info(schedule: dict[str, Any]) -> dict[str, str] | None:
    if schedule.get("type") != "daily":
        return None
    return {"timezone": schedule["tz"], "timezone_source": schedule["tz_source"]}


def local_iso(schedule: dict[str, Any], epoch: float) -> str:
    """*epoch* in the schedule's timezone (UTC for intervals), ISO-8601 with offset."""
    tz = load_zone(schedule["tz"]) if schedule.get("type") == "daily" else UTC
    return datetime.fromtimestamp(epoch, tz).isoformat(timespec="seconds")
