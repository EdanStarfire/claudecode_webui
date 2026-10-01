"""Comprehensive SDK-type-coverage test for raw_log.jsonl fixtures (issue #2037, AC5).

Unlike RawFixtureReplay (backend/mock_sdk.py), which is a sequential replay that
necessarily stops at the first unrecognized type, this test attempts
reconstruct_sdk_message() on every recorded sdk_message record in every committed
fixture and collects every distinct unrecognized type, so a future SDK bump's
re-recording can't hide a second unknown type behind the first.
"""

import json
from pathlib import Path

import pytest

from backend.raw_replay import KNOWN_UNHANDLED_SDK_TYPES, reconstruct_sdk_message

FIXTURES_RAW_DIR = Path(__file__).parent / "fixtures" / "raw"


def _list_raw_fixture_names() -> list[str]:
    """Every raw-fixture directory with a raw_log.jsonl — discovered dynamically,
    not hardcoded. Raises (does not return an empty list) if none are found, so a
    misconfigured checkout fails this suite loudly rather than silently reporting
    zero tests (mirrors src/tests/test_fault_simulation.py's _list_raw_fixture_names()
    fail-not-skip pattern)."""
    if not FIXTURES_RAW_DIR.exists():
        raise RuntimeError(f"Raw fixtures directory not found: {FIXTURES_RAW_DIR}")
    names = sorted(
        p.name
        for p in FIXTURES_RAW_DIR.iterdir()
        if p.is_dir() and (p / "raw_log.jsonl").exists()
    )
    if not names:
        raise RuntimeError(f"No raw fixtures with raw_log.jsonl found under {FIXTURES_RAW_DIR}")
    return names


def _sdk_message_records(name: str) -> list[dict]:
    raw_log_path = FIXTURES_RAW_DIR / name / "raw_log.jsonl"
    records = []
    for line in raw_log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("kind") == "sdk_message":
            records.append(record)
    return records


def _unrecognized_types(name: str) -> set[str]:
    """Attempt reconstruction on every sdk_message record in the fixture, returning
    the set of distinct _type values that fail — not stopping at the first failure.
    A type listed in KNOWN_UNHANDLED_SDK_TYPES is an explicitly tracked exception,
    not a failure."""
    failing: set[str] = set()
    for record in _sdk_message_records(name):
        _type = record["_type"]
        if _type in KNOWN_UNHANDLED_SDK_TYPES:
            continue
        try:
            reconstruct_sdk_message(_type, record["data"])
        except ValueError:
            failing.add(_type)
    return failing


class TestFixtureDiscoveryFailsLoudly:
    def test_missing_fixtures_dir_raises(self, monkeypatch):
        monkeypatch.setattr(
            "backend.tests.test_raw_replay_sdk_coverage.FIXTURES_RAW_DIR",
            Path(__file__).parent / "does-not-exist",
        )
        with pytest.raises(RuntimeError, match="not found"):
            _list_raw_fixture_names()

    def test_empty_fixtures_dir_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "backend.tests.test_raw_replay_sdk_coverage.FIXTURES_RAW_DIR", tmp_path
        )
        with pytest.raises(RuntimeError, match="No raw fixtures"):
            _list_raw_fixture_names()


class TestEverySdkTypeIsReconstructable:
    """AC5's comprehensive check: every sdk_message record's _type across every
    committed fixture must be known to reconstruct_sdk_message() (or explicitly
    tracked in KNOWN_UNHANDLED_SDK_TYPES)."""

    @pytest.mark.parametrize("fixture_name", _list_raw_fixture_names())
    def test_all_sdk_message_types_reconstruct(self, fixture_name):
        failing = _unrecognized_types(fixture_name)
        assert not failing, (
            f"Unrecognized SDK message type(s) in fixture {fixture_name!r}: "
            f"{sorted(failing)} — either implement reconstruction in raw_replay.py "
            f"or add to KNOWN_UNHANDLED_SDK_TYPES with an issue reference."
        )
