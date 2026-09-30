"""Real-pipeline replay generation for the equivalence gate (issue #2037, AC2).

Replays each committed raw fixture's recorded SDK messages through the real backend
pipeline (mock SDK raw-replay -> real SessionCoordinator/BackendApp message callback ->
storage -> event emission) and captures the resulting live event stream plus REST
history endpoint output into backend/tests/fixtures/generated/{name}/. This is what
frontend/src/stores/__tests__/helpers/fixtureEquivalence.js prefers over the committed
static backend/tests/fixtures/raw/{name}/ copy when present (see its GENERATED_FIXTURES_ROOT
handling) — so the vitest equivalence check (equivalence.test.js) normally exercises fresh
data produced by the real pipeline, not just whatever was captured at commit time.

Reuses backend/tests/integration/conftest.py's api_integration_env fixture (real BackendApp/
SessionCoordinator/EventQueue, MockClaudeSDK injectable via a session's `name` resolving to
a fixture directory), rather than duplicating that harness — bound as a module attribute
here since backend/tests/integration/conftest.py's fixtures aren't visible to sibling
modules outside their own directory subtree. A module-attribute assignment (not
`pytest_plugins = [...]`) because the full suite run also collects backend/tests/
integration/*.py directly, which makes pytest auto-load that conftest.py as an ordinary
conftest — registering the same module a second time via `pytest_plugins` collides
("Plugin already registered under a different name"). Not a `from ... import
api_integration_env` either — pytest's fixture registration keys off of a module's own
`dir()` attribute name, and a bare import creates exactly that, but ruff's pyflakes then
can't tell the import is "used" purely by pytest's own attribute-name-based discovery and
flags it (F401), plus a same-named `@pytest.fixture`-decorated wrapper function parameter
would collide with it (F811). The explicit assignment below is both ruff-clean and
resolves to the correct fixture under the same name.
"""

import json
import shutil
from pathlib import Path

import pytest

from backend.tests.integration import conftest as _integration_conftest

api_integration_env = _integration_conftest.api_integration_env

RAW_FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "raw"
GENERATED_FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "generated"

# Page size for paging through the REST history endpoint, mirroring
# frontend/src/stores/message.js's fetchAllMessagePages() no-limit page size — the
# generated rest_history.json must be a single complete blob (has_more: false) since
# fixtureEquivalence.js/equivalence.test.js replay it as one static REST response, not
# a live paginated sequence.
_REST_PAGE_SIZE = 10000


def _discover_fixture_names() -> list[str]:
    if not RAW_FIXTURES_ROOT.exists():
        raise RuntimeError(
            f"Raw fixtures directory not found: {RAW_FIXTURES_ROOT}. This test requires "
            f"at least one recorded fixture (see backend/tests/fixtures/raw/) — it does "
            f"not skip when fixtures are absent."
        )
    names = sorted(
        p.name for p in RAW_FIXTURES_ROOT.iterdir()
        if p.is_dir() and (p / "raw_log.jsonl").exists()
    )
    if not names:
        raise RuntimeError(
            f"No raw fixtures with raw_log.jsonl found under {RAW_FIXTURES_ROOT}. This "
            f"test requires at least one recorded fixture — it does not skip when "
            f"fixtures are absent."
        )
    return names


FIXTURE_NAMES = _discover_fixture_names()


@pytest.fixture(scope="module", autouse=True)
def _wipe_generated_root():
    """Wipe and recreate generated/ once per module run, before any per-fixture replay.

    A fixture's subdirectory is only ever written after that fixture's own captured
    output has passed its assertions (see test body below) — so a mid-replay failure
    never leaves a partial-but-present generated/{name}/ that looks valid to
    fixtureEquivalence.js. Wiping here (not per-fixture) ensures stale data from a
    previous run/renamed fixture never lingers either.
    """
    if GENERATED_FIXTURES_ROOT.exists():
        shutil.rmtree(GENERATED_FIXTURES_ROOT)
    GENERATED_FIXTURES_ROOT.mkdir(parents=True)
    yield


async def _fetch_all_messages(client, session_id: str) -> dict:
    """Pages through GET /api/sessions/{id}/messages until has_more is false, and
    returns a single combined response shaped like a one-shot no-limit fetch."""
    all_messages: list[dict] = []
    offset = 0
    combined: dict = {}
    while True:
        resp = await client.get(
            f"/api/sessions/{session_id}/messages",
            params={"limit": _REST_PAGE_SIZE, "offset": offset},
        )
        assert resp.status_code == 200, f"GET messages failed: {resp.text}"
        body = resp.json()
        combined = body
        all_messages.extend(body.get("messages", []))
        if not body.get("has_more"):
            break
        offset += _REST_PAGE_SIZE

    combined = dict(combined)
    combined["messages"] = all_messages
    combined["has_more"] = False
    return combined


