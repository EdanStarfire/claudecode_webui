"""Inbound constants (issue #2103 stage 3a)."""

import uuid

# An instruction older than this at processing time is not executed (one "received late" reply).
INSTRUCTION_MAX_AGE_SECONDS = 900
THREAD_TTL_SECONDS = 14 * 24 * 3600  # thread records idle this long expire
MAX_THREAD_RECORDS = 200  # per relay; oldest-idle evicted
DEDUPE_RING_SIZE = 5000
MAX_INSTRUCTION_CHARS = 20000
HOUSEKEEPING_INTERVAL_SECONDS = 600
STATE_FLUSH_DEBOUNCE_SECONDS = 2.0
DROP_SUMMARY_INTERVAL_SECONDS = 300
BACKFILL_MAX_PAGES = 3
BACKFILL_PAGE_SIZE = 100

# Reconnect policy of the supervisor
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 60.0
MIN_CONNECT_SPACING_SECONDS = 5.0
STABLE_CONNECTION_SECONDS = 60.0

# Accepted-instruction rate limits (token buckets): per relay and per thread
RELAY_RATE_PER_MINUTE = 30
RELAY_BURST = 10
THREAD_RATE_PER_MINUTE = 10
THREAD_BURST = 10

# Deterministic comm ids: uuid5 over relay:channel:ts
COMM_NAMESPACE = uuid.UUID("5d3e2c7e-0b0a-4f2a-9a55-2103a0b17a3a")

# Slack reaction (emoji name without colons) the relay itself adds to an owner's self-DM message once
# it is accepted and about to be enqueued as an instruction (issue #2364). A flying saucer: a reaction
# the owner rarely uses by hand, so it is not mistaken for their own. Only instructions get it: never
# ignored/refused/late/rate-limited/replayed messages and never monitored content (3b).
INBOUND_ACK_REACTION = "flying_saucer"
# Upper bound for the (background) reaction call; delivery never waits for it.
ACK_TIMEOUT_SECONDS = 10.0

THREAD_KIND = "instruction_thread"

# Connection status
STATUS_DISABLED = "disabled"
STATUS_TRANSPORT_UNAVAILABLE = "transport_unavailable"
STATUS_CONNECTING = "connecting"
STATUS_CONNECTED = "connected"
STATUS_BACKOFF = "backoff"
STATUS_AUTH_FAILED = "auth_failed"
STATUS_STOPPED = "stopped"

# ---------------------------------------------------------------- monitors (stage 3b-i)
KIND_MONITOR_THREAD = "monitor_thread"
KIND_MONITOR_CHANNEL = "monitor_channel"
MONITOR_KINDS = frozenset({KIND_MONITOR_THREAD, KIND_MONITOR_CHANNEL})  # 3b-ii adds mentions / dms

# Coalescing window and envelope caps
MONITOR_WINDOW_SECONDS = 60.0
ENVELOPE_MAX_MESSAGES = 50
ENVELOPE_MAX_CHARS = 24_000
MESSAGE_MAX_CHARS = 2_000

# Flood limits (matched messages per rolling window) and per-session envelope rate
FLOOD_WINDOW_SECONDS = 600.0
FLOOD_PER_MONITOR = 120
FLOOD_PER_RELAY = 600
MAX_ENVELOPES_PER_HOUR = 12
FLOOD_SUSPEND_WINDOWS = 3  # consecutive flooded windows suspend the monitor
POSTPONE_STEP_SECONDS = 60.0
POSTPONE_MAX_SECONDS = 600.0

# Backfill, caps, expiry
MONITOR_BACKFILL_MAX_AGE_SECONDS = 24 * 3600
MAX_MONITORS_PER_SESSION = 20
MAX_MONITORS_PER_RELAY = 100
MONITOR_THREAD_IDLE_SECONDS = 14 * 24 * 3600
MONITOR_MAX_EXPIRY_SECONDS = 90 * 24 * 3600
MAX_KEYWORDS = 10
MAX_KEYWORD_CHARS = 100
MAX_FILTER_USERS = 10

# Delivery watcher
WATCH_POLL_SECONDS = 5.0
WATCH_MAX_AGE_SECONDS = 24 * 3600
DELIVERY_FAILURES_TO_SUSPEND = 3
LABEL_TIMEOUT_SECONDS = 3.0
MONITOR_COMM_NAMESPACE = uuid.UUID("a3b7c9e2-6d41-4f0b-8a52-2103b0a17b3b")

# Monitor status / suspension reasons
SUSPEND_CHANNEL_BLOCKED = "channel_blocked"
SUSPEND_POLICY_UNSET = "policy_unset"
SUSPEND_DISPOSED = "disposed"
SUSPEND_SESSION_INACTIVE = "session_inactive"
SUSPEND_FLOOD = "flood"
SUSPEND_DELIVERY_FAILED = "delivery_failed"
# Reasons the policy / eligibility re-check may lift on its own (flood / delivery_failed need the
# agent to re-create the monitor; disposed is lifted by a session start).
AUTO_RESUME_REASONS = frozenset({SUSPEND_CHANNEL_BLOCKED, SUSPEND_POLICY_UNSET, SUSPEND_SESSION_INACTIVE})
