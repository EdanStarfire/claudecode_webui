"""Shared parsing for the `{events, next_cursor, reset, evicted}` long-poll
response body — the one piece of protocol logic every poll consumer needs,
whether it's the stage 1b fault harness (`src/tests/simulation/fault_harness.py`)
or the scripted scenario driver (`backend/tools/scenario_driver/`) (issue #2038,
AC10).
"""

from dataclasses import dataclass


@dataclass
class PollBatch:
    events: list[dict]
    next_cursor: int
    reset: bool
    evicted: bool


def parse_poll_response(body: dict) -> PollBatch:
    return PollBatch(
        events=body["events"],
        next_cursor=body["next_cursor"],
        reset=body["reset"],
        evicted=body["evicted"],
    )
