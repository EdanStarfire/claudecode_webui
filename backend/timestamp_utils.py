"""
Timestamp Utilities - Standardized timestamp handling

Provides Unix timestamp (float) generation for consistent message timestamps.
All message timestamps should use Unix timestamps (seconds since epoch) to avoid
parsing overhead and format inconsistencies.
"""

from datetime import UTC, datetime


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
