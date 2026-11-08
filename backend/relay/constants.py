"""Relay constants (issue #2353)."""

# How long a relay approval prompt raised from inside a tool call waits for the owner before the
# send is refused (``approval_timeout``). A constant, not user config: it only has to be longer
# than the 60 s one-shot grant and shorter than the CLI's ~28 h per-call tool limit.
RELAY_PROMPT_TIMEOUT_SECONDS = 600
