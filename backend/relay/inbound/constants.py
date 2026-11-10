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
# it is accepted and about to be enqueued as an instruction (issue #2364). Only instructions get
# it: never ignored/refused/late/rate-limited/replayed messages and never monitored content (3b).
INBOUND_ACK_REACTION = "eyes"
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
