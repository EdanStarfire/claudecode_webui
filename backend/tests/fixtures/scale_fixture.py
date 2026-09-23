"""Deterministic scale-fixture generator (issue #1999, US5 / edge case).

Replays a recorded raw fixture's `queue_event` stream N times — with fresh,
collision-free identity fields and monotonically advancing timestamps per
repetition — to produce a 20,000+-event sequence for exercising the
equivalence/fault-simulation harnesses' run time and event-loss behavior at
scale. Deliberately NOT committed to the repo (would bloat git with a
synthetic multi-MB fixture): generated at test run time by importing this
module and calling `generate_scale_events()`, or via the CLI entry point for
manual inspection.

Seeded and deterministic: the same `(source_name, target_event_count, seed)`
always produces byte-identical output, so a flaky failure is reproducible.
"""

import argparse
import copy
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RAW_FIXTURES_ROOT = _REPO_ROOT / "backend" / "tests" / "fixtures" / "raw"

# Fields that carry identity and must be rewritten per repetition to avoid
# collisions with the same field's value in every other repetition (a real
# consumer — e.g. frontend/src/stores/message.js's message_id-keyed dedup —
# would otherwise silently collapse every repetition after the first into a
# single delivered event, defeating the whole point of a scale test).
_ID_KEYS = frozenset({
    "message_id", "uuid", "tool_use_id", "id", "task_id", "request_id",
})


def _load_source_queue_events(source_name: str) -> list[dict[str, Any]]:
    raw_log_path = _RAW_FIXTURES_ROOT / source_name / "raw_log.jsonl"
    if not raw_log_path.exists():
        raise FileNotFoundError(f"raw_log.jsonl not found for fixture '{source_name}' at {raw_log_path}")
    events = []
    for line in raw_log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("kind") == "queue_event":
            events.append(record["event"])
    if not events:
        raise ValueError(f"No queue_event records found in {raw_log_path}")
    return events


def _rewrite_ids(value: Any, suffix: str) -> Any:
    """Recursively suffix every identity-shaped field so repetition N's copy of
    an event never collides with repetition 0..N-1's copies. Applied uniformly
    regardless of nesting depth — content blocks, tool_use/tool_result ids,
    task ids, etc. all live at different depths across message types."""
    if isinstance(value, dict):
        rewritten = {}
        for key, val in value.items():
            if key in _ID_KEYS and isinstance(val, str):
                rewritten[key] = f"{val}{suffix}"
            else:
                rewritten[key] = _rewrite_ids(val, suffix)
        return rewritten
    if isinstance(value, list):
        return [_rewrite_ids(item, suffix) for item in value]
    return value


def _shift_timestamp_value(val: Any, offset_seconds: float) -> Any:
    """A raw fixture's `timestamp` field shows up in both shapes recorded
    events actually use: numeric epoch seconds (nested SDK message data) and
    ISO 8601 strings with a UTC offset (the queue_event envelope's own
    top-level `timestamp`, e.g. SessionRecorder.record_queue_event's capture
    format) — both need shifting for repeated events to stay plausible."""
    if isinstance(val, (int, float)):
        return val + offset_seconds
    if isinstance(val, str):
        try:
            parsed = datetime.fromisoformat(val)
        except ValueError:
            return val
        return (parsed + timedelta(seconds=offset_seconds)).isoformat()
    return val


def _shift_timestamps(event: dict[str, Any], offset_seconds: float) -> dict[str, Any]:
    """Advances every timestamp-shaped field forward by `offset_seconds` so
    repeated events stay monotonically ordered instead of all claiming the
    original capture's timestamps — irrelevant to the queue/relay transport
    layer (which doesn't read event timestamps at all) but keeps the data
    plausible for anything that does inspect it."""
    def shift(value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, val in value.items():
                if key == "timestamp":
                    result[key] = _shift_timestamp_value(val, offset_seconds)
                else:
                    result[key] = shift(val)
            return result
        if isinstance(value, list):
            return [shift(item) for item in value]
        return value

    return shift(event)


def generate_scale_events(
    source_name: str = "2026-09-23-primary",
    target_event_count: int = 20_000,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Replays `source_name`'s recorded queue_event stream enough times to
    reach at least `target_event_count` events, each repetition's events
    carrying fresh identity fields and shifted timestamps. Deterministic for a
    given (source_name, target_event_count, seed) — `seed` is accepted for
    interface stability (future randomized perturbation) but the current
    implementation is already fully deterministic without it, so it's
    intentionally never read here (mutating the process-global `random` module
    state as a side effect of an otherwise-pure fixture-scaling call would be
    its own bug).
    """
    source_events = _load_source_queue_events(source_name)
    if not source_events:
        raise ValueError(f"Source fixture '{source_name}' produced no queue_event records")

    repetitions_needed = -(-target_event_count // len(source_events))  # ceil division
    scaled_events: list[dict[str, Any]] = []
    for rep in range(repetitions_needed):
        suffix = "" if rep == 0 else f"-scale-rep{rep}"
        offset = rep * 3600.0  # 1 hour apart per repetition — comfortably monotonic
        for source_event in source_events:
            event = copy.deepcopy(source_event)
            if suffix:
                event = _rewrite_ids(event, suffix)
            event = _shift_timestamps(event, offset)
            scaled_events.append(event)

    return scaled_events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="2026-09-23-primary", help="Source fixture name under backend/tests/fixtures/raw/")
    parser.add_argument("--count", type=int, default=20_000, help="Minimum target event count")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    events = generate_scale_events(args.source, args.count, args.seed)
    print(f"Generated {len(events)} events from source fixture '{args.source}' (target: {args.count})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
