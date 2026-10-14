"""Regenerates 2026-09-23-primary's committed messages.jsonl/rest_history.json via the
real-pipeline replay (issue #2109 AC11). NOT run by default — writes to a committed fixture.

    uv run pytest backend/tests/test_regenerate_primary_fixture_messages.py -m regenerate_fixture

Re-invoke only if a backend change alters the canonical shape/content of stored messages or
the REST history response for this fixture (e.g. a change to what gets persisted/bypassed,
tool-call reconstruction/canonicalization logic, or similar) — not for unrelated changes.

`raw_log.jsonl`'s `queue_event` records are a separate, already-existing opt-in
(test_regenerate_primary_fixture_queue_events.py) and are untouched by this test.
"""

import json
import shutil
from pathlib import Path

import pytest

from backend.tests.integration import conftest as _integration_conftest
from backend.tests.test_equivalence_replay_generation import _fetch_all_messages

api_integration_env = _integration_conftest.api_integration_env

_FIXTURE_NAME = "2026-09-23-primary"
RAW_FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "raw"


@pytest.mark.regenerate_fixture
async def test_regenerate_primary_fixture_messages(api_integration_env):
    client = api_integration_env["client"]
    create_test_project = api_integration_env["create_test_project"]
    create_test_session = api_integration_env["create_test_session"]

    project = await create_test_project(name=f"Regenerate: {_FIXTURE_NAME}")
    session = await create_test_session(project["project_id"], name=f"raw/{_FIXTURE_NAME}")
    session_id = session["session_id"]

    resp = await client.post(f"/api/sessions/{session_id}/start")
    assert resp.status_code == 200, f"start session failed: {resp.text}"

    rest_history = await _fetch_all_messages(client, session_id)
    assert rest_history.get("messages"), (
        "Replay produced zero stored messages — refusing to overwrite the committed "
        "fixture with an empty result."
    )

    storage = api_integration_env["coordinator"]._storage_managers[session_id]
    live_messages_path = storage.messages_file
    assert live_messages_path.exists(), (
        f"No live messages.jsonl found for fixture '{_FIXTURE_NAME}' at "
        f"{live_messages_path} — cannot promote into the committed fixture."
    )

    committed_dir = RAW_FIXTURES_ROOT / _FIXTURE_NAME
    shutil.copyfile(live_messages_path, committed_dir / "messages.jsonl")
    (committed_dir / "rest_history.json").write_text(
        json.dumps(rest_history, indent=2, default=str), encoding="utf-8"
    )

    reread_messages = [
        json.loads(line)
        for line in (committed_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert reread_messages, "messages.jsonl round-trip came back empty."

    reread_rest_history = json.loads(
        (committed_dir / "rest_history.json").read_text(encoding="utf-8")
    )
    assert reread_rest_history.get("messages"), "rest_history.json round-trip came back empty."
