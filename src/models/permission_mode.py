"""Permission mode enumeration for sessions and templates."""

from enum import Enum


class PermissionMode(str, Enum):
    """Valid permission modes for sessions and templates.

    Passed directly to ClaudeAgentOptions(permission_mode=...).
    Using str mixin ensures values serialize as plain strings and
    compare equal to their string equivalents.
    """

    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS_PERMISSIONS = "bypassPermissions"
    DONT_ASK = "dontAsk"
    AUTO = "auto"
