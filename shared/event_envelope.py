"""Typed envelope for every entry written to an EventQueue (issue #2063, epic #1990 stage 2a).

Both tiers construct/read these — Backend's real queues and the Frontend tier's locally
relayed copies — so the shape can't drift between them the way the current ad-hoc dicts have.
"""

import secrets
from dataclasses import asdict, dataclass

# Epic #1990 stage 5a: regenerated once per Python process at first import — Backend
# runs as its own OS process, so this is automatically a new value on every real
# Backend restart with no explicit "on startup, generate and store an epoch" code
# anywhere. Not persisted: every restart is a new epoch by design (AC1 only requires
# random-unique, not "recognize the same Backend restarting into the same epoch").
PROCESS_EPOCH: str = secrets.token_hex(8)

DEFAULT_BACKEND_ID = "local"  # UC4: single implicit value until #1818 multi-backend.
# Frontend-local writes (e.g. src/routers/system.py's server_restarting) land on the same
# ui_queue poll_relay.py relays Backend's real backend_id/sequence into — stamping
# DEFAULT_BACKEND_ID here too could collide (backend_id, queue, scope, sequence) tuples
# with an unrelated relayed event and produce a duplicate event_id.
FRONTEND_LOCAL_BACKEND_ID = "frontend-local"

QUEUE_UI = "ui"
QUEUE_SESSION = "session"
QUEUE_AUDIT = "audit"

# Top-level keys the envelope itself owns. Every other top-level key on a raw append-site
# dict is payload and gets folded into `data` by from_dict() — most of today's 31 registered
# event types put their payload directly at the top level (sibling to "type"), not nested
# under a "data" key, so treating `data` as "the one key named data" would silently drop it.
_ENVELOPE_FIELDS = frozenset(
    {"type", "queue", "sequence", "timestamp", "data", "backend_id", "scope", "event_id", "epoch"}
)


def fold_payload(payload: dict) -> dict:
    """Fold a raw append-site payload dict into the shape `EventEnvelope.data` holds —
    shared by `from_dict()` (reading an already-appended dict back) and `emit()` in
    `shared/event_emitter.py` (constructing one to append). Keeping both directions on
    the same fold means a payload that already nests its own fields under a "data" key
    (today's convention for roughly half of the registered event types) ends up with the
    exact same `.data` shape whether it was built by `emit()` or reconstructed via
    `from_dict()` from a legacy ad-hoc dict — no double-nesting, no shape drift for
    consumers that read through either path."""
    data = dict(payload.get("data") or {})
    for key, value in payload.items():
        if key not in _ENVELOPE_FIELDS:
            data[key] = value
    return data


@dataclass
class EventEnvelope:
    type: str
    queue: str  # QUEUE_UI | QUEUE_SESSION | QUEUE_AUDIT
    sequence: int  # the queue cursor at append time
    timestamp: str
    data: dict
    backend_id: str = DEFAULT_BACKEND_ID
    scope: str | None = None  # session_id or project_id, when applicable
    epoch: str = PROCESS_EPOCH  # which Backend-process generation produced this event

    @property
    def event_id(self) -> str:
        # Deterministic, not a random UUID: AC12 regenerates committed fixtures by replaying
        # the real pipeline, and a random id would make every replay produce a different
        # (if equally valid) fixture, defeating byte-for-byte fixture diffing.
        scope_segment = self.scope if self.scope is not None else "-"
        return f"{self.backend_id}:{self.queue}:{scope_segment}:{self.sequence}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["event_id"] = self.event_id
        if self.scope is None:
            del d["scope"]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "EventEnvelope":
        return cls(
            type=d.get("type", ""),
            queue=d.get("queue", ""),
            sequence=d.get("sequence", 0),
            timestamp=d.get("timestamp", ""),
            data=fold_payload(d),
            backend_id=d.get("backend_id", DEFAULT_BACKEND_ID),
            scope=d.get("scope"),
            epoch=d.get("epoch", PROCESS_EPOCH),
        )
