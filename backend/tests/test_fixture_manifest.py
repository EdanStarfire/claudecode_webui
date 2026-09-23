"""Regression tests keeping EXCLUDED_FROM_EQUIVALENCE.json honest (issue #1998, AC6).

Two checks so stage 1b's (#1999) equivalence harness has a single tested file to
check against instead of hardcoding fixture names or re-deriving the "has a raw
log" heuristic itself:
  1. every fixture directory lacking raw_log.jsonl must appear in the manifest
     (catches a future hand-built fixture that forgets to exclude itself)
  2. every fixture directory that HAS raw_log.jsonl must NOT appear in it
     (catches accidentally excluding a real recorded fixture)
"""

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "EXCLUDED_FROM_EQUIVALENCE.json"


def _fixture_dirs() -> list[Path]:
    return sorted(
        d for d in FIXTURES_DIR.iterdir()
        if d.is_dir() and d.name != "raw" and (d / "messages.jsonl").exists()
    )


def _manifest_names() -> set[str]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {entry["name"] for entry in data["excluded_from_equivalence"]}


def test_manifest_is_valid_json_with_reasons():
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert "excluded_from_equivalence" in data
    for entry in data["excluded_from_equivalence"]:
        assert entry.get("name")
        assert entry.get("reason")


def test_every_fixture_without_raw_log_is_excluded():
    manifest_names = _manifest_names()
    for fixture_dir in _fixture_dirs():
        if not (fixture_dir / "raw_log.jsonl").exists():
            assert fixture_dir.name in manifest_names, (
                f"Fixture '{fixture_dir.name}' has no raw_log.jsonl and must be listed in "
                f"{MANIFEST_PATH.name}"
            )


def test_no_recorded_fixture_is_accidentally_excluded():
    manifest_names = _manifest_names()
    for fixture_dir in _fixture_dirs():
        if (fixture_dir / "raw_log.jsonl").exists():
            assert fixture_dir.name not in manifest_names, (
                f"Fixture '{fixture_dir.name}' has a raw_log.jsonl (it's a real recorded "
                f"fixture) but is listed in {MANIFEST_PATH.name} as excluded"
            )


def test_current_five_fixtures_are_exactly_the_manifest():
    """Locks in the known state at time of writing (issue #1998) — the 5 existing
    hand-built fixtures, no more, no less. A genuinely new fixture should update
    the manifest deliberately rather than this test silently passing either way.
    """
    assert _manifest_names() == {
        "hook_messages", "multi_turn", "permission_flow", "single_turn", "tool_use",
    }
