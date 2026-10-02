"""Regenerates 2026-09-23-primary's committed raw_log.jsonl `queue_event` records via the
real-pipeline replay (issue #2063 AC12). NOT run by default — writes to a committed fixture.

    uv run pytest backend/tests/test_regenerate_primary_fixture_queue_events.py -m regenerate_fixture

Only `queue_event` records change. Every other record kind (`sdk_message`,
`permission_invocation`, `permission_response`, `interrupt`, `lifecycle` — the ground truth
being replayed) and `rest_history.json` are left untouched.
"""

import json
from pathlib import Path

import pytest

from backend.tests.integration import conftest as _integration_conftest

api_integration_env = _integration_conftest.api_integration_env

_FIXTURE_NAME = "2026-09-23-primary"
_RAW_LOG_PATH = Path(__file__).parent / "fixtures" / "raw" / _FIXTURE_NAME / "raw_log.jsonl"


@pytest.mark.regenerate_fixture
async def test_regenerate_primary_fixture_queue_events(api_integration_env):
    client = api_integration_env["client"]
    create_test_project = api_integration_env["create_test_project"]
    create_test_session = api_integration_env["create_test_session"]
    webui = api_integration_env["webui"]

    project = await create_test_project(name=f"Regenerate: {_FIXTURE_NAME}")
    session = await create_test_session(project["project_id"], name=f"raw/{_FIXTURE_NAME}")
    session_id = session["session_id"]

    resp = await client.post(f"/api/sessions/{session_id}/start")
    assert resp.status_code == 200, f"start session failed: {resp.text}"

    queue = webui.session_queues.get(session_id)
    assert queue is not None, f"No EventQueue registered for session {session_id}"
    events, _next_cursor, _evicted = queue.events_since(0)
    assert events, (
        "Replay produced zero events — refusing to overwrite the committed fixture "
        "with an empty result."
    )

    original_lines = [
        line for line in _RAW_LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    preserved = [line for line in original_lines if json.loads(line).get("kind") != "queue_event"]
    fresh_queue_event_lines = [
        json.dumps(
            {"kind": "queue_event", "timestamp": event.get("timestamp"), "event": event},
            default=str,
        )
        for event in events
    ]

    _RAW_LOG_PATH.write_text(
        "\n".join(preserved + fresh_queue_event_lines) + "\n", encoding="utf-8"
    )

    reread = [
        json.loads(line) for line in _RAW_LOG_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(reread) == len(preserved) + len(fresh_queue_event_lines), (
        "raw_log.jsonl round-trip lost records after regeneration."
    )
    assert reread[: len(preserved)] == [json.loads(line) for line in preserved], (
        "Preserved (non-queue_event) records moved out of their original relative order."
    )
