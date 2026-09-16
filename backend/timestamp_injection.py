"""
Timestamp Injection - Automatic timestamp prefixing for user messages (issue #1779).

Pure functions operating on an explicit ``now_utc`` so injection logic is testable
without mocking wall-clock time. Called from SessionCoordinator.send_message().
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def resolve_timezone(tz_name: str | None) -> ZoneInfo:
    """Resolve an IANA timezone name to a ZoneInfo, falling back to UTC if invalid."""
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo("UTC")


def local_date_str(now_utc: datetime, tz_name: str) -> str:
    """ISO date (YYYY-MM-DD) for now_utc in the given timezone, for once-per-day comparison."""
    return now_utc.astimezone(resolve_timezone(tz_name)).date().isoformat()


def format_injection_prefix(now_utc: datetime, tz_name: str) -> str:
    """Build the '[Current time: ...]' prefix line for the given UTC instant and timezone."""
    tz = resolve_timezone(tz_name)
    local = now_utc.astimezone(tz)
    display_tz = getattr(tz, "key", None) or "UTC"
    return f"[Current time: {local.strftime('%Y-%m-%d %H:%M')} {display_tz}]"


def inject_timestamp(content: str, now_utc: datetime, tz_name: str) -> str:
    """Prepend a timestamp prefix block to content."""
    return f"{format_injection_prefix(now_utc, tz_name)}\n\n{content}"


def is_slash_command(content: str) -> bool:
    """Whether content is an SDK-native slash command (e.g. /usage, /compact).

    Leading whitespace is tolerated to catch pasted/auto-indented commands.
    No validation against a known command list — anything starting with `/`
    is treated as a command, since the SDK is the source of truth for what's
    valid and false positives here are harmless (message is sent unmodified).
    """
    return content.lstrip().startswith("/")


def maybe_inject_timestamp(
    content: str,
    *,
    enabled: bool,
    frequency: str,
    tz_name: str,
    last_injection_date: str | None,
    now_utc: datetime,
) -> tuple[str, str | None]:
    """Apply timestamp injection per the resolved config.

    Returns (possibly-augmented content, new_last_injection_date). The date is
    only non-None when it changed and the caller must persist it onto SessionInfo
    (once_per_day tracking); every_message and disabled never return a date.
    Slash commands (SDK-native, e.g. /usage) are always passed through
    untouched regardless of frequency, so enabling injection doesn't break
    them and a skipped command never consumes once_per_day's daily slot.
    """
    if not enabled or is_slash_command(content):
        return content, None

    if frequency == "once_per_day":
        today = local_date_str(now_utc, tz_name)
        if last_injection_date == today:
            return content, None
        return inject_timestamp(content, now_utc, tz_name), today

    return inject_timestamp(content, now_utc, tz_name), None
