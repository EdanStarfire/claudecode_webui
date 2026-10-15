"""
Timestamp Utilities - Standardized timestamp handling

Provides Unix timestamp (float) generation for consistent message timestamps.
All message timestamps should use Unix timestamps (seconds since epoch) to avoid
parsing overhead and format inconsistencies.
"""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

# Issue #2190 (AC2): matches the CLI's synthetic usage-limit text, e.g.
# "You've hit your session limit · resets 6:10am (UTC)". 12-hour clock, zone name
# in parens.
_RATE_LIMIT_RESET_RE = re.compile(
    r"resets?\s+(\d{1,2}):(\d{2})\s*([ap]m)\s*\(([^)]+)\)", re.IGNORECASE
)


def get_unix_timestamp() -> float:
    """
    Get current time as Unix timestamp (seconds since epoch).

    Returns:
        float: Current time in seconds since epoch (e.g., 1761781369.1559327)
    """
    return datetime.now(UTC).timestamp()


def normalize_timestamp(timestamp: float | str | datetime) -> float:
    """
    Normalize various timestamp formats to Unix timestamp (float).

    Args:
        timestamp: Timestamp in various formats:
            - float: Unix timestamp in seconds (returned as-is)
            - str: ISO 8601 string (parsed and converted)
            - datetime: datetime object (converted to timestamp)

    Returns:
        float: Unix timestamp in seconds
    """
    if isinstance(timestamp, float):
        return timestamp
    elif isinstance(timestamp, str):
        return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp()
    elif isinstance(timestamp, datetime):
        return timestamp.timestamp()
    else:
        raise TypeError(f"Unsupported timestamp type: {type(timestamp)}")


def parse_rate_limit_reset_time(text: str, reference_ts: float) -> float | None:
    """
    Parse a usage-limit reset time out of CLI-emitted synthetic text (issue #2190,
    AC2), e.g. "You've hit your session limit · resets 6:10am (UTC)".

    Resolves to the next occurrence of that wall-clock time at or after
    `reference_ts` in the named zone, rolling forward one day if the parsed time
    has already passed on `reference_ts`'s calendar date.

    Returns None on any parsing/timezone failure — a CLI text-format change must
    silently degrade to the caller's own fallback, not raise.
    """
    try:
        match = _RATE_LIMIT_RESET_RE.search(text)
        if not match:
            return None
        hour_str, minute_str, meridiem, zone_name = match.groups()
        hour = int(hour_str)
        minute = int(minute_str)
        if not (1 <= hour <= 12) or not (0 <= minute <= 59):
            return None
        if meridiem.lower() == "pm" and hour != 12:
            hour += 12
        elif meridiem.lower() == "am" and hour == 12:
            hour = 0

        tz = ZoneInfo(zone_name.strip())
        reference_dt = datetime.fromtimestamp(reference_ts, tz=tz)
        candidate = reference_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate < reference_dt:
            candidate += timedelta(days=1)
        return candidate.timestamp()
    except Exception:
        return None
