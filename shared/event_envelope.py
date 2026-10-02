"""Typed envelope for every entry written to an EventQueue (issue #2063, epic #1990 stage 2a).

Both tiers construct/read these — Backend's real queues and the Frontend tier's locally
relayed copies — so the shape can't drift between them the way the current ad-hoc dicts have.
"""

from dataclasses import asdict, dataclass

DEFAULT_BACKEND_ID = "local"  # UC4: single implicit value until #1818 multi-backend.

QUEUE_UI = "ui"
QUEUE_SESSION = "session"
QUEUE_AUDIT = "audit"

# Top-level keys the envelope itself owns. Every other top-level key on a raw append-site
# dict is payload and gets folded into `data` by from_dict() — most of today's 31 registered
# event types put their payload directly at the top level (sibling to "type"), not nested
# under a "data" key, so treating `data` as "the one key named data" would silently drop it.
_ENVELOPE_FIELDS = frozenset(
    {"type", "queue", "sequence", "timestamp", "data", "backend_id", "scope", "event_id"}
)


@dataclass
class EventEnvelope:
    type: str
    queue: str  # QUEUE_UI | QUEUE_SESSION | QUEUE_AUDIT
    sequence: int  # the queue cursor at append time
    timestamp: str
    data: dict
    backend_id: str = DEFAULT_BACKEND_ID
    scope: str | None = None  # session_id or project_id, when applicable

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
        data = dict(d.get("data") or {})
        for key, value in d.items():
            if key not in _ENVELOPE_FIELDS:
                data[key] = value
        return cls(
            type=d.get("type", ""),
            queue=d.get("queue", ""),
            sequence=d.get("sequence", 0),
            timestamp=d.get("timestamp", ""),
            data=data,
            backend_id=d.get("backend_id", DEFAULT_BACKEND_ID),
            scope=d.get("scope"),
        )
