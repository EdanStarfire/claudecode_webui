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

from backend.fixture_export import REQUIRED_MARKERS, _check_markers
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

# Issue #2055: fixture_export.REQUIRED_MARKERS split into what a real-pipeline raw
# replay can currently prove it exercised (sdk_message-kind records, which
# RawFixtureReplay._parse() already handles) vs. what it structurally cannot, given
# RawFixtureReplay's current scope and what SessionRecorder actually captures for
# the other record kinds. See #2055 for the full per-kind analysis of why each of
# these isn't a simple "add a case to RawFixtureReplay" fix.
_SDK_MESSAGE_BACKED_MARKERS = {
    "streaming deltas",
    "AskUserQuestion",
    "subagent task with progress",
    "compaction",
}

# Reasons (not consumed by code — documentation for why each marker is unprovable today):
# - "tool call with permission prompt": RawFixtureReplay skips permission_invocation records
# - "denied permission": RawFixtureReplay skips permission_response records
# - "interrupt mid-tool": RawFixtureReplay skips interrupt records; the real
#   ClaudeSDK.interrupt_session() is also a no-op without a live SDK client, so there is no
#   real downstream effect to replay even if the record kind were handled
# - "session restart": RawFixtureReplay skips lifecycle records
# - "inter-minion comm": RawFixtureReplay skips queue_event records; the raw log only
#   captures the EventQueue's processed output, not the input that produced it, so there is
#   nothing to replay even if the record kind were handled
_NOT_YET_EXERCISED_BY_RAW_REPLAY = {
    "tool call with permission prompt",
    "denied permission",
    "interrupt mid-tool",
    "session restart",
    "inter-minion comm",
}

_TASK_SUBTYPES = {"task_started", "task_progress", "task_notification"}


def _check_markers_from_queue_events(events: list[dict]) -> dict[str, bool]:
    """Issue #2055: coverage-inventory pass over the LIVE events a real-pipeline
    replay actually produced (test_generate_fixture_from_real_pipeline's own
    `events` list), restricted to _SDK_MESSAGE_BACKED_MARKERS — the only markers
    a real replay can currently produce any evidence for. Mirrors
    fixture_export._check_markers()'s detection logic, translated from raw
    SessionRecorder-shaped records to the processed poll-queue envelope shape
    (`{"type": ..., "data": websocket_data, ...}`) these events actually have.
    """
    found = dict.fromkeys(_SDK_MESSAGE_BACKED_MARKERS, False)

    for event in events:
        if event.get("type") == "assistant_delta":
            found["streaming deltas"] = True
            continue

        if event.get("type") != "message":
            continue
        data = event.get("data") or {}
        metadata = data.get("metadata") or {}

        if data.get("type") == "assistant":
            tool_uses = metadata.get("tool_uses") or []
            if any(tu.get("name") == "AskUserQuestion" for tu in tool_uses):
                found["AskUserQuestion"] = True

        elif data.get("type") == "system":
            subtype = metadata.get("subtype")
            if subtype in _TASK_SUBTYPES:
                found["subagent task with progress"] = True
            elif subtype == "compact_boundary":
                found["compaction"] = True

    return found


def _static_markers_provable_by_live_replay(
    static_raw_records: list[dict], check_markers_result: dict[str, bool]
) -> dict[str, bool]:
    """Issue #2055 code review: `fixture_export._check_markers()` answers "did ANY
    record kind claim this marker," but two of the four `_SDK_MESSAGE_BACKED_MARKERS`
    have a narrower real-pipeline path than that blended answer accounts for:

    - "streaming deltas": `_check_markers()` flags any `StreamEvent` sdk_message record
      regardless of `parent_tool_use_id`, but the live pipeline
      (`backend/web_server.py`'s message callback) drops any `assistant_delta` whose
      `parent_tool_use_id` is not None — subagent deltas are out of scope for v1 and
      never reach the live event stream `_check_markers_from_queue_events` reads.
    - "AskUserQuestion": `_check_markers()` also flags this from
      `permission_invocation`/`permission_response` records — kinds `RawFixtureReplay`
      structurally skips (see `_NOT_YET_EXERCISED_BY_RAW_REPLAY` above) — so a fixture
      claiming it only via those kinds has nothing for live replay to reproduce.

    Recomputes both from sdk_message records alone, under the same constraints the live
    pipeline applies, so the per-marker comparison below never flags either as a false
    "genuine regression."
    """
    markers = dict(check_markers_result)
    markers["streaming deltas"] = any(
        record.get("kind") == "sdk_message"
        and record.get("_type") == "StreamEvent"
        and (record.get("data") or {}).get("parent_tool_use_id") is None
        for record in static_raw_records
    )
    markers["AskUserQuestion"] = any(
        record.get("kind") == "sdk_message"
        and record.get("_type") == "AssistantMessage"
        and any(
            isinstance(block, dict) and block.get("name") == "AskUserQuestion"
            for block in (record.get("data") or {}).get("content") or []
        )
        for record in static_raw_records
    )
    return markers


