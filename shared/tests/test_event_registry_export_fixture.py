"""Writes/validates the registry fixture frontend/src/stores/__tests__/eventRegistry
Completeness.test.js consumes (issue #2065, stage 2b-A, AC8). Mirrors backend/tests/
test_equivalence_replay_generation.py's role for #2037's equivalence harness."""

import json
from pathlib import Path

from shared.event_registry import export_json

GENERATED_PATH = Path(__file__).parent / "fixtures" / "generated" / "event_registry.json"
STATIC_PATH = Path(__file__).parent / "fixtures" / "static" / "event_registry.json"


def test_generated_fixture_is_written():
    GENERATED_PATH.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_PATH.write_text(json.dumps(export_json(), indent=2, sort_keys=True))
    assert GENERATED_PATH.exists()


def test_static_fixture_matches_export_json():
    # Freshness guard (mirrors #2063's test_synthetic_fixture_freshness.py pattern) — fails
    # loudly if someone changes the registry and forgets to regenerate the committed
    # fallback frontend's standalone `vitest run` (no prior pytest run) depends on.
    committed = json.loads(STATIC_PATH.read_text())
    assert committed == export_json(), (
        "shared/tests/fixtures/static/event_registry.json is stale — regenerate it from "
        "export_json() and commit the update."
    )