@pytest.mark.parametrize("fixture_name", FIXTURE_NAMES)
async def test_generate_fixture_from_real_pipeline(api_integration_env, fixture_name):
    client = api_integration_env["client"]
    create_test_project = api_integration_env["create_test_project"]
    create_test_session = api_integration_env["create_test_session"]
    webui = api_integration_env["webui"]

    project = await create_test_project(name=f"Equivalence replay: {fixture_name}")
    # The session's `name` is what SessionCoordinator forwards to the mock SDK factory
    # as `session_name` (see session_coordinator.py's set_sdk_factory call site) —
    # api_integration_env's _lenient_mock_factory resolves it as fixtures_dir/session_name,
    # and fixtures_dir is backend/tests/fixtures (the parent of raw/), so the session name
    # must include the "raw/" prefix to resolve to this fixture's actual directory.
    session = await create_test_session(project["project_id"], name=f"raw/{fixture_name}")
    session_id = session["session_id"]

    # (b) Start the session — raw-mode replay runs synchronously to completion inside
    # this call (MockClaudeSDK.start() awaits _start_raw_replay() before returning).
    resp = await client.post(f"/api/sessions/{session_id}/start")
    assert resp.status_code == 200, f"start session failed: {resp.text}"

    # (c) Assert replay actually happened. _process_sdk_message() has an outer
    # `except Exception: logger.exception(...)` that swallows per-message failures
    # without re-raising, so "the start call didn't throw" is not a reliable replay
    # signal — assert on the session's reached state plus stored message count instead.
    resp = await client.get(f"/api/sessions/{session_id}")
    assert resp.status_code == 200
    session_state = resp.json()["session"]["state"]
    assert session_state not in ("error", "created", "starting"), (
        f"Session {session_id} for fixture '{fixture_name}' did not reach a running "
        f"state after start: {session_state}"
    )

    raw_log_path = RAW_FIXTURES_ROOT / fixture_name / "raw_log.jsonl"
    fixture_sdk_message_count = 0
    for line in raw_log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if json.loads(line).get("kind") == "sdk_message":
            fixture_sdk_message_count += 1
    assert fixture_sdk_message_count > 0, (
        f"Fixture '{fixture_name}' has no sdk_message records to replay — a broken fixture, "
        f"not a valid empty-replay case."
    )

    # (e) Call the real REST endpoint. Also doubles as the "stored message count > 0"
    # signal for (c) — get_session_messages() is expensive (reconstructs tool-call
    # display state from every stored row), so this reuses its one REST round-trip's
    # total_count rather than issuing a second, separate call just to read that count.
    rest_history = await _fetch_all_messages(client, session_id)
    stored_count = rest_history.get("total_count", 0)
    assert stored_count > 0, (
        f"Replay of fixture '{fixture_name}' ({fixture_sdk_message_count} recorded "
        f"sdk_message records) produced zero stored messages — replay did not actually "
        f"run, even though start() reported success (its outer exception handler can "
        f"swallow per-message failures silently)."
    )
    assert rest_history["messages"], (
        f"REST history for fixture '{fixture_name}' came back empty despite "
        f"{stored_count} stored messages."
    )

    # (d) Read the real EventQueue this harness's BackendApp created for this session.
    queue = webui.session_queues.get(session_id)
    assert queue is not None, f"No EventQueue registered for session {session_id}"
    events, _next_cursor, _evicted = queue.events_since(0)
    assert len(events) > 0, (
        f"Live event stream for fixture '{fixture_name}' is empty despite "
        f"{fixture_sdk_message_count} recorded sdk_message records — an empty live "
        f"stream from a fixture with recorded SDK messages is a bug, not a valid result."
    )

    # (f) Only after (c)-(e)'s assertions pass: write the generated fixture directory.
    generated_dir = GENERATED_FIXTURES_ROOT / fixture_name
    generated_dir.mkdir(parents=True, exist_ok=True)

    raw_log_lines = [
        json.dumps({"kind": "queue_event", "event": event}, default=str)
        for event in events
    ]
    (generated_dir / "raw_log.jsonl").write_text(
        "\n".join(raw_log_lines) + "\n", encoding="utf-8"
    )
    (generated_dir / "rest_history.json").write_text(
        json.dumps(rest_history, indent=2, default=str), encoding="utf-8"
    )

    # (g) Read back what was just written and assert it round-trips.
    reread_raw_log = (generated_dir / "raw_log.jsonl").read_text(encoding="utf-8")
    reread_records = [
        json.loads(line) for line in reread_raw_log.splitlines() if line.strip()
    ]
    assert len(reread_records) == len(events), (
        f"raw_log.jsonl round-trip for fixture '{fixture_name}' lost records: wrote "
        f"{len(events)}, read back {len(reread_records)}."
    )
    assert all(r.get("kind") == "queue_event" for r in reread_records)

    reread_rest_history = json.loads(
        (generated_dir / "rest_history.json").read_text(encoding="utf-8")
    )
    assert reread_rest_history.get("messages"), (
        f"rest_history.json round-trip for fixture '{fixture_name}' came back empty."
    )
