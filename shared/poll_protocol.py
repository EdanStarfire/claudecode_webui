"""Shared parsing for the `{events, next_cursor, reset, evicted}` long-poll
response body — the one piece of protocol logic every poll consumer needs,
whether it's the stage 1b fault harness (`src/tests/simulation/fault_harness.py`)
or the scripted scenario driver (`backend/tools/scenario_driver/`, issue #2038).
"""

from dataclasses import dataclass

from pydantic import BaseModel

from shared.event_envelope import EventEnvelope


@dataclass
class PollBatch:
    events: list[EventEnvelope]
    next_cursor: int
    reset: bool
    evicted: bool
    epoch: str


def parse_poll_response(body: dict) -> PollBatch:
    return PollBatch(
        events=[EventEnvelope.from_dict(e) for e in body["events"]],
        next_cursor=body["next_cursor"],
        reset=body["reset"],
        evicted=body["evicted"],
        epoch=body["epoch"],
    )


@dataclass
class PollResponse:
    events: list[dict]
    next_cursor: int
    reset: bool
    evicted: bool
    epoch: str


@dataclass
class FrontendPollResponse(PollResponse):
    backend_status: str


# --- Multiplexed POST /api/poll (epic #1990 stage 5c) -----------------------------
# One request body shape and one response-entry shape, shared by both tiers'
# `POST /api/poll` handlers so the wire contract can't drift between them.


class StreamQuery(BaseModel):
    since: int = 0


class MultiplexPollRequest(BaseModel):
    """Request body for `POST /api/poll`. `ui` is typically always present (the UI
    stream is always subscribed); `sessions` carries the browser's current
    subscription-set cursors, keyed by session_id."""

    timeout: int = 30
    ui: StreamQuery | None = None
    sessions: dict[str, StreamQuery] = {}


@dataclass
class MultiplexStreamResponse:
    """One subscribed stream's slice within a `POST /api/poll` response — the same
    `{events, next_cursor, reset, evicted, epoch}` shape every single-stream poll
    response has always used, just nested under "ui"/a session_id instead of being
    the whole response body."""

    events: list[dict]
    next_cursor: int
    reset: bool
    evicted: bool
    epoch: str
