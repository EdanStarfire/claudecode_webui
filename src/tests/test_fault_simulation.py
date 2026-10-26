"""Fault-simulation harness tests (issue #1999, US2/AC3/AC4).

Drives the real EventQueue/PollRelay code (src/tests/simulation/fault_harness.py)
through AC4's four named fault types plus the "none" baseline, injecting each
against a recorded fixture's queue_event stream as payload, and asserts
exactly-once delivery.

Payload comes from backend/tests/fixtures/raw/ — read directly as plain JSON
files (never imported as Python), since src/ is structurally forbidden from
importing backend/ (see src/tests/test_import_boundary.py).
"""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from src.tests.simulation.fault_harness import (
    _DESTRUCTIVE_FAULTS,
    FAULT_TYPES,
    BrowserClient,
    FakeBackend,
    FakeFrontend,
    resolve_delivered_seqs,
    run_fault_scenario,
    wait_until,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RAW_FIXTURES_ROOT = _REPO_ROOT / "backend" / "tests" / "fixtures" / "raw"


@pytest.fixture(autouse=True)
def _fast_poll_timing(monkeypatch):
    """Scoped-down determinism pattern (see PLAN_1999's Risks section, and
    src/tests/test_poll_relay.py's existing use of the same technique):
    PollRelay's own upstream long-poll timeout defaults to 30s
    (src/poll_relay.py's _POLL_TIMEOUT_SECONDS) — far longer than this test
    file's settle budget. Shrinking it (and the client-side margin above it,
    and the error backoff) makes a relay recovering from a stale/orphaned
    queue reference (e.g. after backend_restart) resolve in well under a
    second instead of up to 30s of real wall-clock time."""
    monkeypatch.setattr("src.poll_relay._POLL_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("src.poll_relay._POLL_CLIENT_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr("src.poll_relay._ERROR_BACKOFF_SECONDS", 0.02)


def _list_raw_fixture_names() -> list[str]:
    """Every raw-fixture directory with a raw_log.jsonl — discovered dynamically,
    not hardcoded. Raises (does not return an empty list) if none are found, so
    a misconfigured checkout fails this suite loudly rather than silently
    reporting zero tests (mirrors US1's AC1 fail-not-skip guard)."""
    if not _RAW_FIXTURES_ROOT.exists():
        raise RuntimeError(f"Raw fixtures directory not found: {_RAW_FIXTURES_ROOT}")
    names = sorted(
        p.name for p in _RAW_FIXTURES_ROOT.iterdir()
        if p.is_dir() and (p / "raw_log.jsonl").exists()
    )
    if not names:
        raise RuntimeError(f"No raw fixtures with raw_log.jsonl found under {_RAW_FIXTURES_ROOT}")
    return names


def _load_queue_events(name: str) -> list[dict]:
    raw_log_path = _RAW_FIXTURES_ROOT / name / "raw_log.jsonl"
    events = []
    for line in raw_log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("kind") == "queue_event":
            events.append(record["event"])
    if not events:
        raise RuntimeError(f"No queue_event records found in {raw_log_path}")
    return events


# One fixture is enough payload for the transport-layer fault matrix (unlike US1's
# equivalence harness, this isn't validating message-pipeline content — just that
# the queue/relay layer delivers whatever payload it's given exactly once under
# each fault). Uses the first discovered fixture so it still fails loudly (via
# _list_raw_fixture_names()) rather than silently skipping if none exist.
_FIXTURE_NAME = _list_raw_fixture_names()[0]
_EVENTS = _load_queue_events(_FIXTURE_NAME)
# Bounded slice: the fault matrix runs many scenarios: full-fixture replay (~3800
# events) per scenario would make the suite slow without adding coverage — the
# transport layer doesn't care about payload content, only volume/ordering, and
# US5 separately covers the 20,000+-event scale case.
_SAMPLE_EVENTS = _EVENTS[:200]

# Issue #2160 AC1: a frontend_restart now seeds an explicit stream_reset marker,
# which means the full (Backend-untouched) history gets replayed into the freshly
# -renumbered local queue afterward — a real browser handles this via a full
# reload-and-replace (out of scope for this transport-layer harness, see
# BrowserClient's own docstring), so the harness's flat received-log legitimately
# sees already-delivered content again. Exempted from the strict single-delivery
# check in test_fault_matrix_delivers_exactly_once below; the "every tail event
# eventually arrives" property that test also checks is unaffected and still
# enforced for this fault.
_DUPLICATE_ON_RESET_REPLAY_FAULTS = frozenset({"frontend_restart"})


@pytest.mark.asyncio
async def test_fixture_discovery_is_dynamic_and_fails_loudly(monkeypatch):
    """AC3's fail-not-skip guard, mirrored from US1: an empty/missing raw fixtures
    directory must raise, not silently produce zero scenarios."""
    monkeypatch.setattr(
        "src.tests.test_fault_simulation._RAW_FIXTURES_ROOT", _REPO_ROOT / "does-not-exist"
    )
    with pytest.raises(RuntimeError, match="not found"):
        _list_raw_fixture_names()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", FAULT_TYPES)
async def test_fault_matrix_delivers_exactly_once(fault):
    """T2 (AC3/AC4): the fault-interleaving matrix. For every named fault type
    (plus the "none" baseline): no event is ever delivered more than once, and
    the transport has recovered by the tail of the stream — every event
    appended once things are well past the fault point is eventually
    delivered. (A destructive fault may still legitimately drop events that
    were genuinely in flight at the exact moment it fired — see
    test_non_destructive_faults_lose_nothing for the stronger "nothing lost
    at all" property, which only holds for the non-destructive fault types.)
    """
    result = await run_fault_scenario(fault, _SAMPLE_EVENTS)
    client = result["client"]

    received = client.received
    deduped = client.received_deduped()
    if fault not in _DUPLICATE_ON_RESET_REPLAY_FAULTS:
        assert len(received) == len(deduped), (
            f"fault={fault}: {len(received) - len(deduped)} event(s) delivered more than once"
        )

    delivered_seqs = resolve_delivered_seqs(deduped, result["seq_identity"])
    missing_tail = set(result["tail_seqs"]) - delivered_seqs
    assert not missing_tail, (
        f"fault={fault}: {len(missing_tail)} tail event(s) never delivered — transport did not recover"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", sorted(set(FAULT_TYPES) - _DESTRUCTIVE_FAULTS))
async def test_non_destructive_faults_lose_nothing(fault):
    """AC4: frontend_restart and freeze (and the "none" baseline) must not lose
    any pre-fault history either — only backend_restart/eviction are allowed to
    (the same real-world tradeoff #1889 documents: a Backend restart or buffer
    eviction genuinely discards undelivered history; a Frontend restart or a
    frozen tab must not)."""
    result = await run_fault_scenario(fault, _SAMPLE_EVENTS)
    client = result["client"]

    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    missing = set(result["expected_seqs"]) - delivered_seqs
    assert not missing, f"fault={fault}: {len(missing)} event(s) lost despite being non-destructive"


@pytest.mark.asyncio
async def test_backend_restart_is_visible_as_a_reset_to_the_browser():
    """AC4: a Backend restart mid-poll produces a lower cursor than the client
    last saw — EventQueue.append()'s reset branch (shared/event_queue.py) — and
    the browser client observes at least one reset signal, distinct from a
    plain connection error."""
    result = await run_fault_scenario("backend_restart", _SAMPLE_EVENTS)
    assert result["client"].reset_count >= 1


@pytest.mark.asyncio
async def test_eviction_is_visible_to_the_browser():
    """AC4: forcing Backend's retained window down below what the client still
    needs must surface as an explicit evicted signal (not a silent gap)."""
    result = await run_fault_scenario("eviction", _SAMPLE_EVENTS, fault_at_fraction=0.1)
    assert result["client"].evicted_count >= 1 or result["client"].reset_count >= 1


@pytest.mark.asyncio
async def test_freeze_client_resumes_with_no_loss_and_no_duplicates():
    """AC4: a paused (frozen-tab) browser client simply stops polling — once
    resumed, it must catch up from its own last cursor with no gap and no
    redelivery, since nothing about the queue/relay itself changed while it
    was paused."""
    result = await run_fault_scenario("freeze", _SAMPLE_EVENTS)
    client = result["client"]
    assert len(client.received) == len(client.received_deduped())
    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    assert set(result["expected_seqs"]) <= delivered_seqs


@pytest.mark.slow
@pytest.mark.asyncio
async def test_scale_fixture_completes_in_bounded_time_with_no_event_loss():
    """US5 (edge case, not a numbered AC): a 20,000+-event synthetic fixture,
    generated at test run time (never committed — see
    backend/tests/fixtures/scale_fixture.py for the reusable generator
    counterpart), confirms bounded run time and no event loss through the
    fault harness. Performance and no-loss only, no content assertions, per
    the issue's own scoping.

    Builds its own scaled event list here (a plain cycle of the source
    fixture's events) rather than importing backend.tests.fixtures.
    scale_fixture — src/ is structurally forbidden from importing backend/
    (see src/tests/test_import_boundary.py). Unlike that module (which
    rewrites identity fields for consumers that dedup by content, e.g. the
    frontend's message_id-keyed store), this harness's own exactly-once
    tracking is purely cursor/seq-based (see BrowserClient.received_deduped()
    and run_fault_scenario()'s `seq_identity` out-of-band identity map) —
    verbatim repetition is sufficient here.
    """
    target_count = 20_000
    scaled_events = [
        _EVENTS[i % len(_EVENTS)] for i in range(target_count)
    ]

    start = time.monotonic()
    result = await run_fault_scenario("none", scaled_events, settle_timeout=60.0)
    elapsed = time.monotonic() - start

    client = result["client"]
    assert len(client.received) == len(client.received_deduped()), "duplicate delivery at scale"
    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    assert set(result["expected_seqs"]) <= delivered_seqs, "event loss at scale"

    # Bounded, not tight: this is a regression guard against the harness going
    # quadratic or otherwise pathological at scale, not a strict perf budget.
    assert elapsed < 45.0, f"scale scenario took {elapsed:.1f}s — expected well under 45s"


@pytest.mark.asyncio
async def test_backend_down_recovers_with_no_loss():
    """AC6: a Backend that stops answering polls entirely (connection refused)
    must not lose any events — once it comes back up, the relay catches up
    from exactly where it left off. First confirms go_down() genuinely
    produces a connection failure directly against FakeBackend (BrowserClient
    itself never observes this: FakeFrontend/PollRelay mediates and retries
    internally, catching httpx.RequestError in its own loop — the same
    shielding a real browser gets from polling.js never seeing a raw
    connection error either), then confirms the full scenario loses nothing.
    """
    probe_backend = FakeBackend()
    probe_backend.go_down()
    with pytest.raises(httpx.ConnectError):
        await probe_backend.request_json(
            "POST", "/api/poll", json={"timeout": 0, "sessions": {"probe-session": {"since": 0}}}
        )

    result = await run_fault_scenario("backend_down", _SAMPLE_EVENTS)
    client = result["client"]

    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    missing = set(result["expected_seqs"]) - delivered_seqs
    assert not missing, f"backend_down: {len(missing)} event(s) lost despite recovery"
    assert len(client.received) == len(client.received_deduped())


@pytest.mark.asyncio
async def test_slow_ready_recovers_with_no_loss_and_no_spurious_signal():
    """AC6: a Backend that's up but slow to answer (e.g. mid-startup) must not
    lose any events and must not raise a spurious reset/evicted signal — only
    timing is affected, nothing about queue state changed."""
    result = await run_fault_scenario("slow_ready", _SAMPLE_EVENTS)
    client = result["client"]

    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    missing = set(result["expected_seqs"]) - delivered_seqs
    assert not missing, f"slow_ready: {len(missing)} event(s) lost despite recovery"
    assert len(client.received) == len(client.received_deduped())
    assert client.reset_count == 0
    assert client.evicted_count == 0


@pytest.mark.asyncio
async def test_eviction_during_backend_restart_surfaces_both_signals():
    """AC6: the combined fault (a Backend restart immediately followed by an
    eviction, no yield in between) exercises both hazards compounding — fresh
    cursor space AND a tiny retained window. Mirrors
    test_backend_restart_is_visible_as_a_reset_to_the_browser/
    test_eviction_is_visible_to_the_browser's style: at least one of the two
    signals must surface, never a silent gap.

    fault_at_fraction=0.15 (not 0.1, unlike the plain "eviction" test):
    0.1 puts the fault at exactly 20 pre-fault events — the same value as
    _EVICTION_BURST_LEN, a coincidental round-number match that (like the
    collision item 4's dedicated test deliberately engineers for
    backend_restart alone) can land the post-restart burst exactly on the
    relay's stale cursor and mask the signal despite genuine data loss.
    0.15 avoids that coincidence.
    """
    result = await run_fault_scenario(
        "eviction_during_backend_restart", _SAMPLE_EVENTS, fault_at_fraction=0.15
    )
    client = result["client"]
    assert client.reset_count >= 1 or client.evicted_count >= 1


@pytest.mark.asyncio
async def test_frontend_restart_is_visible_as_a_reset_to_the_browser():
    """Issue #2160 AC1: a Frontend-tier restart must now be a deterministic,
    explicit `stream_reset` signal — mirrors
    test_backend_restart_is_visible_as_a_reset_to_the_browser. The client's own
    cursor survives frontend.restart() (a real browser's Pinia state is
    unaffected by a server-side process restart), so its next poll arrives with
    since > 0 against the freshly rebuilt PollRelay's brand-new local queue —
    exactly the condition touch_session()/start_ui_relay() seed a marker for."""
    result = await run_fault_scenario("frontend_restart", _SAMPLE_EVENTS)
    assert result["client"].reset_count >= 1


@pytest.mark.asyncio
async def test_frontend_restart_reseeds_from_backends_real_cursor_space():
    """US2's Technical Approach: FakeFrontend.restart() discards PollRelay/local
    EventQueue and rebuilds against the SAME, unaffected FakeBackend. Before issue
    #2160 AC1, this asserted "no loss and no duplication" — that held only by a
    timing coincidence (the freshly-renumbered local queue's replay boundary
    happened to line up with the client's pre-restart cursor count). AC1 makes
    the restart an explicit, deterministic `stream_reset` signal instead, which
    is the real property worth asserting here; a real browser handles the signal
    via a full reload-and-replace (out of scope for this transport-layer harness
    — see BrowserClient's own docstring), so re-encountering Backend's untouched
    history "as new" afterward is expected, not a regression (see
    test_frontend_restart_is_visible_as_a_reset_to_the_browser, which asserts the
    identical property under AC1's own name)."""
    result = await run_fault_scenario("frontend_restart", _SAMPLE_EVENTS)
    assert result["client"].reset_count >= 1


@pytest.mark.asyncio
async def test_cursor_collision_silently_drops_colliding_post_restart_events():
    """AC5/AC6 item 4 (epic #1990 stage 5a — capstone): deliberately engineers the
    exact "round event counts" cursor collision that `_inject_fault()`'s
    `backend_restart` branch's sleep workaround exists to avoid — bypassing
    `run_fault_scenario()`'s general helper (and that sleep) entirely,
    constructing the harness directly.

    With NO settle delay after `backend.restart()`, a post-restart burst sized
    to exactly match the client's already-observed pre-restart cursor used to
    recreate a collision under the OLD cursor-magnitude-based design: the
    relay's stale `since` equaled the fresh queue's current cursor once the
    burst completed, so `events_since()` read "already caught up" rather than
    "these are N entirely different events from a new epoch," silently
    dropping every colliding event with no reset/evicted signal.

    Stage 5a's epoch-based `stream_reset` detection (`src/poll_relay.py`)
    eliminates this hazard by construction: the relay no longer needs cursor
    magnitude to detect a restart at all, so a collision in Backend's cursor
    space is irrelevant to it. This test previously carried an `xfail(strict=
    True)` marker tracking exactly this fix; it now passes UNMODIFIED (the
    assertions below are unchanged from before stage 5a), confirming the fix
    is real and not a test change.
    """
    session_id = "cursor-collision-session"
    backend = FakeBackend()
    frontend = FakeFrontend(backend)
    client = BrowserClient(frontend, session_id=session_id)

    stop_event = asyncio.Event()
    poll_task = asyncio.create_task(client.run_until(stop_event, poll_timeout=0.05))

    pre_restart_count = 10
    for i in range(pre_restart_count):
        backend.queue_for(session_id).append({"phase": "pre", "i": i})
        await asyncio.sleep(0)  # let the relay observe each pre-restart append promptly

    await wait_until(lambda: len(client.received) >= pre_restart_count, timeout=2.0, interval=0.01)
    assert len(client.received) == pre_restart_count, "setup failed: client never caught up pre-restart"

    # The collision itself: restart with no settle delay, then a same-sized
    # burst with no yield in between — the fresh queue's cursor lands on
    # exactly the value the relay's stale `since` already holds.
    backend.restart()
    for i in range(pre_restart_count):
        backend.queue_for(session_id).append({"phase": "post", "i": i})

    # Fixed wait, not a predicate loop: this is the collision itself under test — there
    # is no "done" condition to poll for, since the whole point is that nothing should
    # (but today, nothing does) visibly happen for these colliding events.
    await asyncio.sleep(2.0)

    stop_event.set()
    await poll_task
    await frontend.stop()

    post_restart_received = [e for _, e in client.received if e.data.get("phase") == "post"]
    assert (
        len(post_restart_received) == pre_restart_count
        or client.reset_count >= 1
        or client.evicted_count >= 1
    )


# --- Issue #2305: session age-out / re-entry must not replay Backend's queue -------

_AGE_OUT_IDLE_SECONDS = 0.3


async def _run_age_out_scenario(
    before_events: list[dict], while_aged_out: list[dict], gap_fault=None
) -> dict:
    """K events delivered, client goes quiet past the (shrunk) idle timeout so the
    relay drops the session from its subscription set, N more events land on
    Backend, client resumes. Returns the raw (non-deduped) post-resume deliveries."""
    session_id = "age-out-session"
    backend = FakeBackend()
    frontend = FakeFrontend(backend)
    client = BrowserClient(frontend, session_id=session_id)
    try:
        for i, event in enumerate(before_events):
            backend.queue_for(session_id).append({**event, "__fault_harness_seq__": i})
        # Initial subscription + catch-up on the K events.
        for _ in range(20):
            await client.poll_once(timeout=0.05)
            if len(client.received) >= len(before_events):
                break
        assert len(client.received) == len(before_events)

        # Client frozen: no touch_session() calls, so the session ages out.
        await asyncio.sleep(_AGE_OUT_IDLE_SECONDS * 2 + 0.3)
        assert session_id not in frontend.poll_relay._session_last_activity

        if gap_fault is not None:
            gap_fault(backend, session_id)

        for j, event in enumerate(while_aged_out):
            backend.queue_for(session_id).append(
                {**event, "__fault_harness_seq__": len(before_events) + j}
            )

        received_before_resume = len(client.received)
        local_cursor_before = client.cursor
        # Resume and drain until quiescent (several consecutive empty polls), so a relay
        # that wrongly re-appends the whole queue is fully observed rather than cut off.
        idle_polls = 0
        for _ in range(60):
            before = len(client.received)
            await client.poll_once(timeout=0.1)
            idle_polls = idle_polls + 1 if len(client.received) == before else 0
            if idle_polls >= 4:
                break
        post = client.received[received_before_resume:]
        return {
            "post_resume": post,
            "post_resume_count": len(post),
            "local_cursor_delta": client.cursor - local_cursor_before,
            "duplicates": len(post) - len({identity for identity, _ in post}),
            "pre_seq_redelivered": sum(
                1 for identity, _ in post if identity < len(before_events)
            ),
            "reset_markers": client.reset_count,
            "backend": backend,
            "frontend": frontend,
            "client": client,
        }
    finally:
        await frontend.stop()


@pytest.fixture
def _short_idle(monkeypatch):
    monkeypatch.setattr("src.poll_relay._SESSION_IDLE_TIMEOUT_SECONDS", _AGE_OUT_IDLE_SECONDS)


@pytest.mark.asyncio
async def test_issue_2305_age_out_reentry_delivers_only_new_events(_short_idle):
    """AC4: after age-out + re-entry the browser receives exactly the N events produced
    while aged out — not K+N (the whole Backend queue re-appended from cursor 0)."""
    k, n = 30, 7
    # Synthetic payload: the recorded fixture slice is only ~17 events, fewer than K+N.
    payload = [{**_SAMPLE_EVENTS[i % len(_SAMPLE_EVENTS)]} for i in range(k + n)]
    result = await _run_age_out_scenario(payload[:k], payload[k:])
    print(
        f"AGE_OUT_RESULT delivered={result['post_resume_count']} "
        f"duplicates={result['pre_seq_redelivered']} cursor_delta={result['local_cursor_delta']}"
    )
    assert result["post_resume_count"] == n
    assert result["pre_seq_redelivered"] == 0
    assert result["duplicates"] == 0
    assert result["local_cursor_delta"] == n


def _synthetic_payload(total: int) -> list[dict]:
    return [{**_SAMPLE_EVENTS[i % len(_SAMPLE_EVENTS)]} for i in range(total)]


@pytest.mark.asyncio
async def test_issue_2305_age_out_then_session_reset_delivers_no_pre_reset_events(_short_idle):
    """AC3/AC4: a session reset during the gap -> one explicit stream_reset, and none of
    the pre-reset records (seq < K) are ever redelivered."""
    k, n = 30, 5
    payload = _synthetic_payload(k + n)
    result = await _run_age_out_scenario(
        payload[:k], payload[k:], gap_fault=lambda backend, sid: backend.reset_session(sid)
    )
    assert result["reset_markers"] == 1
    assert result["pre_seq_redelivered"] == 0
    delivered = [i for i, e in result["post_resume"] if e.type != "stream_reset"]
    assert delivered == list(range(k, k + n))


@pytest.mark.asyncio
async def test_issue_2305_age_out_then_backend_restart_emits_exactly_one_reset(_short_idle):
    k, n = 30, 5
    payload = _synthetic_payload(k + n)
    result = await _run_age_out_scenario(
        payload[:k], payload[k:], gap_fault=lambda backend, sid: backend.restart()
    )
    assert result["reset_markers"] == 1
    assert result["pre_seq_redelivered"] == 0
    assert result["duplicates"] == 0


@pytest.mark.asyncio
async def test_issue_2305_age_out_with_two_browsers_at_different_local_cursors(_short_idle):
    """Browsers share one relay cursor but keep independent local cursors: each receives
    exactly the delta it lacks, nothing twice."""
    session_id = "two-browser-session"
    backend = FakeBackend()
    frontend = FakeFrontend(backend)
    a = BrowserClient(frontend, session_id=session_id)
    b = BrowserClient(frontend, session_id=session_id)
    payload = _synthetic_payload(40)
    try:
        for i in range(20):
            backend.queue_for(session_id).append({**payload[i], "__fault_harness_seq__": i})
        for _ in range(20):
            await a.poll_once(timeout=0.05)
            if len(a.received) >= 20:
                break
        # Browser B arrives later, behind: it has only seen the first 8 local events.
        b.cursor = 8
        b_seen = len(b.received)

        await asyncio.sleep(_AGE_OUT_IDLE_SECONDS * 2 + 0.3)
        assert session_id not in frontend.poll_relay._session_last_activity
        for i in range(20, 25):
            backend.queue_for(session_id).append({**payload[i], "__fault_harness_seq__": i})

        a_before = len(a.received)
        for _ in range(15):
            await a.poll_once(timeout=0.1)
            await b.poll_once(timeout=0.1)
        a_new = [identity for identity, _ in a.received[a_before:]]
        b_new = [identity for identity, _ in b.received[b_seen:]]
        assert a_new == list(range(20, 25))
        assert b_new == list(range(8, 25))  # exactly the delta B lacks, once
    finally:
        await frontend.stop()