def test_marker_classification_matches_required_markers():
    """Drift guard: fixture_export.REQUIRED_MARKERS changed without updating this
    file's classification of which markers real-pipeline raw replay can currently
    prove (see #2055). Kept inside a test function (not a module-level assert) so a
    drift shows up as one clean, named test failure rather than a collection error
    for the whole file — mirrors test_scenario_driver_dry_run.py's
    test_required_markers_all_covered_by_scenarios."""
    assert _SDK_MESSAGE_BACKED_MARKERS | _NOT_YET_EXERCISED_BY_RAW_REPLAY == set(
        REQUIRED_MARKERS
    ), (
        "fixture_export.REQUIRED_MARKERS changed without updating this file's "
        "classification of which markers real-pipeline raw replay can currently prove "
        "(see #2055)"
    )


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


def _final_tool_call_states(records: list[dict]) -> dict[str, dict]:
    """Issue #2109 AC12: reduce a chronological sequence of `tool_call`-typed
    records down to each `tool_use_id`'s final observed state — last-write-wins.
    `get_session_messages()` returns stored tool_call records unchanged (AC3: no
    read-time synthesis), and the live event stream is just those same records as
    they're appended, so both the live and REST/reload sources should reduce to
    identical final state for every tool call, independent of how many restarts
    the underlying recording actually contains."""
    final: dict[str, dict] = {}
    for record in records:
        if record.get("type") != "tool_call":
            continue
        tool_use_id = record.get("tool_use_id")
        if not tool_use_id:
            continue
        final[tool_use_id] = {
            "name": record.get("name"),
            "status": record.get("status"),
        }
    return final


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
    static_raw_records = []
    fixture_sdk_message_count = 0
    for line in raw_log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        static_raw_records.append(record)
        if record.get("kind") == "sdk_message":
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

    # Issue #2055: the fixture's own static raw_log.jsonl may claim coverage for an
    # sdk_message-backed marker (per fixture_export._check_markers()) — if it does,
    # the live replay above should have reproduced it too. Only markers the fixture
    # actually claims are checked; a marker the fixture never claimed has nothing to
    # verify (see _NOT_YET_EXERCISED_BY_RAW_REPLAY for the markers no real replay can
    # currently prove either way).
    static_markers = _static_markers_provable_by_live_replay(
        static_raw_records, _check_markers(static_raw_records)
    )
    live_markers = _check_markers_from_queue_events(events)
    for marker in _SDK_MESSAGE_BACKED_MARKERS:
        if not static_markers.get(marker):
            continue  # fixture never claimed this marker; nothing to verify
        assert live_markers.get(marker), (
            f"Fixture '{fixture_name}' raw_log.jsonl claims coverage for marker "
            f"{marker!r} (sdk_message-backed — real-pipeline replay should reproduce "
            f"it), but the live replay's event stream never exercised it. This is a "
            f"genuine regression, not the known #2055 gap (which only covers "
            f"{sorted(_NOT_YET_EXERCISED_BY_RAW_REPLAY)})."
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

    # Issue #2109 (AC11): 2026-09-23-primary is the one committed raw fixture whose
    # messages.jsonl/rest_history.json are still exactly as non-canonical as stage 3
    # left them (confirmed via `git log 95b4fc45..041c5280` — no stage since original
    # canonicalization has touched it). Promote this replay's real, canonically-written
    # messages.jsonl (and the rest_history.json already captured above) into the
    # committed fixture directory, overwriting the stale non-canonical content.
    if fixture_name == "2026-09-23-primary":
        storage = api_integration_env["coordinator"]._storage_managers[session_id]
        live_messages_path = storage.messages_file
        assert live_messages_path.exists(), (
            f"No live messages.jsonl found for fixture '{fixture_name}' at "
            f"{live_messages_path} — cannot promote into the committed fixture."
        )
        committed_dir = RAW_FIXTURES_ROOT / fixture_name
        shutil.copyfile(live_messages_path, committed_dir / "messages.jsonl")
        (committed_dir / "rest_history.json").write_text(
            json.dumps(rest_history, indent=2, default=str), encoding="utf-8"
        )

        # Issue #2109 (AC12): live-streamed and REST-reloaded tool_call records must
        # converge to the same final per-tool_use_id state. The issue text says this
        # recording contains "two restarts"; counted directly it actually has 3
        # post-initial client_launched markers (see plan) — asserted generically here
        # (final-state equality) rather than against a specific restart count, since
        # that discrepancy is unresolved and not this stage's to guess at.
        live_tool_call_records = [
            event["data"] for event in events
            if event.get("type") == "tool_call" and isinstance(event.get("data"), dict)
        ]
        live_states = _final_tool_call_states(live_tool_call_records)
        reload_states = _final_tool_call_states(rest_history["messages"])
        assert live_states, (
            f"Live event stream for fixture '{fixture_name}' produced zero tool_call "
            f"records — nothing to converge."
        )
        assert live_states == reload_states, (
            f"Live vs reload tool-call state diverged for fixture '{fixture_name}': "
            f"live={live_states}, reload={reload_states}"
        )
