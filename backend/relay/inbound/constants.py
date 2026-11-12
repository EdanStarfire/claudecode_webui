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
KIND_MONITOR_MENTIONS = "monitor_mentions"  # stage 3b-ii: every message that mentions the owner
KIND_MONITOR_DMS = "monitor_dms"  # stage 3b-ii: direct and group messages other people send the owner
SCOPE_KINDS = frozenset({KIND_MONITOR_MENTIONS, KIND_MONITOR_DMS})  # no channel; need the owner's consent
KIND_MONITOR_UNREAD = "monitor_unread"  # stage 3c-ii: scheduled summary of Slack's own unread state
MONITOR_KINDS = frozenset({KIND_MONITOR_THREAD, KIND_MONITOR_CHANNEL, KIND_MONITOR_UNREAD}) | SCOPE_KINDS

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
MAX_PENDING_ENVELOPES = 3  # un-dispatched monitor envelopes per session; beyond it the flush keeps waiting

# Backfill, caps, expiry
MONITOR_BACKFILL_MAX_AGE_SECONDS = 24 * 3600
MAX_MONITORS_PER_SESSION = 20
MAX_MONITORS_PER_RELAY = 100
MONITOR_THREAD_IDLE_SECONDS = 14 * 24 * 3600
MONITOR_MAX_EXPIRY_SECONDS = 90 * 24 * 3600
MAX_KEYWORDS = 10
MAX_KEYWORD_CHARS = 100
MAX_FILTER_USERS = 10
MONITOR_RECENT_KEYS = 1000  # per monitor: message keys remembered for per-monitor dedupe

# Scope-monitor backfill (3b-ii)
BACKFILL_MARGIN_SECONDS = 60.0  # re-read this far before the cursor: search indexes lag
MENTION_RERUN_DELAY_SECONDS = 30.0  # one delayed re-run of the mentions backfill (index lag)
HYDRATION_CAP = 20  # single-message fetches per backfill run
DM_BACKFILL_MAX_CONVERSATIONS = 50
SEARCH_MIN_SPACING_SECONDS = 6.0  # per relay between two backfill searches
SEARCH_MAX_PAGES = 3
RECENT_KEYS_PERSISTED = 100  # per monitor: delivered keys written with the cursor

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

# ------------------------------------------------------- scheduled digests (stage 3c-i)
MODE_LIVE = "live"
MODE_DIGEST = "digest"
DIGEST_MIN_INTERVAL_MINUTES = 30
DIGEST_MAX_TIMES = 4
DIGEST_WINDOW_MAX_AGE_SECONDS = 72 * 3600  # content window of one digest run
DIGEST_HYDRATION_CAP = 40  # single-message fetches per digest run (hits beyond it are never delivered)
DIGEST_TICK_SECONDS = 30.0
DIGEST_MAX_RUNS_PER_TICK = 3
DIGEST_JITTER_MAX_SECONDS = 90
DIGEST_STARTUP_DELAY_SECONDS = 30.0
DIGEST_RATE_LIMIT_RESCHEDULE_SECONDS = 300.0
DIGEST_RATE_LIMIT_MAX_RETRIES = 3
DIGEST_DEFER_STEP_SECONDS = 600.0
DIGEST_DEFER_MAX = 6
RUN_NOW_AGENT_MIN_SECONDS = 300.0
RUN_NOW_OWNER_MIN_SECONDS = 60.0
MAX_SCHEDULED_PER_RELAY = 30
API_MIN_SPACING_SECONDS = 1.3  # between Tier-3 calls (info / history / replies) of a digest run

# ------------------------------------------------------- unread monitors (stage 3c-ii)
UNREAD_MIN_INTERVAL_MINUTES = 60
UNREAD_MAX_CONVERSATIONS = 30  # conversations listed in one envelope
UNREAD_MESSAGES_PER_CONVERSATION = 5
UNREAD_MESSAGE_CHARS = 500
UNREAD_HISTORY_LIMIT = 50
UNREAD_RUN_MAX_SECONDS = 300.0
UNREAD_MAX_CHANNELS = 20
UNREAD_MAX_EXCLUDE = 30
UNREAD_NOTIFIED_MAX = 100
INFO_CACHE_SECONDS = 60.0
LIST_CACHE_SECONDS = 600.0
